"""Opt-in real Codex Host crash/restart at five cut points.

Set RUNVORI_REAL_HOST_TESTS=1, RUNVORI_REAL_CODEX to the absolute pinned
binary, and RUNVORI_REAL_CODEX_HOME to a dedicated logged-in Codex home.
These tests launch real model turns and are never part of the offline pass.
"""

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
from runvori.codex.version_probe import PINNED_CODEX, require_codex_version
from runvori.infrastructure.process_identity import (
    ISOLATED_OUTCOMES, probe_process_identity,
)

PEER = Path(__file__).with_name("host_real_peer.py")
CUT_MARKERS = {
    "starting": ["cut"],
    "running": ["driver_cut"],
    "waiting_approval": ["driver_cut"],
    "approval_dispatched": ["cut", "driver_decided"],
    "interrupting": ["cut", "driver_interrupted"],
}
RELAY_SCENARIOS = {"starting", "approval_dispatched", "interrupting"}
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x1
STILL_ACTIVE = 259


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME)]
    api.GetProcessTimes.restype = wintypes.BOOL
    api.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    api.Process32FirstW.restype = wintypes.BOOL
    api.Process32NextW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    api.Process32NextW.restype = wintypes.BOOL
    return api


def _creation_time(pid: int) -> int:
    api = _kernel32()
    handle = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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


def _process_table() -> dict[int, dict]:
    api = _kernel32()
    handle = api.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError("process_snapshot_failed")
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        rows = {}
        if not api.Process32FirstW(handle, ctypes.byref(entry)):
            return rows
        while True:
            rows[entry.th32ProcessID] = {
                "ppid": entry.th32ParentProcessID,
                "exe": entry.szExeFile,
            }
            if not api.Process32NextW(handle, ctypes.byref(entry)):
                return rows
    finally:
        api.CloseHandle(handle)


def _observed_descendants(root_pid: int) -> list[dict]:
    observed: dict[int, str] = {}
    for _ in range(3):
        table = _process_table()
        children: dict[int, list[int]] = {}
        for pid, row in table.items():
            children.setdefault(row["ppid"], []).append(pid)
        pending = list(children.get(root_pid, []))
        seen = set()
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            observed[pid] = table[pid]["exe"]
            pending.extend(children.get(pid, []))
        time.sleep(0.1)
    results = []
    for pid, exe in sorted(observed.items()):
        try:
            created = _creation_time(pid)
        except OSError:
            continue
        results.append({"pid": pid, "exe": exe, "created_at": created})
    return results


def _terminate_observed(pid: int, created_at: int) -> None:
    api = _kernel32()
    handle = api.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(
                handle, *[ctypes.byref(value) for value in values]):
            return
        current = ((values[0].dwHighDateTime << 32)
                   | values[0].dwLowDateTime)
        if current == created_at:
            api.TerminateProcess(handle, 1)
    finally:
        api.CloseHandle(handle)


class RealCodexHostRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("RUNVORI_REAL_HOST_TESTS") != "1":
            raise unittest.SkipTest("RUNVORI_REAL_HOST_TESTS is not enabled")
        codex = os.environ.get("RUNVORI_REAL_CODEX")
        home = os.environ.get("RUNVORI_REAL_CODEX_HOME")
        if not codex or not Path(codex).is_file() or not Path(codex).is_absolute():
            raise AssertionError("RUNVORI_REAL_CODEX must be an absolute file")
        if not home or not Path(home).is_dir() or not Path(home).is_absolute():
            raise AssertionError(
                "RUNVORI_REAL_CODEX_HOME must be an absolute directory")
        cls.codex = Path(codex).resolve()
        cls.codex_home = Path(home).resolve()
        env = dict(os.environ, CODEX_HOME=str(cls.codex_home))
        for key in list(env):
            if any(word in key.upper()
                   for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
                del env[key]
        require_codex_version(
            str(cls.codex), PINNED_CODEX, env, cls.codex_home)
        evidence = os.environ.get("RUNVORI_REAL_EVIDENCE_DIR")
        cls.evidence_dir = None
        if evidence:
            cls.evidence_dir = Path(evidence).resolve()
            cls.evidence_dir.mkdir(parents=True, exist_ok=True)

    def _spawn(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
        return subprocess.Popen(
            [sys.executable, str(PEER), *[str(item) for item in args]],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def _wait_file(self, path: Path, process, timeout: float = 240) -> str:
        deadline = time.monotonic() + timeout
        while not path.exists():
            if process.poll() is not None:
                self.fail(f"real Host peer exited early: {process.returncode}")
            if time.monotonic() >= deadline:
                self.fail(f"marker timeout: {path.name}")
            time.sleep(0.02)
        return path.read_text(encoding="utf-8")

    def _wait_isolated(self, identities, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while True:
            outcomes = [probe_process_identity(pid, created)
                        for pid, created in identities]
            if all(outcome in ISOLATED_OUTCOMES for outcome in outcomes):
                return
            if time.monotonic() >= deadline:
                for (pid, created), outcome in zip(identities, outcomes):
                    if outcome not in ISOLATED_OUTCOMES:
                        _terminate_observed(pid, created)
                self.fail(f"real process tree not isolated: {outcomes}")
            time.sleep(0.05)

    def _start_report(self, data_dir: Path, markers: Path, *, replay: bool):
        args = ["report", data_dir, markers, self.codex, self.codex_home]
        if replay:
            args.append("--replay-approval")
        process = self._spawn(*args)
        try:
            process.wait(timeout=120)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)
        for error in ("report_error", "report_stop_error"):
            path = markers / error
            if path.exists():
                self.fail(f"{error}: {path.read_text(encoding='utf-8')}")
        started = json.loads(
            (markers / "report_started").read_text(encoding="utf-8"))
        report = json.loads(
            (markers / "report.json").read_text(encoding="utf-8"))
        stopped = json.loads(
            (markers / "report_stopped").read_text(encoding="utf-8"))
        replay_result = None
        path = markers / "replay_result"
        if path.exists():
            replay_result = json.loads(path.read_text(encoding="utf-8"))
        return started, report, stopped, replay_result

    def _crash(self, scenario: str):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            base = Path(temp)
            data_dir, markers = base / "data", base / "markers"
            data_dir.mkdir()
            markers.mkdir()
            process = self._spawn(
                "crash", scenario, data_dir, markers,
                self.codex, self.codex_home)
            try:
                started = json.loads(self._wait_file(
                    markers / "started", process, timeout=180))
                self._wait_file(markers / "driver_accepted", process)
                for name in CUT_MARKERS[scenario]:
                    self._wait_file(markers / name, process)
                if (markers / "driver_error").exists():
                    self.fail((markers / "driver_error").read_text(
                        encoding="utf-8"))
                if (markers / "driver_timeout").exists():
                    self.fail("real driver timed out")

                cut_kind = None
                relay_pid = None
                if scenario in RELAY_SCENARIOS:
                    cut_kind = (markers / "cut").read_text(encoding="utf-8")
                    relay_pid = int((markers / "cut.peer").read_text(
                        encoding="utf-8"))
                    self.assertTrue((markers / "codex.pid").is_file())
                    self.assertIn(
                        cut_kind,
                        {"turn_start", "interrupt", "approval"})
                    relay_error = markers / "relay_error"
                    if relay_error.exists():
                        self.fail("relay_error: " + relay_error.read_text(
                            encoding="utf-8"))
                relay_answers = 0
                if scenario == "approval_dispatched":
                    answers = (markers / "answers").read_text(
                        encoding="utf-8").splitlines()
                    relay_answers = len(answers)
                    self.assertEqual(answers, ["answer_forwarded"])

                descendants = _observed_descendants(process.pid)
                observed_pids = {row["pid"] for row in descendants}
                exe_names = {row["exe"].lower() for row in descendants}
                self.assertIn("codex.exe", exe_names)
                if relay_pid is not None:
                    self.assertIn(relay_pid, observed_pids)

                identities = []
                seen = set()
                launcher = (started["process"]["pid"],
                            started["process"]["created_at"])
                for identity in [launcher, *[
                        (row["pid"], row["created_at"])
                        for row in descendants]]:
                    if identity[0] not in seen:
                        seen.add(identity[0])
                        identities.append(identity)
                old_epoch = started["epoch"]
                process.kill()
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

            self._wait_isolated(identities)
            outcomes = [probe_process_identity(pid, created)
                        for pid, created in identities]
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(db.execute(
                    "SELECT epoch FROM journal_meta WHERE singleton=1"
                ).fetchone()[0], old_epoch)

            report_markers = base / "report"
            report_markers.mkdir()
            report_started, report, stopped, replay_result = \
                self._start_report(
                    data_dir, report_markers,
                    replay=scenario in ("waiting_approval",
                                        "approval_dispatched"))
            self.assertEqual(report_started["epoch"], old_epoch + 1)
            self.assertEqual(report_started["host_id"], started["host_id"])
            self.assertEqual(stopped["epoch"], old_epoch + 2)
            self._assert_recovered_real(
                scenario, old_epoch, started, report, replay_result)

            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(dict(db.execute(
                    "SELECT process_epoch, state FROM upstream_epochs"
                ).fetchall())[old_epoch + 2], "unlaunched")

            cycle_markers = base / "cycle"
            cycle_markers.mkdir()
            cycle_started, cycle_report, cycle_stopped, _ = \
                self._start_report(
                    data_dir, cycle_markers, replay=False)
            self.assertEqual(cycle_started["epoch"], old_epoch + 3)
            self.assertEqual(cycle_stopped["epoch"], old_epoch + 4)
            names = [event["type"] for event in cycle_report["events"]]
            self.assertEqual(names.count("run.created"), 1)
            self.assertEqual(cycle_report["runs"][0]["status"], "interrupted")
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(dict(db.execute(
                    "SELECT process_epoch, state FROM upstream_epochs"
                ).fetchall())[old_epoch + 4], "unlaunched")

            if self.evidence_dir is not None:
                evidence = {
                    "scenario": scenario,
                    "codex_version": PINNED_CODEX,
                    "upstream": started["upstream"],
                    "thread_policy": started["policy"],
                    "cut_kind": cut_kind,
                    "relay_answers": relay_answers,
                    "old_epoch": old_epoch,
                    "host_id": started["host_id"],
                    "launcher": started["process"],
                    "descendants": descendants,
                    "outcomes": [
                        {"pid": pid, "created_at": created,
                         "outcome": outcome}
                        for (pid, created), outcome in zip(identities, outcomes)
                    ],
                    "report_epoch": report_started["epoch"],
                    "created_event_count": names.count("run.created"),
                    "recovered_run": report["runs"],
                    "approvals": report["approvals"],
                    "approval_operations": report["approval_operations"],
                    "run_operations": report["run_operations"],
                    "interrupt_operations": report["interrupt_operations"],
                    "replay_result": replay_result,
                    "events": [event["type"] for event in report["events"]],
                }
                (self.evidence_dir / f"{scenario}.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

    def _assert_recovered_real(self, scenario, old_epoch, started, report,
                               replay_result):
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
            self.assertIn("approval_expired",
                          (replay_result or {}).get("error", ""))
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
            self.assertEqual(
                (replay_result or {}).get("result", {}).get("status"),
                "unknown")
            self.assertEqual(
                (replay_result or {}).get("result", {}).get("outcome"),
                "dispatch_unconfirmed")
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
        self.assertEqual(report["host_id"], started["host_id"])

    def test_starting_cut_with_real_codex(self):
        self._crash("starting")

    def test_running_cut_with_real_codex(self):
        self._crash("running")

    def test_waiting_approval_cut_with_real_codex(self):
        self._crash("waiting_approval")

    def test_approval_dispatched_cut_with_real_codex(self):
        self._crash("approval_dispatched")

    def test_interrupting_cut_with_real_codex(self):
        self._crash("interrupting")


if __name__ == "__main__":
    unittest.main()
