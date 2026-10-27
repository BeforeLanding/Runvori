"""Run the real HostRuntime in a subprocess for crash/restart tests.

This is a test harness, not a product interface. It seeds one session, starts
the real Host, drives one run through the product mailbox, and writes markers
the parent test uses to kill the process at a chosen cut point.

Modes:
- ``crash <scenario> <data_dir> <marker_dir>``
- ``report <data_dir> <marker_dir>``
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

from runvori.host import HostConfig, HostRuntime
from runvori.infrastructure.locked_journal import LockedHostJournals

ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_app_server.py")
SCENARIO_MODES = {
    "starting": "host_turn_start_hang",
    "running": "host_running",
    "waiting_approval": "host_approval",
    "approval_dispatched": "host_approval_dispatch",
    "interrupting": "host_interrupt_hang",
    "upstream_fault": "host_turn_exit",
}


def mark(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def config(data_dir: Path, command: list[str]) -> HostConfig:
    # The synthetic approval names the seeded workspace so that a real
    # one-time allow remains possible for this fixture.
    env = dict(os.environ,
               RUNVORI_FAKE_WORKSPACE=str(Path(data_dir) / "workspace"))
    return HostConfig(
        data_dir=data_dir, upstream_command=command, upstream_cwd=ROOT,
        upstream_env=env, poll_timeout=0.02)


def seed(data_dir: Path) -> None:
    if (data_dir / "host.db").exists():
        return
    workspace = data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    with LockedHostJournals(data_dir) as startup:
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0, expected_run_id=None,
            expected_run_version=None, reason="host_restart",
            recorded_at="2026-10-26T00:00:00Z")["epoch"]
        startup.register_workspace(
            "workspace", workspace, sandbox="read-only", epoch=epoch)
        startup.create_session_in_workspace(
            "session", "workspace", {}, epoch=epoch)
        startup.events.bind_session_thread("session", "thread_demo")


def process_row(runtime: HostRuntime) -> dict | None:
    row = runtime.journals.events.connection.execute(
        "SELECT pid, created_at FROM upstream_processes "
        "WHERE process_epoch=?", (runtime.epoch,)).fetchone()
    return None if row is None else {"pid": row[0], "created_at": row[1]}


def report(runtime: HostRuntime, marker_dir: Path, name: str) -> None:
    db = runtime.journals.events.connection

    def rows(sql: str) -> list[dict]:
        return [dict(row) for row in db.execute(sql).fetchall()]

    events = []
    for row in db.execute(
            "SELECT seq, event_json FROM events WHERE session_id='session' "
            "ORDER BY seq").fetchall():
        event = json.loads(row["event_json"])
        events.append({"seq": row["seq"], "type": event.get("type"),
                       "payload": event.get("payload")})
    data = {
        "epoch": runtime.epoch,
        "host_id": runtime.device_access.host_id,
        "epochs": rows("SELECT process_epoch, state FROM upstream_epochs "
                       "ORDER BY process_epoch"),
        "processes": rows("SELECT process_epoch, pid, created_at "
                          "FROM upstream_processes ORDER BY process_epoch"),
        "runs": rows("SELECT run_id, status, version, terminal_event_seq "
                     "FROM runs ORDER BY run_id"),
        "active_slot": rows("SELECT run_id FROM active_slot"),
        "approvals": rows("SELECT approval_id, status, version, epoch, "
                          "decision, run_id, upstream_request_id "
                          "FROM approvals ORDER BY approval_id"),
        "run_operations": rows("SELECT request_id, status, outcome, "
                               "dispatch_epoch FROM run_operations"),
        "approval_operations": rows("SELECT request_id, status, outcome "
                                    "FROM approval_operations"),
        "interrupt_operations": rows("SELECT request_id, status, outcome, "
                                     "dispatch_epoch FROM interrupt_operations"),
        "session_threads": rows("SELECT session_id, thread_id FROM session_threads"),
        "upstream_turns": rows("SELECT run_id, process_epoch, thread_id, turn_id "
                               "FROM upstream_turns"),
        "events": events,
        "snapshot": runtime.journals.events.snapshot("session"),
    }
    mark(marker_dir / name, json.dumps(data, ensure_ascii=False))


def approval_version(mailbox, approval_id: str) -> int:
    page = mailbox.get_session_events(
        session_id="session", after_seq=0, limit=100, timeout=10)
    for event in page["events"]:
        payload = event.get("payload")
        if (event.get("type") == "approval.requested"
                and isinstance(payload, dict)
                and payload.get("approval_id") == approval_id):
            version = payload.get("approval_version")
            if type(version) is int and version >= 1:
                return version
    raise RuntimeError("approval_version_not_found")


def drive(runtime: HostRuntime, scenario: str, marker_dir: Path) -> None:
    try:
        mailbox = runtime.mailbox
        snapshot = mailbox.get_session_snapshot(
            session_id="session", timeout=10)
        accepted = mailbox.start_run(
            session_id="session", actor_id="device", request_id="run-op",
            expected_session_version=snapshot.get("session_version", 0),
            input_text="hello", timeout=10)
        mark(marker_dir / "driver_accepted",
             json.dumps(accepted, ensure_ascii=False))
        if scenario == "starting":
            return
        deadline = time.monotonic() + 15
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


def crash(scenario: str, data_dir: Path, marker_dir: Path) -> int:
    seed(data_dir)
    mode = SCENARIO_MODES[scenario]
    command = [sys.executable, str(FAKE), mode,
               str(marker_dir / "cut"), str(marker_dir / "leaf.pid")]
    runtime = HostRuntime(config(data_dir, command))
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


def report_only(data_dir: Path, marker_dir: Path) -> int:
    command = [sys.executable, str(FAKE), "thread_bootstrap_good"]
    runtime = HostRuntime(config(data_dir, command))
    try:
        started = runtime.start()
    except Exception as error:
        mark(marker_dir / "report_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "report_started", json.dumps({
        "epoch": runtime.epoch,
        "recovered_epoch": started["recovered_epoch"],
        "recovered_run": started["recovered_run"],
    }, ensure_ascii=False))
    report(runtime, marker_dir, "report.json")
    try:
        stopped = runtime.stop()
    except Exception as error:
        mark(marker_dir / "report_stop_error",
             type(error).__name__ + ":" + str(error))
        return 1
    mark(marker_dir / "report_stopped",
         json.dumps(stopped, ensure_ascii=False))
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        return 2
    mode = sys.argv[1]
    if mode == "crash" and len(sys.argv) >= 5:
        scenario, data_dir, marker_dir = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
        marker_dir.mkdir(parents=True, exist_ok=True)
        return crash(scenario, data_dir, marker_dir)
    if mode == "report" and len(sys.argv) >= 4:
        data_dir, marker_dir = Path(sys.argv[2]), Path(sys.argv[3])
        marker_dir.mkdir(parents=True, exist_ok=True)
        return report_only(data_dir, marker_dir)
    return 2


if __name__ == "__main__":
    sys.exit(main())
