"""Bound upstream terminal notifications and atomic run completion."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.stdio import StdioClient
from runvori.codex.turn_events import (
    TurnEventError, record_turn_completed_notification,
)
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals

from approval_fixtures import reviewable_command, seed_workspace_session


PEER = Path(__file__).with_name("fake_app_server.py")


class TurnEventTests(unittest.TestCase):
    def test_bad_notification_never_calls_store(self):
        class RejectStore:
            def record_upstream_turn_terminal(self, **_kwargs):
                raise AssertionError("unexpected_store_write")

        for message in (
                {}, {"method": "turn/completed", "params": {}},
                {"id": 1, "method": "turn/completed", "params": {}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread", "turn": {"id": "turn", "status": "running"}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread", "turn": {"id": "turn", "status": {}}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread", "turn": {"id": "", "status": "completed"}}}):
            with self.subTest(message=message):
                with self.assertRaises(TurnEventError):
                    record_turn_completed_notification(
                        RejectStore(), message, epoch=1,
                        recorded_at="2026-10-25T00:00:00Z")

    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        seed_workspace_session(startup, epoch)
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
    def test_stdio_terminal_releases_only_bound_run_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                with StdioClient([sys.executable, str(PEER), "turn_terminal"],
                                 cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    client.request("turn/start", {})
                    message = client.next_event(1)
                self.assertEqual(message["method"], "turn/completed")
                wrong = {**message, "params": {
                    **message["params"], "threadId": "other"}}
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_turn_not_found"):
                    record_turn_completed_notification(
                        startup, wrong, epoch=epoch,
                        recorded_at="2026-10-25T00:00:04Z")
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    record_turn_completed_notification(
                        startup, message, epoch=epoch + 1,
                        recorded_at="2026-10-25T00:00:04Z")
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_terminal BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%run.completed%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(Exception):
                    record_turn_completed_notification(
                        startup, message, epoch=epoch,
                        recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(db.execute(
                    "SELECT run_id FROM active_slot").fetchone()[0], "run")
                self.assertEqual(db.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "running")
                db.execute("DROP TRIGGER block_terminal")
                event = record_turn_completed_notification(
                    startup, message, epoch=epoch,
                    recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(event["type"], "run.completed")
                self.assertIsNone(db.execute(
                    "SELECT run_id FROM active_slot").fetchone())
                seq = startup.events.snapshot("session")["snapshot_seq"]
                self.assertEqual(record_turn_completed_notification(
                    startup, message, epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z"), event)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], seq)
                failed = {**message, "params": {**message["params"],
                    "turn": {"id": "turn_demo", "status": "failed"}}}
                with self.assertRaisesRegex(EventJournalError, "terminal_conflict"):
                    record_turn_completed_notification(
                        startup, failed, epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_failed_and_interrupted_statuses_are_preserved(self):
        for terminal in ("failed", "interrupted"):
            with self.subTest(terminal=terminal):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    with LockedHostJournals(Path(temp)) as startup:
                        epoch = self._running(startup)
                        event = record_turn_completed_notification(
                            startup, {"method": "turn/completed", "params": {
                                "threadId": "thread_demo", "turn": {
                                    "id": "turn_demo", "status": terminal}}},
                            epoch=epoch, recorded_at="2026-10-25T00:00:04Z")
                        self.assertEqual(event["type"], f"run.{terminal}")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_terminal_settles_unsent_and_uncertain_approvals_atomically(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                for name in ("pending", "recorded", "claimed", "unknown"):
                    startup.record_approval_request(
                        "session", "run", approval_id=name, epoch=epoch,
                        upstream_request_id=name, kind="command",
                        details=reviewable_command(startup.data_dir),
                        turn_id="turn_demo",
                        recorded_at="2026-10-25T00:00:04Z")
                for name in ("recorded", "claimed", "unknown"):
                    startup.record_approval_decision(
                        name, actor_id="device", request_id=f"decision-{name}",
                        expected_version=1, decision="allow_once",
                        recorded_at="2026-10-25T00:00:05Z")
                for name in ("claimed", "unknown"):
                    startup.claim_approval_dispatch(
                        "device", f"decision-{name}", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                startup.mark_approval_dispatch_unknown(
                    "device", "decision-unknown", epoch=epoch,
                    recorded_at="2026-10-25T00:00:07Z")
                db = startup.events.connection
                before = startup.events.snapshot("session")["snapshot_seq"]
                db.execute("""CREATE TRIGGER block_last_terminal BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%run.completed%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                message = {"method": "turn/completed", "params": {
                    "threadId": "thread_demo", "turn": {
                        "id": "turn_demo", "status": "completed"}}}
                with self.assertRaises(Exception):
                    record_turn_completed_notification(
                        startup, message, epoch=epoch,
                        recorded_at="2026-10-25T00:00:08Z")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"],
                                 before)
                self.assertEqual(startup.approvals.get("pending")["status"],
                                 "pending")
                self.assertEqual(startup.approvals.get("claimed")["status"],
                                 "dispatching")
                db.execute("DROP TRIGGER block_last_terminal")
                record_turn_completed_notification(
                    startup, message, epoch=epoch,
                    recorded_at="2026-10-25T00:00:08Z")
                self.assertEqual(startup.approvals.get("pending")["status"],
                                 "expired")
                self.assertEqual(startup.approvals.get("recorded")["status"],
                                 "expired")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-recorded")["outcome"],
                    "turn_terminal_before_dispatch")
                self.assertEqual(startup.approvals.get("claimed")["status"],
                                 "unknown")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-claimed")["outcome"],
                    "turn_terminal_dispatch_unconfirmed")
                self.assertEqual(startup.approvals.get("unknown")["status"],
                                 "unknown")
                snapshot = startup.events.snapshot("session")
                self.assertIsNone(snapshot["active_run"])
                self.assertEqual(snapshot["open_approval_ids"],
                                 ["claimed", "unknown"])
                self.assertEqual(snapshot["snapshot_seq"], before + 6)
                closed = startup.record_upstream_request_closed(
                    thread_id="thread_demo", epoch=epoch,
                    upstream_request_id="pending",
                    recorded_at="2026-10-25T00:00:09Z")
                self.assertEqual(closed["status"], "expired")
                self.assertEqual(closed["resolution_kind"], "request_closed")
                seq = startup.events.snapshot("session")["snapshot_seq"]
                self.assertEqual(startup.record_upstream_request_closed(
                    thread_id="thread_demo", epoch=epoch,
                    upstream_request_id="pending",
                    recorded_at="2026-10-25T00:00:10Z"), closed)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], seq)
                recorded_closed = startup.record_upstream_request_closed(
                    thread_id="thread_demo", epoch=epoch,
                    upstream_request_id="recorded",
                    recorded_at="2026-10-25T00:00:10Z")
                self.assertEqual(recorded_closed["status"], "expired")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-recorded")["outcome"],
                    "turn_terminal_before_dispatch")
                self.assertEqual(startup.record_upstream_request_closed(
                    thread_id="thread_demo", epoch=epoch,
                    upstream_request_id="claimed",
                    recorded_at="2026-10-25T00:00:11Z")["status"],
                    "resolved")


if __name__ == "__main__":
    unittest.main()
