"""One-shot turn/start claim, response binding, and uncertain outcomes."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.event_handoff import process_next_event
from runvori.codex.run_dispatch import RunDispatchError, start_run_once
from runvori.codex.stdio import StdioClient, TransportError
from runvori.codex.thread_resume import ThreadResumeError
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class RunDispatchTests(unittest.TestCase):
    @staticmethod
    def _reserved(startup, *, bind_thread=True):
        epoch = startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        if bind_thread:
            startup.events.bind_session_thread("session", "thread_demo")
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="request",
            expected_session_version=0, input_text="continue",
            recorded_at="2026-10-25T00:00:01Z")
        return epoch

    @staticmethod
    def _start(client, startup, epoch, *, input_text="continue"):
        return start_run_once(
            client, startup, session_id="session", run_id="run",
            actor_id="device", request_id="request",
            expected_session_version=0, input_text=input_text,
            epoch=epoch,
            recorded_at="2026-10-25T00:00:02Z")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_approval_before_response_is_bound_then_persisted(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._reserved(startup)
                with StdioClient(
                        [sys.executable, str(PEER), "approval_before_response"],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    result = self._start(client, startup, epoch)
                    self.assertEqual(result["status"], "confirmed")
                    self.assertEqual(result["outcome"], "turn_started")
                    self.assertEqual(startup.events.snapshot("session")[
                        "active_run"]["status"], "running")
                    request = process_next_event(
                        client, startup, epoch=epoch,
                        recorded_at="2026-10-25T00:00:03Z", timeout=1)
                    self.assertEqual(request["kind"], "approval_request")
                    self.assertEqual(request["record"]["status"], "pending")
                    with self.assertRaisesRegex(RunDispatchError,
                                                "dispatch_unavailable"):
                        self._start(client, startup, epoch)
                self.assertEqual(startup.events.connection.execute(
                    "SELECT thread_id, turn_id FROM upstream_turns").fetchone()[:],
                    ("thread_demo", "turn_demo"))

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_changed_input_and_claim_failure_never_send(self):
        class RejectClient:
            def request(self, method, _params):
                if method == "thread/resume":
                    return {"thread": {"id": "thread_demo"}}
                raise AssertionError("turn_start_sent")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._reserved(startup)
                with self.assertRaisesRegex(EventJournalError,
                                            "idempotency_conflict"):
                    self._start(RejectClient(), startup, epoch,
                                input_text="different")
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_run_claim BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%dispatching%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(sqlite3.IntegrityError):
                    self._start(RejectClient(), startup, epoch)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "starting")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_unbound_session_cannot_claim_or_send(self):
        class RejectClient:
            def request(self, method, _params):
                if method == "thread/resume":
                    return {"thread": {"id": "thread_demo"}}
                raise AssertionError("turn_start_sent")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._reserved(startup, bind_thread=False)
                with self.assertRaisesRegex(EventJournalError,
                                            "session_thread_not_bound"):
                    self._start(RejectClient(), startup, epoch)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "accepted")
                startup.events.bind_session_thread("session", "thread_demo")
                self.assertEqual(startup.events.get_session_thread("session"),
                                 "thread_demo")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_resume_identity_failure_prevents_turn_start_claim(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._reserved(startup)
                with StdioClient(
                        [sys.executable, str(PEER), "thread_resume_wrong"],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    with self.assertRaisesRegex(ThreadResumeError,
                                                "thread_resume_unconfirmed"):
                        self._start(client, startup, epoch)
                    with self.assertRaisesRegex(TransportError,
                                                "connection_unusable"):
                        client.next_event(0.1)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "request")["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "starting")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_missing_or_malformed_response_is_unknown_without_retry(self):
        for mode in ("turn_start_no_response", "turn_start_bad_shape"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    with LockedHostJournals(Path(temp)) as startup:
                        epoch = self._reserved(startup)
                        with StdioClient([sys.executable, str(PEER), mode],
                                         cwd=ROOT, env=dict(os.environ)) as client:
                            client.initialize()
                            result = self._start(client, startup, epoch)
                            self.assertEqual(result["status"], "unknown")
                            with self.assertRaisesRegex(TransportError,
                                                        "connection_unusable"):
                                client.next_event(0.1)
                        self.assertEqual(startup.events.get_run_operation(
                            "device", "request")["status"], "unknown")
                        self.assertEqual(startup.events.snapshot("session")[
                            "active_run"]["status"], "starting")
                        with self.assertRaisesRegex(RunDispatchError,
                                                    "dispatch_unavailable"):
                            self._start(None, startup, epoch)

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_turn_binding_failure_closes_upstream_and_keeps_unknown(self):
        class RespondingClient:
            def __init__(self):
                self.closed = False
                self.calls = []

            def request(self, method, params):
                self.calls.append(method)
                assert params["threadId"] == "thread_demo"
                if method == "thread/resume":
                    return {"thread": {"id": "thread_demo"}}
                assert method == "turn/start"
                return {"turn": {"id": "turn_demo"}}

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._reserved(startup)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_run_binding BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%run.started%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                client = RespondingClient()
                result = self._start(client, startup, epoch)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(client.calls,
                                 ["thread/resume", "turn/start"])
                self.assertTrue(client.closed)
                self.assertIsNone(db.execute(
                    "SELECT run_id FROM upstream_turns").fetchone())
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "starting")


if __name__ == "__main__":
    unittest.main()
