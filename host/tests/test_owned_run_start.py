"""Accepted run starts share the owned stdio event loop."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.owned_event_loop import run_owned_event_loop
from runvori.codex.owned_run_start import (
    OwnedRunStartError, OwnedRunStartQueue, start_owned_run_once,
)
from runvori.codex.upstream_lifecycle import launch_owned_upstream
from runvori.infrastructure.event_journal import MAX_INPUT_BYTES
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


@unittest.skipUnless(os.name == "nt", "Windows owned upstream integration")
class OwnedRunStartTests(unittest.TestCase):
    @staticmethod
    def _owner(startup, mode, *, bind_thread=True):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        if bind_thread:
            startup.events.bind_session_thread("session", "thread_demo")
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="request",
            expected_session_version=0, input_text="continue",
            recorded_at="2026-10-25T00:00:01Z")
        owner = launch_owned_upstream(
            startup, epoch=epoch,
            command=[sys.executable, str(PEER), mode],
            cwd=ROOT, env=dict(os.environ))
        return epoch, owner

    @staticmethod
    def _submit(queue, *, input_text="continue"):
        queue.submit(
            session_id="session", run_id="run", actor_id="device",
            request_id="request", expected_session_version=0,
            input_text=input_text)

    def test_queue_is_bounded_and_limits_input_bytes(self):
        queue = OwnedRunStartQueue(capacity=1)
        self._submit(queue)
        with self.assertRaisesRegex(OwnedRunStartError,
                                    "run_start_queue_full"):
            self._submit(queue)
        self.assertEqual(queue.take_nowait()[-1], "continue")
        self.assertIsNone(queue.take_nowait())
        with self.assertRaisesRegex(OwnedRunStartError,
                                    "run_input_too_large"):
            self._submit(queue, input_text="x" * (MAX_INPUT_BYTES + 1))
        with self.assertRaisesRegex(OwnedRunStartError,
                                    "invalid_run_start_queue_item"):
            self._submit(queue, input_text="")

    def test_loop_binds_turn_before_buffered_approval(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "approval_before_response")
                queue = OwnedRunStartQueue()
                self._submit(queue)
                self._submit(queue)
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    max_handoffs=1, run_starts=queue)
                self.assertEqual(result["reason"], "handoff_limit")
                self.assertEqual(result["counts"]["run_started"], 1)
                self.assertEqual(result["counts"]["approval_request"], 1)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["outcome"], "turn_started")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                repeated = start_owned_run_once(
                    owner, session_id="session", run_id="run",
                    actor_id="device", request_id="request",
                    expected_session_version=0, input_text="continue",
                    recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(repeated["kind"], "run_start_rejected")
                self.assertFalse(owner.client._closed)

    def test_changed_input_is_rejected_before_rpc(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "approval_before_response")
                result = start_owned_run_once(
                    owner, session_id="session", run_id="run",
                    actor_id="device", request_id="request",
                    expected_session_version=0, input_text="different",
                    recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(result["kind"], "run_start_rejected")
                self.assertEqual(result["cause"], "idempotency_conflict")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "accepted")

    def test_lost_turn_reply_recovers_without_repeating_queue(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "turn_start_no_response")
                queue = OwnedRunStartQueue()
                self._submit(queue)
                self._submit(queue)
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    run_starts=queue)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["cause"], "run_start_fault")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "unknown")
                self.assertIsNotNone(queue.take_nowait())

    def test_resume_identity_failure_never_claims_turn(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "thread_resume_wrong")
                queue = OwnedRunStartQueue()
                self._submit(queue)
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    run_starts=queue)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "failed")
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT turn_id FROM upstream_turns WHERE run_id='run'"
                ).fetchone())

    def test_claim_write_failure_isolates_before_turn_send(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "approval_before_response")
                startup.events.connection.execute(
                    """CREATE TRIGGER block_claim BEFORE INSERT ON events
                       WHEN NEW.event_json LIKE '%dispatching%'
                       BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                queue = OwnedRunStartQueue()
                self._submit(queue)
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    run_starts=queue)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "failed")
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT turn_id FROM upstream_turns WHERE run_id='run'"
                ).fetchone())


if __name__ == "__main__":
    unittest.main()
