"""Typed upstream request identity and synthetic notification handoff."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.approval_events import (
    ApprovalEventError, record_approval_request_event,
    record_resolved_notification, upstream_request_key,
)
from runvori.codex.stdio import StdioClient
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class ApprovalEventTests(unittest.TestCase):
    def test_request_key_preserves_json_rpc_type(self):
        self.assertEqual(upstream_request_key(23), "int:23")
        self.assertEqual(upstream_request_key("23"), "str:23")
        self.assertNotEqual(upstream_request_key(23), upstream_request_key("23"))
        for value in (True, None, b"23", "", "x" * 129, 2**63):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ApprovalEventError,
                                            "invalid_upstream_request_id"):
                    upstream_request_key(value)

    def test_bad_resolved_shape_never_calls_store(self):
        class RejectStore:
            def record_upstream_request_closed(self, **_kwargs):
                raise AssertionError("unexpected_store_write")

        for message in (
                {}, {"method": "other", "params": {}},
                {"id": 23, "method": "serverRequest/resolved",
                 "params": {"threadId": "thread", "requestId": 23}},
                {"method": "serverRequest/resolved",
                 "params": {"requestId": 23}},
                {"method": "serverRequest/resolved",
                 "params": {"threadId": "thread", "requestId": True}}):
            with self.subTest(message=message):
                with self.assertRaises(ApprovalEventError):
                    record_resolved_notification(
                        RejectStore(), message, epoch=1,
                        recorded_at="2026-10-25T00:00:00Z")

    def test_bad_request_shape_never_calls_store(self):
        class RejectStore:
            def record_upstream_approval_request(self, **_kwargs):
                raise AssertionError("unexpected_store_write")

        for message in (
                {}, {"id": 1, "method": "other", "params": {}},
                {"id": True, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": "thread", "turnId": "turn"}},
                {"id": 1, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": "thread"}},
                {"id": 1, "method": "item/fileChange/requestApproval",
                 "params": {"threadId": "", "turnId": "turn"}},
                {"id": 1, "method": "item/fileChange/requestApproval",
                 "params": {"threadId": "thread", "turnId": "turn"},
                 "result": {}}):
            with self.subTest(message=message):
                with self.assertRaises(ApprovalEventError):
                    record_approval_request_event(
                        RejectStore(), message, epoch=1,
                        recorded_at="2026-10-25T00:00:00Z")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_stdio_resolved_event_matches_thread_epoch_and_typed_id(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = startup.recover_host_after_isolation(
                    old_process_epoch=0,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:00Z")["epoch"]
                startup.events.create_session("session", {})
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="run-request",
                    expected_session_version=0, input_text="continue",
                    recorded_at="2026-10-25T00:00:01Z")
                startup.events.claim_run_dispatch(
                    "device", "run-request", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:02Z")
                startup.events.confirm_run_started(
                    "device", "run-request", process_epoch=epoch,
                    thread_id="thread_demo", turn_id="turn_demo",
                    recorded_at="2026-10-25T00:00:03Z")
                with StdioClient(
                        [sys.executable, str(PEER), "approval_resolved"],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    client.request("turn/start", {})
                    request = client.next_event(1)
                    self.assertEqual(request["id"], 23)
                    integer = record_approval_request_event(
                        startup, request, epoch=epoch,
                        recorded_at="2026-10-25T00:00:04Z")
                    self.assertEqual(integer["status"], "pending")
                    seq = startup.events.snapshot("session")["snapshot_seq"]
                    self.assertEqual(record_approval_request_event(
                        startup, request, epoch=epoch,
                        recorded_at="2026-10-25T00:00:05Z"), integer)
                    self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], seq)
                    string_request = dict(request, id="23")
                    string_approval = record_approval_request_event(
                        startup, string_request, epoch=epoch,
                        recorded_at="2026-10-25T00:00:05Z")
                    self.assertNotEqual(integer["approval_id"],
                                        string_approval["approval_id"])
                    resolved = client.next_event(1)
                self.assertEqual(resolved["method"], "serverRequest/resolved")
                wrong_thread = {"method": "serverRequest/resolved", "params": {
                    "threadId": "other-thread", "requestId": 23}}
                with self.assertRaisesRegex(EventJournalError, "approval_not_found"):
                    record_resolved_notification(
                        startup, wrong_thread, epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                with self.assertRaisesRegex(EventJournalError, "approval_not_found"):
                    record_resolved_notification(
                        startup, resolved, epoch=epoch + 1,
                        recorded_at="2026-10-25T00:00:06Z")
                wrong_turn = {**request, "params": {
                    **request["params"], "turnId": "other-turn"}}
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_turn_not_found"):
                    record_approval_request_event(
                        startup, wrong_turn, epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                wrong_kind = {**request,
                              "method": "item/fileChange/requestApproval"}
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_request_conflict"):
                    record_approval_request_event(
                        startup, wrong_kind, epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                changed_command = {**request, "params": {
                    **request["params"], "command": "different"}}
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_request_conflict"):
                    record_approval_request_event(
                        startup, changed_command, epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    record_approval_request_event(
                        startup, request, epoch=epoch + 1,
                        recorded_at="2026-10-25T00:00:06Z")
                closed = record_resolved_notification(
                    startup, resolved, epoch=epoch,
                    recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(closed["approval_id"], integer["approval_id"])
                self.assertEqual(closed["status"], "expired")
                self.assertEqual(startup.approvals.get(
                    string_approval["approval_id"])["status"], "pending")
                seq = startup.events.snapshot("session")["snapshot_seq"]
                self.assertEqual(record_resolved_notification(
                    startup, resolved, epoch=epoch,
                    recorded_at="2026-10-25T00:00:07Z"), closed)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], seq)
                self.assertEqual(record_approval_request_event(
                    startup, request, epoch=epoch,
                    recorded_at="2026-10-25T00:00:07Z"), closed)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], seq)
                string_message = {"method": "serverRequest/resolved", "params": {
                    "threadId": "thread_demo", "requestId": "23"}}
                self.assertEqual(record_resolved_notification(
                    startup, string_message, epoch=epoch,
                    recorded_at="2026-10-25T00:00:08Z")["approval_id"],
                    string_approval["approval_id"])


if __name__ == "__main__":
    unittest.main()
