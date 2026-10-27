"""Interrupt RPC is claimed once and settled by a separate turn terminal."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.owned_event_loop import run_owned_event_loop
from runvori.codex.owned_interrupt_queue import (
    OwnedInterruptQueue, OwnedInterruptQueueError,
    dispatch_owned_interrupt_once,
)
from runvori.codex.interrupt_dispatch import send_interrupt_once
from runvori.codex.owned_event_pump import pump_owned_event_once
from runvori.codex.upstream_lifecycle import launch_owned_upstream
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


@unittest.skipUnless(os.name == "nt", "Windows owned upstream integration")
class OwnedInterruptTests(unittest.TestCase):
    @staticmethod
    def _ready(startup, mode):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.bind_session_thread("session", "thread_demo")
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="start",
            expected_session_version=0, input_text="go",
            recorded_at="2026-10-25T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "start", process_epoch=epoch,
            recorded_at="2026-10-25T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "start", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-25T00:00:03Z")
        startup.events.reserve_interrupt_request(
            "run", actor_id="device", request_id="stop",
            expected_version=2, recorded_at="2026-10-25T00:00:04Z")
        owner = launch_owned_upstream(
            startup, epoch=epoch,
            command=[sys.executable, str(PEER), mode],
            cwd=ROOT, env=dict(os.environ))
        return epoch, owner

    def test_queue_is_bounded_and_contains_only_operation_keys(self):
        queue = OwnedInterruptQueue(capacity=1)
        queue.submit("device", "stop")
        with self.assertRaisesRegex(OwnedInterruptQueueError,
                                    "interrupt_queue_full"):
            queue.submit("device", "other")
        self.assertEqual(queue.take_nowait(), ("device", "stop"))
        self.assertIsNone(queue.take_nowait())
        with self.assertRaisesRegex(OwnedInterruptQueueError,
                                    "invalid_interrupt_queue_item"):
            queue.submit("", "stop")

    def test_ack_waits_for_terminal_then_reports_interrupted(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._ready(startup, "interrupt_ack_then_terminal")
                queue = OwnedInterruptQueue()
                queue.submit("device", "stop")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    max_handoffs=1, interrupts=queue)
                self.assertEqual(result["counts"]["interrupt_dispatched"], 1)
                self.assertEqual(result["counts"]["turn_terminal"], 1)
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["outcome"], "run_interrupted")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                skipped = dispatch_owned_interrupt_once(
                    owner, actor_id="device", request_id="stop",
                    recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(skipped["kind"], "interrupt_skipped")

    def test_rpc_error_keeps_connection_for_completed_terminal(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._ready(startup, "recoverable_rpc_error")
                queue = OwnedInterruptQueue()
                queue.submit("device", "stop")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    max_handoffs=1, interrupts=queue)
                self.assertEqual(result["counts"]["interrupt_uncertain"], 1)
                self.assertEqual(result["counts"]["turn_terminal"], 1)
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["outcome"], "run_completed")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertFalse(owner.client._closed)

    def test_lost_rpc_reply_isolates_without_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._ready(startup, "interrupt_lost_reply")
                queue = OwnedInterruptQueue()
                queue.submit("device", "stop")
                queue.submit("device", "stop")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    interrupts=queue)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["cause"], "interrupt_dispatch_fault")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "unknown")
                self.assertIsNotNone(queue.take_nowait())

    def test_claim_failure_prevents_rpc_and_recovers(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._ready(startup, "interrupt_ack_then_terminal")
                startup.events.connection.execute(
                    """CREATE TRIGGER block_claim BEFORE INSERT ON events
                       WHEN NEW.event_json LIKE '%\"status\":\"dispatching\"%'
                       BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                queue = OwnedInterruptQueue()
                queue.submit("device", "stop")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    interrupts=queue)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["outcome"], "not_dispatched")

    def test_rpc_ack_alone_does_not_confirm_interrupt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._ready(startup, "interrupt_ack_then_terminal")
                sent = send_interrupt_once(
                    owner.client, startup, actor_id="device", request_id="stop",
                    epoch=epoch, recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(sent["status"], "dispatching")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "dispatching")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "interrupting")
                self.assertEqual(pump_owned_event_once(
                    owner, timeout=1,
                    recorded_at="2026-10-25T00:00:06Z")["kind"],
                    "turn_terminal")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["outcome"], "run_interrupted")

    def test_mismatched_session_thread_never_sends_interrupt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._ready(startup, "interrupt_lost_reply")
                startup.events.connection.execute(
                    "UPDATE session_threads SET thread_id='other_thread' "
                    "WHERE session_id='session'")
                result = dispatch_owned_interrupt_once(
                    owner, actor_id="device", request_id="stop",
                    recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(result["kind"], "interrupt_skipped")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "accepted")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertFalse(owner.client._closed)


if __name__ == "__main__":
    unittest.main()
