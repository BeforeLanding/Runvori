"""Real HostRuntime crash/restart at four cut points, with real process trees."""

import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from ctypes import wintypes
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.process_identity import (
    ISOLATED_OUTCOMES, probe_process_identity,
)


PEER = Path(__file__).with_name("host_peer.py")
CUT_MARKERS = {
    "starting": "cut",
    "running": "driver_cut",
    "waiting_approval": "driver_cut",
    "approval_dispatched": "cut",
    "interrupting": "cut",
}


def _creation_time(pid: int) -> int:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME)]
    api.GetProcessTimes.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError("process_open_failed")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(
                handle, *[ctypes.byref(value) for value in values]):
            raise OSError("process_times_failed")
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    finally:
        api.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "Windows Host process tree")
class HostRestartTests(unittest.TestCase):
    def _spawn(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
        return subprocess.Popen(
            [sys.executable, str(PEER), *[str(item) for item in args]],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def _wait_file(self, path: Path, process, timeout: float = 30) -> str:
        deadline = time.monotonic() + timeout
        while not path.exists():
            if process.poll() is not None:
                self.fail(f"Host peer exited early: {process.returncode}")
            if time.monotonic() >= deadline:
                self.fail(f"marker timeout: {path.name}")
            time.sleep(0.02)
        while True:
            try:
                return path.read_text(encoding="utf-8")
            except PermissionError:
                # Windows can refuse the first open right after the atomic
                # rename (scanner/indexer holds it briefly). That is a test
                # race, not a Host result: retry until the timeout.
                if time.monotonic() >= deadline:
                    self.fail(f"marker unreadable: {path.name}")
                time.sleep(0.02)

    def _wait_isolated(self, identities, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while True:
            outcomes = [probe_process_identity(pid, created)
                        for pid, created in identities]
            if all(outcome in ISOLATED_OUTCOMES for outcome in outcomes):
                return
            if time.monotonic() >= deadline:
                self.fail(f"process tree not isolated: {outcomes}")
            time.sleep(0.05)

    def _start_report(self, data_dir: Path, markers: Path):
        process = self._spawn("report", data_dir, markers)
        try:
            process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)
        for error in ("report_error", "report_stop_error"):
            path = markers / error
            if path.exists():
                self.fail(f"{error}: {path.read_text(encoding='utf-8')}")
        return (json.loads((markers / "report_started").read_text(encoding="utf-8")),
                json.loads((markers / "report.json").read_text(encoding="utf-8")),
                json.loads((markers / "report_stopped").read_text(encoding="utf-8")))

    def _crash(self, scenario: str):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            base = Path(temp)
            data_dir, markers = base / "data", base / "markers"
            data_dir.mkdir()
            markers.mkdir()
            process = self._spawn("crash", scenario, data_dir, markers)
            try:
                started = json.loads(self._wait_file(
                    markers / "started", process))
                self._wait_file(markers / "driver_accepted", process)
                self._wait_file(markers / CUT_MARKERS[scenario], process)
                if scenario == "interrupting":
                    self._wait_file(markers / "driver_interrupted", process)
                if scenario == "approval_dispatched":
                    self._wait_file(markers / "driver_decided", process)
                if (markers / "driver_error").exists():
                    self.fail((markers / "driver_error").read_text(encoding="utf-8"))

                peer_pid = int(self._wait_file(
                    markers / "cut.peer", process))
                leaf_pid = int(self._wait_file(
                    markers / "leaf.pid", process))
                identities = [
                    (started["process"]["pid"], started["process"]["created_at"]),
                    (peer_pid, _creation_time(peer_pid)),
                    (leaf_pid, _creation_time(leaf_pid)),
                ]
                old_epoch = started["epoch"]
                process.kill()
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

            self._wait_isolated(identities)
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(db.execute(
                    "SELECT epoch FROM journal_meta WHERE singleton=1"
                ).fetchone()[0], old_epoch)

            report_markers = base / "report"
            report_markers.mkdir()
            _, report, stopped = self._start_report(data_dir, report_markers)
            self._assert_recovered(scenario, old_epoch, report)
            self.assertEqual(stopped["epoch"], old_epoch + 2)

            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(db.execute(
                    "SELECT epoch FROM journal_meta WHERE singleton=1"
                ).fetchone()[0], old_epoch + 2)
                self.assertEqual(dict(db.execute(
                    "SELECT process_epoch, state FROM upstream_epochs"
                ).fetchall())[old_epoch + 2], "unlaunched")

            cycle_markers = base / "cycle"
            cycle_markers.mkdir()
            cycle_started, cycle_report, cycle_stopped = self._start_report(
                data_dir, cycle_markers)
            self.assertEqual(cycle_started["epoch"], old_epoch + 3)
            self.assertEqual(cycle_stopped["epoch"], old_epoch + 4)
            names = [event["type"] for event in cycle_report["events"]]
            self.assertEqual(names.count("run.created"), 1)
            self.assertEqual(cycle_report["runs"][0]["status"], "interrupted")
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(dict(db.execute(
                    "SELECT process_epoch, state FROM upstream_epochs"
                ).fetchall())[old_epoch + 4], "unlaunched")

    def _assert_recovered(self, scenario: str, old_epoch: int, report: dict):
        epochs = {row["process_epoch"]: row["state"]
                  for row in report["epochs"]}
        self.assertEqual(epochs[old_epoch], "recorded")
        self.assertEqual(epochs[old_epoch + 1], "recorded")
        self.assertEqual(report["active_slot"], [])
        self.assertIsNone(report["snapshot"]["active_run"])
        runs = {row["run_id"]: row for row in report["runs"]}
        self.assertEqual(len(runs), 1)
        run = next(iter(runs.values()))
        self.assertEqual(run["status"], "interrupted")
        self.assertIsNotNone(run["terminal_event_seq"])
        names = [event["type"] for event in report["events"]]
        self.assertEqual(names.count("run.created"), 1)
        interrupted = [event for event in report["events"]
                       if event["type"] == "run.interrupted"]
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["payload"]["reason"], "host_restart")

        operations = {row["request_id"]: row for row in report["run_operations"]}
        if scenario == "starting":
            self.assertEqual(operations["run-op"]["status"], "unknown")
            self.assertEqual(operations["run-op"]["outcome"],
                             "dispatch_unconfirmed")
            self.assertNotIn("run.started", names)
            self.assertEqual(report["upstream_turns"], [])
        else:
            self.assertEqual(operations["run-op"]["status"], "confirmed")
            self.assertEqual(operations["run-op"]["outcome"], "turn_started")
            self.assertIn("run.started", names)
            self.assertEqual(len(report["upstream_turns"]), 1)
        if scenario == "waiting_approval":
            self.assertEqual([row["status"] for row in report["approvals"]],
                             ["expired"])
            self.assertEqual(report["snapshot"]["open_approval_ids"], [])
            self.assertEqual(report["approval_operations"], [])
            self.assertEqual(report["snapshot"]["active_run"], None)
        if scenario == "approval_dispatched":
            self.assertEqual(len(report["approvals"]), 1)
            approval = report["approvals"][0]
            self.assertEqual(approval["status"], "unknown")
            self.assertEqual(approval["decision"], "accept")
            self.assertIn(approval["approval_id"],
                          report["snapshot"]["open_approval_ids"])
            self.assertEqual(len(report["approval_operations"]), 1)
            self.assertEqual(report["approval_operations"][0]["status"],
                             "unknown")
            self.assertEqual(report["approval_operations"][0]["outcome"],
                             "dispatch_unconfirmed")
            self.assertNotIn("turn.completed", names)
            statuses = [event["payload"].get("status")
                        for event in report["events"]
                        if event["type"] == "approval.updated"]
            self.assertNotIn("resolved", statuses)
        if scenario == "interrupting":
            self.assertEqual(len(report["interrupt_operations"]), 1)
            self.assertEqual(report["interrupt_operations"][0]["status"],
                             "unknown")
            self.assertEqual(report["interrupt_operations"][0]["outcome"],
                             "dispatch_unconfirmed")
            self.assertNotIn("turn.completed", names)
        old_pid = next(row["pid"] for row in report["processes"]
                       if row["process_epoch"] == old_epoch)
        new_pid = next(row["pid"] for row in report["processes"]
                       if row["process_epoch"] == old_epoch + 1)
        self.assertNotEqual(old_pid, new_pid)

    def test_starting_cut_restarts_and_never_reruns(self):
        self._crash("starting")

    def test_running_cut_restarts_and_never_reruns(self):
        self._crash("running")

    def test_waiting_approval_cut_expires_the_approval(self):
        self._crash("waiting_approval")

    def test_approval_dispatched_cut_keeps_dispatch_unknown(self):
        self._crash("approval_dispatched")

    def test_interrupting_cut_keeps_dispatch_unknown(self):
        self._crash("interrupting")

    def test_upstream_fault_interrupts_the_run_and_ends_the_host(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            base = Path(temp)
            data_dir, markers = base / "data", base / "markers"
            data_dir.mkdir()
            markers.mkdir()
            process = self._spawn("crash", "upstream_fault", data_dir, markers)
            try:
                process.wait(timeout=60)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            for error in ("start_error", "run_error", "stop_error"):
                path = markers / error
                if path.exists():
                    self.fail(f"{error}: {path.read_text(encoding='utf-8')}")
            run_result = json.loads(
                (markers / "run_result").read_text(encoding="utf-8"))
            self.assertEqual(run_result["reason"], "upstream_recovered")
            self.assertEqual(run_result["cause"], "transport_fault")
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                status, terminal = db.execute(
                    "SELECT status, terminal_event_seq FROM runs").fetchone()
                self.assertEqual(status, "interrupted")
                event = json.loads(db.execute(
                    "SELECT event_json FROM events WHERE seq=?",
                    (terminal,)).fetchone()[0])
                self.assertEqual(event["payload"]["reason"], "upstream_exit")
                self.assertIsNone(db.execute(
                    "SELECT run_id FROM active_slot").fetchone())
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM events WHERE event_json LIKE "
                    "'%run.created%'").fetchone()[0], 1)
            # The Host exited after recovery; a new Host starts cleanly.
            cycle = base / "cycle"
            cycle.mkdir()
            _, report, stopped = self._start_report(data_dir, cycle)
            self.assertEqual(report["runs"][0]["status"], "interrupted")
            self.assertGreater(stopped["epoch"], report["epoch"])


if __name__ == "__main__":
    unittest.main()
