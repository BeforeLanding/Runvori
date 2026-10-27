"""Persisted command evidence and required-verification results."""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from runvori.application import run_results
from runvori.codex.event_handoff import route_observed_event
from runvori.host import HostConfig, HostRuntime
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class RunResultsTests(unittest.TestCase):
    @staticmethod
    @contextmanager
    def _opened(temp: str, verification_commands=None):
        with LockedHostJournals(Path(temp)) as startup:
            epoch = startup.recover_host_after_isolation(
                old_process_epoch=0, expected_run_id=None,
                expected_run_version=None, reason="host_restart",
                recorded_at="2026-10-27T00:00:00Z")["epoch"]
            startup.register_workspace(
                "ws", Path(temp), sandbox="workspace-write", epoch=epoch,
                verification_commands=verification_commands)
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
    def _event(startup, epoch, method: str, params: dict, second: int):
        route_observed_event(
            startup, {"method": method, "params": params}, epoch=epoch,
            recorded_at=f"2026-10-27T00:00:{second:02d}Z")

    def _command(self, startup, epoch, *, item_id: str, command: str,
                 second: int, output: str | None = None,
                 exit_code: int | None = 0, status: str = "completed",
                 output_repeats: int = 0, thread_id: str = "thread_demo",
                 turn_id: str = "turn_demo") -> None:
        params = {
            "threadId": thread_id, "turnId": turn_id,
            "startedAtMs": 1700000000000 + second * 1000,
            "item": {"id": item_id, "type": "commandExecution",
                     "command": command, "cwd": str(Path(startup.data_dir)),
                     "status": "inProgress"}}
        self._event(startup, epoch, "item/started", params, second)
        if output_repeats:
            for index in range(output_repeats):
                self._event(
                    startup, epoch,
                    "item/commandExecution/outputDelta",
                    {"threadId": thread_id, "turnId": turn_id,
                     "itemId": item_id, "delta": "x" * 8192}, second + index)
        elif output is not None:
            self._event(
                startup, epoch, "item/commandExecution/outputDelta",
                {"threadId": thread_id, "turnId": turn_id,
                 "itemId": item_id, "delta": output}, second)
        item = {"id": item_id, "type": "commandExecution",
                "command": command, "cwd": str(Path(startup.data_dir)),
                "status": status, "durationMs": 12}
        if exit_code is not None:
            item["exitCode"] = exit_code
        self._event(
            startup, epoch, "item/completed",
            {"threadId": thread_id, "turnId": turn_id,
             "completedAtMs": 1700000000000 + second * 1000 + 120,
             "item": item}, second + 1)

    def _finish(self, startup, epoch, second: int = 90,
                thread_id: str = "thread_demo",
                turn_id: str = "turn_demo") -> None:
        self._event(
            startup, epoch, "turn/completed",
            {"threadId": thread_id,
             "turn": {"id": turn_id, "status": "completed"}}, second)

    def test_completed_run_can_carry_failed_verification_and_survive_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp, [
                    {"key": "ok", "command": "python --version"},
                    {"key": "bad", "command": "exit 7"}]) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, item_id="ok", command="python --version",
                              second=4, output="3.12\n", exit_code=0)
                self._command(startup, epoch, item_id="bad", command="exit 7",
                              second=6, output="exit 7\n", exit_code=7,
                              status="failed")
                self._finish(startup, epoch, second=8)
                result = run_results.run_results(startup.events.connection, "run")
                self.assertEqual("completed", result["status"])
                self.assertTrue(result["terminal"])
                self.assertTrue(result["verification"]["configured"])
                self.assertEqual("failed", result["verification"]["summary"])
                items = {item["key"]: item for item in result["verification"]["items"]}
                self.assertEqual("passed", items["ok"]["status"])
                self.assertEqual(0, items["ok"]["exit_code"])
                self.assertEqual("failed", items["bad"]["status"])
                self.assertEqual(7, items["bad"]["exit_code"])
                self.assertEqual("complete", items["bad"]["output_state"])
                self.assertEqual(2, result["command_count"])
            with LockedHostJournals(Path(temp)) as reopened:
                again = run_results.run_results(reopened.events.connection, "run")
                self.assertEqual("failed", again["verification"]["summary"])
                self.assertEqual("completed", again["status"])
                self.assertEqual(2, again["command_count"])

    def test_workspace_update_does_not_rewrite_an_existing_run(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp, [
                    {"key": "python", "command": "python --version"}]) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, item_id="cmd",
                              command="python --version", second=4,
                              output="3.12\n", exit_code=0)
                self._finish(startup, epoch, second=6)
                session_version = startup.events.snapshot(
                    "session")["session_version"]
                startup.register_workspace(
                    "ws", Path(temp), sandbox="workspace-write", epoch=epoch,
                    verification_commands=[
                        {"key": "failure", "command": "exit 7"}])
                startup.events.reserve_run_request(
                    "session", "run2", actor_id="device", request_id="start2",
                    expected_session_version=session_version, input_text="again",
                    recorded_at="2026-10-27T00:00:10Z")
                startup.events.claim_run_dispatch(
                    "device", "start2", process_epoch=epoch,
                    recorded_at="2026-10-27T00:00:11Z")
                startup.events.confirm_run_started(
                    "device", "start2", process_epoch=epoch,
                    thread_id="thread_demo", turn_id="turn_demo2",
                    recorded_at="2026-10-27T00:00:12Z")
                self._finish(startup, epoch, second=20,
                             turn_id="turn_demo2")
                first = run_results.run_results(
                    startup.events.connection, "run")
                self.assertEqual("passed", first["verification"]["summary"])
                self.assertEqual(["python"], [
                    item["key"] for item in first["verification"]["items"]])
                second = run_results.run_results(
                    startup.events.connection, "run2")
                self.assertEqual("not_run", second["verification"]["summary"])
                self.assertEqual(["failure"], [
                    item["key"] for item in second["verification"]["items"]])

    def test_unconfigured_workspace_is_explicit_and_command_survives_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, item_id="cmd", command="python --version",
                              second=4, output="3.12\n", exit_code=0)
                self._finish(startup, epoch, second=6)
                result = run_results.run_results(startup.events.connection, "run")
                self.assertFalse(result["verification"]["configured"])
                self.assertEqual("not_run", result["verification"]["summary"])
                self.assertEqual([], result["verification"]["items"])
                self.assertEqual(1, result["command_count"])
            with LockedHostJournals(Path(temp)) as reopened:
                commands = run_results.run_commands(
                    reopened.events.connection, "run")
                self.assertEqual(1, len(commands["items"]))
                self.assertEqual("cmd", commands["items"][0]["item_id"])
                self.assertEqual("complete", commands["items"][0]["output_state"])

    def test_not_run_missing_exit_and_truncated_output_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp, [
                    {"key": "never", "command": "git --version"},
                    {"key": "missing", "command": "exit 7"},
                    {"key": "long", "command": "python -c long"}]) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, item_id="missing", command="exit 7",
                              second=4, output="no exit\n", exit_code=None,
                              status="completed")
                self._command(startup, epoch, item_id="long",
                              command="python -c long", second=6,
                              exit_code=0, output_repeats=258)
                self._finish(startup, epoch, second=90)
                result = run_results.run_results(startup.events.connection, "run")
                self.assertEqual("unknown", result["verification"]["summary"])
                items = {item["key"]: item for item in result["verification"]["items"]}
                self.assertEqual("not_run", items["never"]["status"])
                self.assertEqual("unknown", items["missing"]["status"])
                self.assertFalse(items["missing"]["exit_code_present"])
                self.assertIsNone(items["missing"]["exit_code"])
                self.assertEqual("passed", items["long"]["status"])
                self.assertEqual("truncated", items["long"]["output_state"])
                self.assertGreater(items["long"]["output_dropped_bytes"], 0)
                commands = run_results.run_commands(
                    startup.events.connection, "run")["items"]
                missing = next(item for item in commands if item["item_id"] == "missing")
                self.assertFalse(missing["exit_code_present"])
                self.assertIsNone(missing["exit_code"])
                long = next(item for item in commands if item["item_id"] == "long")
                self.assertEqual("truncated", long["output_state"])
                self.assertGreater(long["output_dropped_bytes"], 0)

    def test_command_pages_are_stable_and_do_not_repeat_items(self):
        with tempfile.TemporaryDirectory() as temp:
            with self._opened(temp) as startup:
                epoch = startup.approvals.current_epoch()
                for index, item_id in enumerate(("c1", "c2", "c3")):
                    self._command(
                        startup, epoch, item_id=item_id, command=item_id,
                        second=4 + index * 2, output=item_id + "\n")
                self._finish(startup, epoch, second=20)
                first = run_results.run_commands(
                    startup.events.connection, "run", limit=2)
                self.assertEqual(["c1", "c2"],
                                 [item["item_id"] for item in first["items"]])
                self.assertTrue(first["has_more"])
                self.assertEqual("c2", first["next_cursor"])
                second = run_results.run_commands(
                    startup.events.connection, "run",
                    after_item_id=first["next_cursor"], limit=2)
                self.assertEqual(["c3"],
                                 [item["item_id"] for item in second["items"]])
                self.assertFalse(second["has_more"])
                self.assertIsNone(second["next_cursor"])
                with self.assertRaisesRegex(EventJournalError, "invalid_cursor"):
                    run_results.run_commands(
                        startup.events.connection, "run", after_item_id="missing")

    def test_host_runtime_start_keeps_results_queryable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self._opened(temp, [
                    {"key": "ok", "command": "python --version"}]) as startup:
                epoch = startup.approvals.current_epoch()
                self._command(startup, epoch, item_id="cmd",
                              command="python --version", second=4,
                              output="3.12\n", exit_code=0)
                self._finish(startup, epoch, second=6)
            fake = Path(__file__).with_name("fake_app_server.py").resolve()
            runtime = HostRuntime(HostConfig(
                data_dir=root,
                upstream_command=[sys.executable, str(fake),
                                  "host_api_no_approval"],
                upstream_cwd=root,
                upstream_env={"RUNVORI_FAKE_WORKSPACE": str(root)},
                poll_timeout=0.02))
            runtime.start()
            try:
                result = run_results.run_results(
                    runtime.journals.events.connection, "run")
                self.assertEqual("completed", result["status"])
                self.assertEqual("passed", result["verification"]["summary"])
                self.assertEqual(1, result["command_count"])
            finally:
                runtime.stop()
