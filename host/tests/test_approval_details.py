"""Approval reviews: normalization, storage, and the no-blind-allow gate."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex import approval_details
from runvori.codex.event_handoff import route_observed_event
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


class NormalizationTests(unittest.TestCase):
    def test_command_review_requires_command_item_and_inside_cwd(self):
        workspace = str(ROOT)
        complete = approval_details.command_details({
            "command": "python --version", "cwd": workspace,
            "itemId": "item", "startedAtMs": 1700000000000, "kind": "command",
            "reason": "probe"})
        review = approval_details.normalize(
            complete, workspace_root=workspace,
            recorded_at="2026-10-27T00:00:00Z")
        self.assertEqual(review["details_status"], "complete")
        self.assertTrue(approval_details.can_allow(
            review, status="pending", decided=False))
        self.assertEqual(review["requested_at"], "2023-11-14T22:13:20Z")

        outside = approval_details.command_details({
            "command": "python --version", "cwd": str(ROOT.parent),
            "itemId": "item", "kind": "command"})
        review = approval_details.normalize(
            outside, workspace_root=workspace,
            recorded_at="2026-10-27T00:00:00Z")
        self.assertEqual(review["details_status"], "insufficient")
        self.assertIn("cwd_unverified", review["missing"])
        self.assertIsNone(review["cwd"])
        self.assertFalse(approval_details.can_allow(
            review, status="pending", decided=False))

    def test_unknown_command_kind_and_write_stdin_are_not_allowable(self):
        for kind, reason in (("writeStdin", "unsupported_command_kind"),
                             ("futureKind", "unsupported_command_kind")):
            with self.subTest(kind=kind):
                details = approval_details.command_details({
                    "command": "echo hi", "cwd": str(ROOT), "itemId": "item",
                    "kind": kind})
                review = approval_details.normalize(
                    details, workspace_root=str(ROOT),
                    recorded_at="2026-10-27T00:00:00Z")
                self.assertIn(reason, review["missing"])
                self.assertEqual(review["details_status"], "insufficient")

    def test_file_review_is_insufficient_until_matching_item_arrives(self):
        request = approval_details.file_change_details({
            "itemId": "patch", "startedAtMs": 1700000000000})
        review = approval_details.normalize(
            request, workspace_root=str(ROOT),
            recorded_at="2026-10-27T00:00:00Z")
        self.assertEqual(review["details_status"], "insufficient")
        self.assertIn("file_changes_missing", review["missing"])

        item = approval_details.item_changes({
            "id": "patch", "type": "fileChange", "status": "inProgress",
            "changes": [{"path": "a.txt", "kind": {"type": "add"},
                         "diff": "+a"}]})
        merged = approval_details.merge_item(review, item)
        self.assertEqual(merged["details_status"], "complete")
        self.assertEqual(merged["changes"][0]["path"], "a.txt")
        self.assertTrue(approval_details.can_allow(
            merged, status="pending", decided=False))

    def test_absolute_escaping_and_oversized_content_stay_insufficient(self):
        for path in ("../escape.txt", "C:/absolute.txt", "/abs.txt", ""):
            with self.subTest(path=path):
                request = approval_details.file_change_details({"itemId": "p"})
                review = approval_details.normalize(
                    request, workspace_root=str(ROOT),
                    recorded_at="2026-10-27T00:00:00Z")
                merged = approval_details.merge_item(review, {
                    "item_id": "p", "changes": [{
                        "path": path, "change_type": "add", "move_path": None,
                        "diff": None, "diff_truncated": False}],
                    "review_truncated": False})
                self.assertEqual(merged["details_status"], "insufficient")
                self.assertIn("unsafe_change_path", merged["missing"])

    def test_truncated_diff_blocks_allow_but_stays_visible(self):
        request = approval_details.file_change_details({"itemId": "p"})
        review = approval_details.normalize(
            request, workspace_root=str(ROOT),
            recorded_at="2026-10-27T00:00:00Z")
        merged = approval_details.merge_item(review, {
            "item_id": "p", "changes": [{
                "path": "a.txt", "change_type": "update", "move_path": None,
                "diff": "+" + "x" * approval_details.MAX_DIFF_BYTES,
                "diff_truncated": True}], "review_truncated": True})
        self.assertTrue(merged["review_truncated"])
        self.assertEqual(merged["changes"][0]["path"], "a.txt")
        self.assertFalse(approval_details.can_allow(
            merged, status="pending", decided=False))

    def test_damaged_stored_review_is_insufficient(self):
        for raw in ("", "not-json", '{"details_status": "maybe"}'):
            with self.subTest(raw=raw):
                review = approval_details.decode(raw, "unrecorded", "now")
                self.assertEqual(review["details_status"], "insufficient")


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class ApprovalDetailStorageTests(unittest.TestCase):
    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0, expected_run_id=None,
            expected_run_version=None, reason="host_restart",
            recorded_at="2026-10-27T00:00:00Z")["epoch"]
        workspace = startup.data_dir / "workspace"
        workspace.mkdir(exist_ok=True)
        startup.register_workspace(
            "workspace", workspace, sandbox="read-only", epoch=epoch)
        startup.create_session_in_workspace(
            "session", "workspace", {}, epoch=epoch)
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="start",
            expected_session_version=0, input_text="go",
            recorded_at="2026-10-27T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "start", process_epoch=epoch,
            recorded_at="2026-10-27T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "start", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-27T00:00:03Z")
        return epoch, workspace

    @staticmethod
    def _closure(startup, epoch):
        return startup.record_upstream_approval_request(
            thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
            upstream_request_id="str:1", kind="command",
            approval_id="apr", request_digest=b"x" * 32,
            details=approval_details.command_details({
                "command": "python --version",
                "cwd": str(startup.data_dir / "workspace"),
                "itemId": "item", "startedAtMs": 1700000000000,
                "kind": "command"}),
            recorded_at="2026-10-27T00:00:04Z")

    def test_complete_review_is_stored_and_can_allow(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, _workspace = self._running(startup)
                self._closure(startup, epoch)
                view = startup.get_approval_view("apr")
                self.assertEqual(view["details_status"], "complete")
                self.assertTrue(view["can_allow"])
                self.assertTrue(view["can_deny"])
                self.assertEqual(view["allowed_decisions"],
                                 ["allow_once", "deny"])
                self.assertEqual(view["command"], "python --version")
                self.assertTrue(view["cwd_inside_workspace"])
                self.assertEqual(view["workspace_name"], "workspace")
                self.assertIsNone(view["expires_at"])

    def test_insufficient_review_rejects_allow_and_permits_deny(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, _workspace = self._running(startup)
                startup.record_upstream_approval_request(
                    thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
                    upstream_request_id="str:2", kind="command",
                    approval_id="apr", request_digest=b"y" * 32,
                    details=approval_details.command_details({
                        "itemId": "item", "kind": "command"}),
                    recorded_at="2026-10-27T00:00:04Z")
                view = startup.get_approval_view("apr")
                self.assertEqual(view["details_status"], "insufficient")
                self.assertFalse(view["can_allow"])
                self.assertEqual(view["allowed_decisions"], ["deny"])
                with self.assertRaisesRegex(
                        EventJournalError, "approval_details_insufficient"):
                    startup.record_approval_decision(
                        "apr", actor_id="device", request_id="allow",
                        expected_version=1, decision="allow_once",
                        recorded_at="2026-10-27T00:00:05Z")
                denied = startup.record_approval_decision(
                    "apr", actor_id="device", request_id="deny",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-27T00:00:06Z")
                self.assertEqual(denied["status"], "accepted")

    def test_item_notification_completes_a_file_review_after_the_fact(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, _workspace = self._running(startup)
                startup.record_upstream_approval_request(
                    thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
                    upstream_request_id="str:3", kind="file_change",
                    approval_id="apr", request_digest=b"z" * 32,
                    details=approval_details.file_change_details({
                        "itemId": "patch", "startedAtMs": 1700000000001}),
                    recorded_at="2026-10-27T00:00:04Z")
                self.assertFalse(startup.get_approval_view("apr")["can_allow"])
                recorded = route_observed_event(
                    startup, {"method": "item/started", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "item": {"id": "patch", "type": "fileChange",
                                 "status": "inProgress", "changes": [{
                                     "path": "a.txt",
                                     "kind": {"type": "add"},
                                     "diff": "+a"}]}}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:05Z")
                self.assertEqual(recorded["kind"], "approval_details_updated")
                view = startup.get_approval_view("apr")
                self.assertEqual(view["details_status"], "complete")
                self.assertTrue(view["can_allow"])
                self.assertEqual(view["version"], 2)
                self.assertEqual(view["changes"][0]["path"], "a.txt")
                self.assertTrue(view["changes"][0]["diff_reference"]
                                .startswith("diffref:run:"))

    def test_v13_approval_migrates_to_insufficient(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch, _workspace = self._running(startup)
                self._closure(startup, epoch)
            import sqlite3
            connection = sqlite3.connect(data_dir / "host.db",
                                        isolation_level=None)
            try:
                connection.execute(
                    "UPDATE approvals SET details_json=NULL, "
                    "details_status=NULL")
                connection.execute("PRAGMA user_version=13")
            finally:
                connection.close()
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.events.connection.execute(
                    "PRAGMA user_version").fetchone()[0], 15)
                migrated = reopened.events.connection.execute(
                    "SELECT details_status FROM approvals "
                    "WHERE approval_id='apr'").fetchone()[0]
                self.assertEqual(migrated, "insufficient")
                # Recovery of a new epoch expires the old pending request, so
                # neither path can turn the migrated row into an allow.
                version = reopened.events.connection.execute(
                    "SELECT version FROM runs WHERE run_id='run'"
                ).fetchone()[0]
                reopened.recover_host_after_isolation(
                    old_process_epoch=epoch, expected_run_id="run",
                    expected_run_version=version, reason="host_restart",
                    recorded_at="2026-10-27T00:00:06Z")
                view = reopened.get_approval_view("apr")
                self.assertFalse(view["can_allow"])
                self.assertEqual(view["status"], "expired")
                with self.assertRaises(EventJournalError):
                    reopened.record_approval_decision(
                        "apr", actor_id="device", request_id="allow",
                        expected_version=view["version"],
                        decision="allow_once",
                        recorded_at="2026-10-27T00:00:07Z")


if __name__ == "__main__":
    unittest.main()