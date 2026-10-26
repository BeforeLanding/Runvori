"""Continuous consumption stops at the requested or recovered boundary."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.application.command_mailbox import HostCommandMailbox
from runvori.codex.owned_event_loop import (
    OwnedEventLoopError, run_owned_event_loop,
)
from runvori.codex.owned_approval_queue import (
    OwnedApprovalQueue, OwnedApprovalQueueError, dispatch_owned_approval_once,
)
from runvori.codex.owned_event_pump import pump_owned_event_once
from runvori.codex.stdio import TransportError
from runvori.codex.upstream_lifecycle import launch_owned_upstream
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


@unittest.skipUnless(os.name == "nt", "Windows owned upstream integration")
class OwnedEventLoopTests(unittest.TestCase):
    def test_committed_handoff_notifies_mailbox_readers(self):
        class CountingMailbox(HostCommandMailbox):
            def __init__(self):
                super().__init__()
                self.notifications = 0

            def notify_events(self):
                self.notifications += 1
                super().notify_events()

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                _, owner = self._start(startup, confirmed=True)
                owner.client.request("turn/start", {})
                mailbox = CountingMailbox()
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.05,
                    max_handoffs=1, commands=mailbox)
                self.assertEqual(result["handoffs"], 1)
                self.assertGreaterEqual(mailbox.notifications, 1)
                self.assertFalse(mailbox._closed.is_set())

    def _start(self, startup, *, confirmed=False, mode="event_handoff"):
        epoch = startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run_request(
            "session", "run", actor_id="local", request_id="start",
            expected_session_version=0, input_text="hello",
            recorded_at="2026-10-25T00:00:01Z")
        if confirmed:
            startup.events.claim_run_dispatch(
                "local", "start", process_epoch=epoch,
                recorded_at="2026-10-25T00:00:02Z")
            startup.events.confirm_run_started(
                "local", "start", process_epoch=epoch,
                thread_id="thread_demo", turn_id="turn_demo",
                recorded_at="2026-10-25T00:00:03Z")
        owner = launch_owned_upstream(
            startup, epoch=epoch,
            command=[sys.executable, str(PEER), mode],
            cwd=ROOT, env=dict(os.environ))
        return epoch, owner

    def test_consumes_ordered_handoffs_and_stops_at_limit(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, confirmed=True)
                owner.client.request("turn/start", {})
                first = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.05,
                    max_handoffs=2)
                self.assertEqual(first["reason"], "handoff_limit")
                self.assertEqual(first["counts"]["approval_request"], 1)
                self.assertEqual(first["counts"]["turn_terminal"], 1)
                second = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.05,
                    max_handoffs=1)
                self.assertEqual(second["counts"]["request_closed"], 1)
                self.assertEqual(startup.approvals.current_epoch(), epoch)

    def test_empty_wait_stops_without_changing_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, mode="thread_bootstrap_good")
                stop = threading.Event()
                stopper = threading.Timer(0.12, stop.set)
                stopper.start()
                result = run_owned_event_loop(
                    owner, stop=stop, poll_timeout=0.05)
                stopper.join(timeout=2)
                self.assertEqual(result["reason"], "stopped")
                self.assertEqual(result["handoffs"], 0)
                self.assertEqual(startup.approvals.current_epoch(), epoch)

    def test_upstream_exit_recovers_and_ends_loop(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, mode="exit_after_initialized")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["cause"], "transport_fault")
                self.assertEqual(result["epoch"], epoch + 1)

    def test_handoff_failure_recovers_and_does_not_consume_next_event(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, confirmed=True)
                startup.events.connection.execute(
                    """CREATE TRIGGER block_handoff BEFORE INSERT ON events
                       WHEN NEW.event_json LIKE '%approval.requested%'
                       BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                owner.client.request("turn/start", {})
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["cause"], "handoff_fault")
                self.assertEqual(result["handoffs"], 0)
                self.assertEqual(result["epoch"], epoch + 1)

    def test_queued_decision_is_dispatched_once_and_resolved(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, confirmed=True,
                                           mode="approval")
                owner.client.request("turn/start", {})
                observed = pump_owned_event_once(
                    owner, timeout=1, recorded_at="2026-10-25T00:00:04Z")
                approval_id = observed["record"]["approval_id"]
                startup.record_approval_decision(
                    approval_id, actor_id="device", request_id="decision",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:05Z")
                approvals = OwnedApprovalQueue(capacity=2)
                approvals.submit("device", "decision")
                approvals.submit("device", "decision")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    max_handoffs=2, approvals=approvals)
                self.assertEqual(result["counts"]["approval_dispatched"], 1)
                self.assertEqual(result["counts"]["approval_skipped"], 1)
                self.assertEqual(result["counts"]["request_closed"], 1)
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision")["outcome"], "request_closed")
                self.assertEqual(startup.approvals.current_epoch(), epoch)

    def test_failed_approval_write_recovers_without_resend(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, confirmed=True,
                                           mode="approval")
                owner.client.request("turn/start", {})
                observed = pump_owned_event_once(
                    owner, timeout=1, recorded_at="2026-10-25T00:00:04Z")
                startup.record_approval_decision(
                    observed["record"]["approval_id"], actor_id="device",
                    request_id="decision", expected_version=1,
                    decision="deny", recorded_at="2026-10-25T00:00:05Z")
                calls = []

                def fail(request_id, decision):
                    calls.append((request_id, decision))
                    raise TransportError("upstream_write_timeout")

                owner.client.respond_approval = fail
                approvals = OwnedApprovalQueue()
                approvals.submit("device", "decision")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    approvals=approvals)
                self.assertEqual(calls, [("approval_demo", "decline")])
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["cause"], "approval_dispatch_fault")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision")["status"], "unknown")

    def test_approval_queue_is_bounded_and_validates_identifiers(self):
        approvals = OwnedApprovalQueue(capacity=1)
        approvals.submit("device", "one")
        with self.assertRaisesRegex(OwnedApprovalQueueError,
                                    "approval_queue_full"):
            approvals.submit("device", "two")
        self.assertEqual(approvals.take_nowait(), ("device", "one"))
        self.assertIsNone(approvals.take_nowait())
        with self.assertRaisesRegex(OwnedApprovalQueueError,
                                    "invalid_approval_queue_item"):
            approvals.submit("", "three")

    def test_terminal_before_queued_dispatch_skips_without_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._start(startup, confirmed=True)
                owner.client.request("turn/start", {})
                observed = pump_owned_event_once(
                    owner, timeout=1, recorded_at="2026-10-25T00:00:04Z")
                startup.record_approval_decision(
                    observed["record"]["approval_id"], actor_id="device",
                    request_id="decision", expected_version=1,
                    decision="deny", recorded_at="2026-10-25T00:00:05Z")
                pump_owned_event_once(
                    owner, timeout=1, recorded_at="2026-10-25T00:00:06Z")
                result = dispatch_owned_approval_once(
                    owner, actor_id="device", request_id="decision",
                    recorded_at="2026-10-25T00:00:07Z")
                self.assertEqual(result["kind"], "approval_skipped")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertFalse(owner.client._closed)

    def test_invalid_parameters_do_not_read(self):
        with self.assertRaisesRegex(OwnedEventLoopError,
                                    "invalid_event_loop_request"):
            run_owned_event_loop(None, stop=threading.Event())
        with self.assertRaisesRegex(OwnedEventLoopError,
                                    "invalid_event_loop_request"):
            run_owned_event_loop(None, stop=threading.Event(),
                                 poll_timeout=float("nan"))


if __name__ == "__main__":
    unittest.main()
