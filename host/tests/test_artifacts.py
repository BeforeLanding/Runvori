"""Persisted command-output and file-diff artifacts."""

import hashlib
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from runvori.application import run_results
from runvori.codex.event_handoff import route_observed_event
from runvori.infrastructure.locked_journal import LockedHostJournals


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class ArtifactTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def _opened(temp: str):
        with LockedHostJournals(Path(temp)) as startup:
            epoch = startup.recover_host_after_isolation(
                old_process_epoch=0, expected_run_id=None,
                expected_run_version=None, reason="host_restart",
                recorded_at="2026-10-27T00:00:00Z")["epoch"]
            startup.register_workspace(
                "ws", Path(temp), sandbox="workspace-write", epoch=epoch)
            startup.create_session_in_workspace("session", "ws", {}, epoch=epoch)
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
            yield startup

    @staticmethod
    def _event(startup, epoch, method: str, params: dict, second: int,
               message_id: str | None = None):
        message = {"method": method, "params": params}
        if message_id is not None:
            message["id"] = message_id
        route_observed_event(
            startup, message, epoch=epoch,
            recorded_at=f"2026-10-27T00:00:{second:02d}Z")

    def _command(self, startup, epoch, *, item_id: str = "cmd",
                 output: str | None = "abc", output_repeats: int = 0,
                 exit_code: int = 0, status: str = "completed") -> None:
        params = {
            "threadId": "thread_demo", "turnId": "turn_demo",
            "startedAtMs": 1700000000000,
            "item": {"id": item_id, "type": "commandExecution",
                     "command": "python --version", "cwd": str(Path(startup.data_dir)),
                     "status": "inProgress"}}
        self._event(startup, epoch, "item/started", params, 4)
        if output_repeats:
            for index in range(output_repeats):
                self._event(
                    startup, epoch, "item/commandExecution/outputDelta",
                    {"threadId": "thread_demo", "turnId": "turn_demo",
                     "itemId": item_id, "delta": "x" * 8192}, 5 + index)
        elif output is not None:
            for index, start in enumerate(range(0, len(output), 4096)):
                self._event(
                    startup, epoch, "item/commandExecution/outputDelta",
                    {"threadId": "thread_demo", "turnId": "turn_demo",
                     "itemId": item_id, "delta": output[start:start + 4096]},
                    5 + index)
        item = {"id": item_id, "type": "commandExecution",
                "command": "python --version",
                "cwd": str(Path(startup.data_dir)),
                "status": status, "exitCode": exit_code, "durationMs": 12}
        self._event(
            startup, epoch, "item/completed",
            {"threadId": "thread_demo", "turnId": "turn_demo",
             "completedAtMs": 1700000000120, "item": item}, 40)

    def _finish(self, startup, epoch, second: int = 90) -> None:
        self._event(
            startup, epoch, "turn/completed",
            {"threadId": "thread_demo",
             "turn": {"id": "turn_demo", "status": "completed"}}, second)

    @staticmethod
    def _artifact(startup, kind: str, source: str) -> dict:
        page = run_results.run_artifacts(startup.events.connection, "run")
        matches = [item for item in page["items"]
                   if item["kind"] == kind and item["source"] == source]
        if len(matches) != 1:
            raise AssertionError(f"expected one {kind}/{source}, got {matches}")
        return matches[0]

    @staticmethod
    def _read_all(startup, artifact_id: str, limit: int) -> tuple[bytes, list[int]]:
        chunks = []
        offsets = []
        offset = 0
        while True:
            page = run_results.artifact_content(
                startup.events.connection, artifact_id,
                offset=offset, limit=limit)
            offsets.append(page["next_offset"])
            chunks.append(page["data"])
            if page["next_offset"] >= page["size_bytes"]:
                break
            if page["next_offset"] <= offset:
                raise AssertionError("offset did not advance")
            offset = page["next_offset"]
        return b"".join(chunks), offsets

    def test_command_output_is_chunked_stable_and_hashed(self):
        output = "0123456789" * 3000
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, output=output)
                self._finish(startup, epoch)
                artifact = self._artifact(
                    startup, "command_output", "command_execution")
                self.assertTrue(artifact["content_available"])
                self.assertEqual(len(output.encode()), artifact["size_bytes"])
                self.assertEqual(
                    hashlib.sha256(output.encode()).hexdigest(),
                    artifact["sha256"])
                data, offsets = self._read_all(
                    startup, artifact["artifact_id"], 4096)
                self.assertEqual(output.encode(), data)
                self.assertEqual(sorted(set(offsets)), offsets)
                self.assertGreater(len(offsets), 1)
            with LockedHostJournals(Path(temp)) as reopened:
                page = run_results.run_artifacts(reopened.events.connection, "run")
                artifact = next(
                    item for item in page["items"]
                    if item["kind"] == "command_output")
                data, _ = self._read_all(
                    reopened, artifact["artifact_id"], 4096)
                self.assertEqual(output.encode(), data)
                self.assertEqual(
                    hashlib.sha256(output.encode()).hexdigest(),
                    artifact["sha256"])

    def test_empty_output_is_available_and_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, output=None)
                self._finish(startup, epoch)
                artifact = self._artifact(
                    startup, "command_output", "command_execution")
                self.assertTrue(artifact["content_available"])
                self.assertEqual(0, artifact["size_bytes"])
                self.assertEqual(
                    hashlib.sha256(b"").hexdigest(), artifact["sha256"])
                page = run_results.artifact_content(
                    startup.events.connection, artifact["artifact_id"])
                self.assertEqual(b"", page["data"])
                self.assertEqual(0, page["next_offset"])

    def test_truncated_output_keeps_dropped_bytes_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, output_repeats=260)
                self._finish(startup, epoch)
                artifact = self._artifact(
                    startup, "command_output", "command_execution")
                self.assertTrue(artifact["truncated"])
                self.assertGreater(artifact["dropped_bytes"], 0)
                self.assertEqual(
                    artifact["size_bytes"] + artifact["dropped_bytes"],
                    260 * 8192)
                data, _ = self._read_all(
                    startup, artifact["artifact_id"], 65536)
                self.assertEqual(b"x" * artifact["size_bytes"], data)

    def test_file_review_and_upstream_item_are_distinct_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                epoch = startup.approvals.current_epoch()
                change = {"path": "probe-result.txt",
                          "kind": {"type": "add"},
                          "diff": "+RUNVORI_PROBE_OK\n"}
                item = {"id": "patch_demo", "type": "fileChange",
                        "status": "inProgress", "changes": [change]}
                self._event(startup, epoch, "item/started", {
                    "threadId": "thread_demo", "turnId": "turn_demo",
                    "item": item}, 4)
                self._event(startup, epoch,
                            "item/fileChange/requestApproval", {
                                "threadId": "thread_demo",
                                "turnId": "turn_demo",
                                "itemId": "patch_demo",
                                "startedAtMs": 1700000000001}, 5,
                            message_id="approval_demo")
                self._event(startup, epoch, "item/completed", {
                    "threadId": "thread_demo", "turnId": "turn_demo",
                    "item": {**item, "status": "completed"}}, 6)
                self._finish(startup, epoch)
                page = run_results.run_artifacts(
                    startup.events.connection, "run")
                proposal = [item for item in page["items"]
                             if item["source"] == "approval_proposal"]
                upstream = [item for item in page["items"]
                            if item["source"] == "upstream_report"]
                self.assertEqual(1, len(proposal))
                self.assertEqual(1, len(upstream))
                for artifact in (proposal[0], upstream[0]):
                    self.assertEqual("file_diff", artifact["kind"])
                    self.assertEqual("probe-result.txt", artifact["path"])
                    self.assertEqual("add", artifact["operation"])
                    self.assertTrue(artifact["content_available"])
                    page = run_results.artifact_content(
                        startup.events.connection, artifact["artifact_id"])
                    self.assertEqual(b"+RUNVORI_PROBE_OK\n", page["data"])
                self.assertEqual("proposed", proposal[0]["attribution"])
                self.assertEqual("reported", upstream[0]["attribution"])
                self.assertEqual("completed", upstream[0]["source_status"])

    def test_git_worktree_snapshot_is_attributed_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                root = Path(temp)
                subprocess.run(["git", "-C", str(root), "init", "-q"],
                               check=True, capture_output=True)
                subprocess.run(
                    ["git", "-C", str(root), "config", "user.email",
                     "probe@invalid.local"], check=True, capture_output=True)
                subprocess.run(
                    ["git", "-C", str(root), "config", "user.name",
                     "Runvori Probe"], check=True, capture_output=True)
                (root / "tracked.txt").write_text("before\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(root), "add", "--", "tracked.txt"],
                    check=True, capture_output=True)
                subprocess.run(
                    ["git", "-C", str(root), "commit", "-qm", "baseline"],
                    check=True, capture_output=True)
                (root / "tracked.txt").write_text("after\n", encoding="utf-8")
                (root / "untracked.txt").write_text("new\n", encoding="utf-8")
                page = run_results.run_artifacts(
                    startup.events.connection, "run")
                changed = [item for item in page["items"]
                           if item["source"] == "git_worktree_snapshot"
                           and item["path"] == "tracked.txt"]
                untracked = [item for item in page["items"]
                             if item["source"] == "git_worktree_snapshot"
                             and item["path"] == "untracked.txt"]
                self.assertEqual(1, len(changed))
                self.assertEqual("unknown", changed[0]["attribution"])
                self.assertEqual("snapshot_at_read", changed[0]["source_status"])
                self.assertTrue(changed[0]["content_available"])
                content = run_results.artifact_content(
                    startup.events.connection,
                    changed[0]["artifact_id"])["data"]
                self.assertIn(b"before", content)
                self.assertIn(b"after", content)
                self.assertEqual(1, len(untracked))
                self.assertFalse(untracked[0]["content_available"])
                self.assertEqual("untracked_content_not_captured",
                                 untracked[0]["reason"])

    def test_invalid_artifact_id_and_range_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                with self.assertRaisesRegex(
                        Exception, "invalid_artifact_id"):
                    run_results.artifact_content(
                        startup.events.connection, "../../workspace")
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, output="abc")
                self._finish(startup, epoch)
                artifact = self._artifact(
                    startup, "command_output", "command_execution")
                with self.assertRaisesRegex(Exception, "invalid_range"):
                    run_results.artifact_content(
                        startup.events.connection, artifact["artifact_id"],
                        offset=artifact["size_bytes"] + 1)
