"""One-event stdio handoff, ordering, and fail-closed persistence."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.event_handoff import (
    EventHandoffError, process_next_event, route_observed_event,
)
from runvori.codex.stdio import StdioClient, TransportError
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class EventHandoffTests(unittest.TestCase):
    def test_unknown_notification_is_diagnostic_and_control_request_rejected(self):
        class UnboundEvents:
            @staticmethod
            def append_timeline_bound(_session, **_values):
                # The simulated binding has no such turn: nothing is stored.
                raise EventJournalError("upstream_turn_not_found")

        class RejectStore:
            events = UnboundEvents()

        self.assertEqual(route_observed_event(
            RejectStore(), {"method": "item/started", "params": {
                "threadId": "thread", "turnId": "turn", "item": {
                    "id": "item", "type": "agentMessage", "text": "secret"}}},
            epoch=1, recorded_at="2026-10-25T00:00:00Z"),
            {"kind": "timeline_ignored",
             "reason": "unbound_or_inactive"})
        self.assertEqual(route_observed_event(
            RejectStore(), {"method": "synthetic/unknown", "params": {"text": "x"}},
            epoch=1, recorded_at="2026-10-25T00:00:00Z"),
            {"kind": "ignored_notification"})
        for message in ({"method": "unknown/request", "id": 1, "params": {}},
                        {"method": []},
                        {"method": "other", "result": {}}):
            with self.subTest(message=message):
                with self.assertRaises(EventHandoffError):
                    route_observed_event(
                        RejectStore(), message, epoch=1,
                        recorded_at="2026-10-25T00:00:00Z")

    def test_read_or_persist_failure_closes_client(self):
        class Client:
            def __init__(self, message):
                self.message = message
                self.closed = False

            def next_event(self, _timeout):
                if isinstance(self.message, Exception):
                    raise self.message
                return self.message

            def close(self):
                self.closed = True

        class RejectStore:
            def record_upstream_approval_request(self, **_kwargs):
                raise RuntimeError("storage_failure")

        for message in (
                TransportError("broken"),
                {"id": 1, "method": "unknown/request", "params": {}},
                {"id": 1, "method": "item/fileChange/requestApproval",
                 "params": {"threadId": "thread", "turnId": "turn"}}):
            client = Client(message)
            with self.subTest(message=message):
                with self.assertRaises(Exception):
                    process_next_event(
                        client, RejectStore(), epoch=1,
                        recorded_at="2026-10-25T00:00:00Z", timeout=1)
                self.assertTrue(client.closed)
        client = Client(None)
        self.assertIsNone(process_next_event(
            client, RejectStore(), epoch=1,
            recorded_at="2026-10-25T00:00:00Z", timeout=1))
        self.assertFalse(client.closed)

    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="request",
            expected_session_version=0, input_text="continue",
            recorded_at="2026-10-25T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "request", process_epoch=epoch,
            recorded_at="2026-10-25T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "request", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-25T00:00:03Z")
        return epoch

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_stdio_request_terminal_then_late_closure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                with StdioClient([sys.executable, str(PEER), "event_handoff"],
                                 cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    client.request("turn/start", {})
                    kinds = []
                    for second in (4, 5, 6):
                        result = process_next_event(
                            client, startup, epoch=epoch,
                            recorded_at=f"2026-10-25T00:00:0{second}Z",
                            timeout=1)
                        kinds.append(result["kind"])
                    self.assertEqual(kinds, ["approval_request", "turn_terminal",
                                             "request_closed"])
                    self.assertIsNone(process_next_event(
                        client, startup, epoch=epoch,
                        recorded_at="2026-10-25T00:00:07Z", timeout=0.1))
                approval = startup.events.connection.execute(
                    "SELECT status, resolution_kind FROM approvals").fetchone()
                self.assertEqual(tuple(approval), ("expired", "request_closed"))
                self.assertIsNone(startup.events.snapshot("session")["active_run"])
                self.assertEqual(startup.events.snapshot("session")["open_approval_ids"], [])

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_persist_failure_closes_real_upstream_without_partial_approval(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_handoff BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%approval.requested%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with StdioClient([sys.executable, str(PEER), "event_handoff"],
                                 cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    client.request("turn/start", {})
                    with self.assertRaises(sqlite3.IntegrityError):
                        process_next_event(
                            client, startup, epoch=epoch,
                            recorded_at="2026-10-25T00:00:04Z", timeout=1)
                    with self.assertRaisesRegex(TransportError,
                                                "connection_unusable"):
                        client.next_event(0.1)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "running")


if __name__ == "__main__":
    unittest.main()
