"""Explicitly-enabled real Codex acceptance over the product HTTPS API.

This makes one real model turn with the pinned Codex build. It is skipped
unless RUNVORI_REAL_HOST_TESTS=1; it never retries, never copies credentials
and never prints prompts, model output or absolute paths.

Required environment:
  RUNVORI_REAL_HOST_TESTS=1
  RUNVORI_REAL_CODEX       absolute path to codex.exe (pinned version)
  RUNVORI_REAL_CODEX_HOME  dedicated, already logged-in CODEX_HOME

The client is the same independent HTTPS client used by the synthetic probe;
the only product difference under test is the real app-server.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host/src"))
sys.path.insert(0, str(ROOT / "host/tests"))
sys.path.append(str(ROOT / "scripts"))

from probe_https_host import Client, ProbeFailure, wait_for
from probe_tls_identity import generate_certificate
from runvori.application.local_admin_server import LocalAdminClient
from runvori.codex.execution_probe import ACCEPT_PROMPT, prepare_fixture
from runvori.infrastructure.tls_identity import spki_sha256


POLL_TIMEOUT = 240


def required_env() -> tuple[Path, Path]:
    if os.environ.get("RUNVORI_REAL_HOST_TESTS") != "1":
        raise SystemExit(json.dumps({"status": "skipped",
                                     "code": "real_host_tests_disabled"}))
    codex = os.environ.get("RUNVORI_REAL_CODEX")
    home = os.environ.get("RUNVORI_REAL_CODEX_HOME")
    if not codex or not home:
        raise SystemExit(json.dumps({"status": "skipped",
                                     "code": "real_codex_environment_missing"}))
    executable = Path(codex)
    codex_home = Path(home)
    if not executable.is_file() or not executable.is_absolute():
        raise SystemExit(json.dumps({"status": "failed",
                                     "code": "codex_path_not_absolute"}))
    if not codex_home.is_dir():
        raise SystemExit(json.dumps({"status": "failed",
                                     "code": "codex_home_not_found"}))
    return executable, codex_home


LAUNCHER = r"""
import sys
sys.path.insert(0, r"{host_src}")
from pathlib import Path
from runvori.host import HostRuntime, HostConfig, utc_now

codex, codex_home, data_dir, fixture, certificate, key = sys.argv[1:7]
env = dict(__import__("os").environ, CODEX_HOME=codex_home)
for name in list(env):
    if any(word in name.upper()
           for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
        del env[name]
config = HostConfig(
    data_dir=Path(data_dir),
    upstream_command=[codex, "app-server", "--listen", "stdio://"],
    upstream_cwd=Path(fixture),
    upstream_env=env,
    poll_timeout=0.02,
    now=utc_now,
    expected_upstream_version="codex-cli 0.164.0-alpha.11.1",
    listen_host="127.0.0.1",
    listen_port=0,
    tls_certificate=Path(certificate),
    tls_key=Path(key),
    host_name="Runvori Real Host",
    admin_pipe=True,
)
runtime = HostRuntime(config)
print(__import__("json").dumps(runtime.start()), flush=True)
import threading, time
stop = threading.Event()
threading.Thread(target=lambda: (time.sleep(3600), stop.set()), daemon=True).start()
result = runtime.run(stop)
runtime.stop()
print(__import__("json").dumps({"run": result}), flush=True)
""".replace("{host_src}", str(ROOT / "host/src"))


def run_acceptance() -> dict:
    codex, codex_home = required_env()
    (ROOT / ".dev").mkdir(exist_ok=True)
    report: dict = {"status": "passed", "transport": "https_loopback",
                    "upstream": "real_codex", "steps": []}
    with tempfile.TemporaryDirectory(
            prefix="https-real-", dir=ROOT / ".dev") as temp:
        folder = Path(temp)
        certificate, key = generate_certificate(folder, "host-real")
        pin = spki_sha256(certificate)
        data_dir = folder / "data"
        data_dir.mkdir()
        fixture = prepare_fixture(ROOT, folder / "fixture")
        import subprocess
        process = subprocess.Popen(
            [sys.executable, "-c", LAUNCHER, str(codex), str(codex_home),
             str(data_dir), str(fixture), str(certificate), str(key)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "host/src")))
        try:
            try:
                started = json.loads(process.stdout.readline())
            except ValueError as error:
                raise ProbeFailure("host_startup_unreadable") from error
            if started.get("status") != "running":
                raise ProbeFailure(
                    "host_start_failed:" + str(started.get("code")))
            port = (started.get("api") or {}).get("listen_port")
            report["steps"].append({"step": "host_started",
                                    "epoch": started.get("epoch")})
            _real_steps(report, data_dir, port, certificate, pin, fixture)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
    return report


def _real_steps(report: dict, data_dir: Path, port: int, certificate: Path,
                pin: str, fixture: Path) -> None:
    admin = LocalAdminClient(data_dir)
    admin.call("register-workspace", workspace_id="ws1", root=str(fixture),
               sandbox="workspace-write")
    created = admin.call("create-session", session_id="sess1",
                         workspace_id="ws1", title="real-probe",
                         create_thread=True, request_id="thread-start-real")
    if created.get("thread_start") != "queued":
        raise ProbeFailure("thread_start_not_queued")
    pairing = admin.call("issue-pairing")
    client = Client(port, certificate, pin)
    status, body = client.exchange(base64.b64decode(pairing["pairing_secret"]))
    if status != 200:
        raise ProbeFailure("pairing_exchange_failed")
    report["steps"].append({"step": "paired"})

    # Wait for the product thread/start to bind a real upstream thread.
    snapshot = wait_for(
        lambda: _ready_snapshot(client), POLL_TIMEOUT,
        "thread_not_bound")
    report["steps"].append({"step": "thread_bound",
                            "snapshot_seq": snapshot["snapshot_seq"]})
    status, accepted, _ = client.request(
        "POST", "/v1/sessions/sess1/runs",
        {"request_id": "real-run-1",
         "expected_session_version": snapshot["session_version"],
         "input": ACCEPT_PROMPT})
    if status != 202:
        raise ProbeFailure("run_not_accepted:" + str(
            accepted.get("error", {}).get("code")))
    report["steps"].append({"step": "run_accepted", "status": status})

    approval_id = wait_for(lambda: _approval(client), POLL_TIMEOUT,
                           "approval_timeout")
    status, approval, _ = client.request(
        "GET", f"/v1/approvals/{approval_id}")
    if status != 200 or not approval.get("can_allow"):
        raise ProbeFailure("real_approval_not_reviewable")
    report["steps"].append({
        "step": "approval_detail",
        "details_status": approval["details_status"],
        "command_present": bool(approval.get("command")),
        "cwd_inside_workspace": bool(approval.get("cwd_inside_workspace")),
        "command_matches_probe": approval.get("command") == "python --version",
        "allowed_decisions": approval.get("allowed_decisions")})
    status, decided, _ = client.request(
        "POST", f"/v1/approvals/{approval_id}/decision",
        {"request_id": "real-decision-1",
         "expected_version": approval["version"],
         "decision": "allow_once"})
    if status != 202:
        raise ProbeFailure("real_decision_not_accepted:" + str(
            decided.get("error", {}).get("code")))
    terminal = wait_for(lambda: _terminal(client), POLL_TIMEOUT,
                        "real_run_terminal_timeout")
    status, operation, _ = client.request(
        "GET", "/v1/operations/real-decision-1?kind=approval")
    report["steps"].append({
        "step": "run_terminal", "terminal": terminal,
        "operation_status": operation.get("status"),
        "operation_outcome": operation.get("outcome")})
    status, operation, _ = client.request(
        "GET", "/v1/operations/real-run-1?kind=run")
    report["steps"].append({"step": "run_operation",
                            "operation_status": operation.get("status"),
                            "operation_outcome": operation.get("outcome")})
    status, events, _ = client.request(
        "GET", f"/v1/sessions/sess1/events?after_seq=0&limit=100")
    types = [event["type"] for event in events.get("events", [])]
    report["steps"].append({
        "step": "event_kinds",
        "approval_requested": "approval.requested" in types,
        "approval_updated": "approval.updated" in types,
        "terminal": terminal in types,
        "event_count": len(types)})


def _ready_snapshot(client: Client) -> dict | None:
    status, snapshot, _ = client.request("GET", "/v1/sessions/sess1/snapshot")
    if status != 200:
        return None
    # A bound thread is visible as a snapshot that can accept a run; the
    # product rejects the request with thread_unavailable before binding.
    return snapshot if snapshot.get("session_version", 0) >= 0 else None


def _approval(client: Client) -> str | None:
    status, page, _ = client.request(
        "GET", "/v1/sessions/sess1/events?after_seq=0&limit=100")
    if status != 200:
        return None
    for event in page.get("events", []):
        payload = event.get("payload") or {}
        if (event.get("type") == "approval.requested"
                and isinstance(payload.get("approval_id"), str)):
            return payload["approval_id"]
    return None


def _terminal(client: Client) -> str | None:
    status, snapshot, _ = client.request("GET", "/v1/sessions/sess1/snapshot")
    if status != 200 or snapshot.get("active_run"):
        return None
    status, page, _ = client.request(
        "GET", "/v1/sessions/sess1/events?after_seq=0&limit=100")
    for event in reversed(page.get("events", [])):
        if event.get("type") in {"run.completed", "run.failed",
                                 "run.interrupted"}:
            return event["type"]
    return None


def main() -> int:
    if os.name != "nt":
        print(json.dumps({"status": "skipped", "code": "windows_required"}))
        return 0
    try:
        report = run_acceptance()
    except ProbeFailure as error:
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "code": type(error).__name__}))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())