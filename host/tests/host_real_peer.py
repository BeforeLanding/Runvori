"""Run the real HostRuntime against the pinned real Codex for crash tests.

Test apparatus only.  It creates a disposable fixture and a real Codex thread
through a direct StdioClient, seeds the Host database, then drives one run
through the product mailbox.  The parent test kills this process at a marker.

Modes:
- ``crash <scenario> <data_dir> <marker_dir> <codex> <codex_home>``
- ``report <data_dir> <marker_dir> <codex> <codex_home> [--replay-approval]``
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from runvori.codex.execution_probe import (
    ACCEPT_PROMPT, prepare_fixture,
)
from runvori.codex.version_probe import PINNED_CODEX, require_codex_version
from runvori.host import HostConfig, HostRuntime
from runvori.infrastructure.locked_journal import LockedHostJournals

from host_peer import approval_version, mark, process_row, report

ROOT = Path(__file__).resolve().parents[2]
RELAY = Path(__file__).with_name("codex_relay.py")

RUNNING_PROMPT = (
    "This is a disposable Runvori protocol probe. Do not use any tools, "
    "commands, files, or network access. Write a detailed numbered list from "
    "1 to 300, one item per line, each with a short sentence of explanation. "
    "Keep writing until you reach 300."
)
SCENARIOS = {
    "starting": {
        "relay": "hold_turn_start", "policy": "on-request",
        "prompt": RUNNING_PROMPT,
    },
    "running": {
        "relay": None, "policy": "on-request", "prompt": RUNNING_PROMPT,
    },
    "waiting_approval": {
        "relay": None, "policy": "on-request", "prompt": ACCEPT_PROMPT,
    },
    "approval_dispatched": {
        "relay": "hold_after_approval", "policy": "on-request",
        "prompt": ACCEPT_PROMPT,
    },
    "interrupting": {
        "relay": "hold_after_interrupt", "policy": "on-request",
        "prompt": RUNNING_PROMPT,
    },
}


def real_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ, CODEX_HOME=str(codex_home))
    for key in list(env):
        if any(word in key.upper()
               for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
            del env[key]
    return env


def seed(data_dir: Path, fixture: Path) -> None:
    with LockedHostJournals(data_dir) as startup:
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0, expected_run_id=None,
            expected_run_version=None, reason="host_restart",
            recorded_at="2026-10-26T00:00:00Z")["epoch"]
        startup.register_workspace(
            "workspace", fixture, sandbox="workspace-write", epoch=epoch)
        startup.create_session_in_workspace(
            "session", "workspace", {}, epoch=epoch)


def ensure_thread(runtime: HostRuntime, marker_dir: Path) -> str:
    runtime.thread_starts.submit("session", "local", "thread-op")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        view = runtime.mailbox.get_session_thread(
            session_id="session", timeout=10)
        thread_id = view.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            mark(marker_dir / "driver_thread", thread_id)
            return thread_id
        time.sleep(0.02)
    raise RuntimeError("thread_start_timeout")


def config(data_dir: Path, fixture: Path, codex: Path,
           env: dict[str, str], *, relay_mode: str | None,
           marker_dir: Path | None = None) -> HostConfig:
    if relay_mode is None:
        command = [str(codex), "app-server", "--listen", "stdio://"]
        expected = PINNED_CODEX
    else:
        command = [
            sys.executable, str(RELAY), "--codex", str(codex),
            "--mode", relay_mode, "--marker-dir", str(marker_dir)]
        # The relay is test apparatus; its version is verified separately.
        expected = None
    return HostConfig(
        data_dir=data_dir, upstream_command=command, upstream_cwd=fixture,
        upstream_env=dict(env), poll_timeout=0.02,
        expected_upstream_version=expected)


def drive(runtime: HostRuntime, scenario: str, marker_dir: Path) -> None:
    try:
        mailbox = runtime.mailbox
        ensure_thread(runtime, marker_dir)
        snapshot = mailbox.get_session_snapshot(
            session_id="session", timeout=10)
        accepted = mailbox.start_run(
            session_id="session", actor_id="device", request_id="run-op",
            expected_session_version=snapshot.get("session_version", 0),
            input_text=SCENARIOS[scenario]["prompt"], timeout=10)
        mark(marker_dir / "driver_accepted",
             json.dumps(accepted, ensure_ascii=False))
        if scenario == "starting":
            return
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            snapshot = mailbox.get_session_snapshot(
                session_id="session", timeout=10)
            active = snapshot.get("active_run")
            if scenario in ("waiting_approval", "approval_dispatched"):
                open_ids = snapshot.get("open_approval_ids")
                if (active and active.get("status") == "waiting_approval"
                        and open_ids):
                    if scenario == "waiting_approval":
                        mark(marker_dir / "driver_cut", "waiting_approval")
                        return
                    approval_id = open_ids[0]
                    decided = mailbox.decide_approval(
                        approval_id=approval_id, actor_id="device",
                        request_id="approval-op",
                        expected_version=approval_version(mailbox, approval_id),
                        decision="allow_once", timeout=10)
                    mark(marker_dir / "driver_decided",
                         json.dumps(decided, ensure_ascii=False))
                    return
            elif active and active.get("status") == "running":
                if scenario == "running":
                    mark(marker_dir / "driver_cut", "running")
                    return
                if scenario == "interrupting":
                    mailbox.interrupt_run(
                        run_id=active["run_id"], actor_id="device",
                        request_id="interrupt-op",
                        expected_version=active["version"], timeout=10)
                    mark(marker_dir / "driver_interrupted", "sent")
                    return
            time.sleep(0.02)
        mark(marker_dir / "driver_timeout", scenario)
    except Exception as error:
        mark(marker_dir / "driver_error",
             type(error).__name__ + ":" + str(error))


def crash(scenario: str, data_dir: Path, marker_dir: Path,
          codex: Path, codex_home: Path) -> int:
    env = real_env(codex_home)
    require_codex_version(str(codex), PINNED_CODEX, env, ROOT)
    fixture = prepare_fixture(ROOT, data_dir / "workspace")
    seed(data_dir, fixture)
    relay_mode = SCENARIOS[scenario]["relay"]
    runtime = HostRuntime(config(
        data_dir, fixture, codex, env,
        relay_mode=relay_mode, marker_dir=marker_dir))
    try:
        started = runtime.start()
    except Exception as error:
        mark(marker_dir / "start_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "started", json.dumps({
        "epoch": runtime.epoch,
        "host_id": started["host_id"],
        "recovered_epoch": started["recovered_epoch"],
        "process": process_row(runtime),
        "scenario": scenario,
        "thread_creation": "product_thread_start",
        "policy": SCENARIOS[scenario]["policy"],
        "upstream": "relay" if relay_mode else "direct",
        "codex_version": PINNED_CODEX,
    }, ensure_ascii=False))
    driver = threading.Thread(
        target=drive, args=(runtime, scenario, marker_dir), daemon=True)
    driver.start()
    stop = threading.Event()
    try:
        result = runtime.run(stop)
    except Exception as error:
        mark(marker_dir / "run_error", type(error).__name__ + ":" + str(error))
        result = {"reason": "error"}
    mark(marker_dir / "run_result", json.dumps(result, ensure_ascii=False))
    try:
        stopped = runtime.stop()
        mark(marker_dir / "stopped", json.dumps(stopped, ensure_ascii=False))
    except Exception as error:
        mark(marker_dir / "stop_error", type(error).__name__ + ":" + str(error))
        return 1
    return 0


def _replay(runtime: HostRuntime, marker_dir: Path) -> None:
    db = runtime.journals.events.connection
    row = db.execute(
        "SELECT approval_id, version, decision FROM approvals").fetchone()
    if row is None:
        mark(marker_dir / "replay_result", json.dumps({"status": "no_approval"}))
        return
    decision = "allow_once" if row["decision"] == "accept" else "deny"
    stop = threading.Event()
    outcome: dict = {}

    def caller():
        try:
            outcome["result"] = runtime.mailbox.decide_approval(
                approval_id=row["approval_id"], actor_id="device",
                request_id="approval-replay",
                expected_version=row["version"], decision=decision,
                timeout=10)
        except Exception as error:
            outcome["error"] = type(error).__name__ + ":" + str(error)
        finally:
            stop.set()

    worker = threading.Thread(target=caller, daemon=True)
    worker.start()
    result = runtime.run(stop)
    worker.join(timeout=5)
    mark(marker_dir / "replay_result", json.dumps({
        "run_reason": result.get("reason"), **outcome,
    }, ensure_ascii=False))


def prepare_only(scenario: str, data_dir: Path, marker_dir: Path,
                 codex: Path, codex_home: Path) -> int:
    env = real_env(codex_home)
    require_codex_version(str(codex), PINNED_CODEX, env, ROOT)
    fixture = prepare_fixture(ROOT, data_dir / "workspace")
    seed(data_dir, fixture)
    relay_mode = SCENARIOS[scenario]["relay"]
    runtime = HostRuntime(config(
        data_dir, fixture, codex, env,
        relay_mode=relay_mode, marker_dir=marker_dir))
    try:
        started = runtime.start()
    except Exception as error:
        mark(marker_dir / "prepare_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "prepared", json.dumps({
        "epoch": runtime.epoch,
        "host_id": started["host_id"],
        "process": process_row(runtime),
        "scenario": scenario,
        "upstream": "relay" if relay_mode else "direct",
        "codex_version": PINNED_CODEX,
    }, ensure_ascii=False))
    stop = threading.Event()
    outcome: dict = {}

    def start_thread():
        try:
            outcome["thread_id"] = ensure_thread(runtime, marker_dir)
        except Exception as error:
            outcome["error"] = type(error).__name__ + ":" + str(error)
        finally:
            stop.set()

    worker = threading.Thread(target=start_thread, daemon=True)
    worker.start()
    try:
        run_result = runtime.run(stop)
        worker.join(timeout=5)
        if "error" in outcome:
            mark(marker_dir / "prepare_thread_error", outcome["error"])
        else:
            mark(marker_dir / "prepare_thread", json.dumps({
                "thread_id": outcome.get("thread_id"),
                "run_reason": run_result.get("reason"),
            }, ensure_ascii=False))
        report(runtime, marker_dir, "prepare_report.json")
        stopped = runtime.stop()
    except Exception as error:
        mark(marker_dir / "prepare_stop_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "prepare_stopped",
         json.dumps(stopped, ensure_ascii=False))
    return 0 if "error" not in outcome else 1


def report_only(data_dir: Path, marker_dir: Path, codex: Path,
                codex_home: Path, *, replay: bool) -> int:
    env = real_env(codex_home)
    require_codex_version(str(codex), PINNED_CODEX, env, ROOT)
    fixture = data_dir / "workspace"
    runtime = HostRuntime(config(
        data_dir, fixture, codex, env, relay_mode=None))
    try:
        started = runtime.start()
    except Exception as error:
        mark(marker_dir / "report_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "report_started", json.dumps({
        "epoch": runtime.epoch,
        "host_id": started["host_id"],
        "recovered_epoch": started["recovered_epoch"],
        "recovered_run": started["recovered_run"],
        "process": process_row(runtime),
        "codex_version": PINNED_CODEX,
    }, ensure_ascii=False))
    try:
        if replay:
            _replay(runtime, marker_dir)
        report(runtime, marker_dir, "report.json")
        stopped = runtime.stop()
    except Exception as error:
        mark(marker_dir / "report_stop_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "report_stopped",
         json.dumps(stopped, ensure_ascii=False))
    return 0


def main() -> int:
    if len(sys.argv) < 6:
        return 2
    mode = sys.argv[1]
    if mode == "crash" and len(sys.argv) >= 7:
        scenario = sys.argv[2]
        if scenario not in SCENARIOS:
            return 2
        data_dir = Path(sys.argv[3])
        marker_dir = Path(sys.argv[4])
        codex = Path(sys.argv[5]).resolve()
        codex_home = Path(sys.argv[6]).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        marker_dir.mkdir(parents=True, exist_ok=True)
        return crash(scenario, data_dir, marker_dir, codex, codex_home)
    if mode == "prepare" and len(sys.argv) >= 7:
        scenario = sys.argv[2]
        if scenario not in SCENARIOS:
            return 2
        data_dir = Path(sys.argv[3])
        marker_dir = Path(sys.argv[4])
        codex = Path(sys.argv[5]).resolve()
        codex_home = Path(sys.argv[6]).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        marker_dir.mkdir(parents=True, exist_ok=True)
        return prepare_only(
            scenario, data_dir, marker_dir, codex, codex_home)
    if mode == "report" and len(sys.argv) >= 6:
        data_dir = Path(sys.argv[2])
        marker_dir = Path(sys.argv[3])
        codex = Path(sys.argv[4]).resolve()
        codex_home = Path(sys.argv[5]).resolve()
        marker_dir.mkdir(parents=True, exist_ok=True)
        return report_only(
            data_dir, marker_dir, codex, codex_home,
            replay="--replay-approval" in sys.argv)
    return 2


if __name__ == "__main__":
    sys.exit(main())
