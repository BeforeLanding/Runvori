"""End-to-end HTTPS client check against a real HostRuntime process.

The client is an independent standard-library HTTPS client: it talks only to
the published /v1 API and drives local administration through the named pipe.
The upstream in this run is a synthetic app-server (no model, no account).

Every run prints one redacted JSON report: status codes, field names, sequence
continuity and booleans. Tokens, pairing secrets, absolute paths, prompts and
command output never enter the report.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The scripts directory contains runvori.py, which would shadow the package,
# so it is appended last rather than prepended.
sys.path.append(str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "host/tests"))
sys.path.insert(0, str(ROOT / "host/src"))

from probe_tls_identity import PinMismatch, generate_certificate, open_pinned
from runvori.application.local_admin_server import (
    LocalAdminClient, LocalAdminError,
)
from runvori.infrastructure.tls_identity import spki_sha256


FAKE = ROOT / "host/tests/fake_app_server.py"
POLL_TIMEOUT = 30


class ProbeFailure(RuntimeError):
    """A stable failure code; never contains payloads or credentials."""


class HostProcess:
    """Run the real Host CLI with a synthetic upstream; read its JSON report."""

    def __init__(self, data_dir: Path, certificate: Path, key: Path):
        self.data_dir = data_dir
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT / "host/src"), str(ROOT / "host/tests")])
        env["RUNVORI_CODEX"] = sys.executable
        # Route the synthetic app-server through the launcher wrapper: the
        # Host pins an exact Codex version only when one is configured, and
        # this probe intentionally does not (it is not a real Codex run).
        env["RUNVORI_FAKE_WORKSPACE"] = str(data_dir / "workspace")
        self.process = subprocess.Popen(
            [sys.executable, "-c", LAUNCHER, str(FAKE), str(data_dir),
             str(certificate), str(key)],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def report(self) -> dict:
        line = self.process.stdout.readline()
        try:
            return json.loads(line)
        except ValueError as error:
            raise ProbeFailure("host_startup_unreadable") from error

    def wait(self, timeout: float) -> int:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop()
            return -1

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                return
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass


# The launcher starts the real HostRuntime with a synthetic app-server command
# so the version gate stays unset; it never touches a real Codex install.
LAUNCHER = r"""
import sys
sys.path.insert(0, r"{host_src}")
from pathlib import Path
from runvori.host import HostRuntime, HostConfig, utc_now

fake, data_dir, certificate, key = sys.argv[1:5]
data_dir = Path(data_dir)
(data_dir / "workspace").mkdir(parents=True, exist_ok=True)
config = HostConfig(
    data_dir=data_dir,
    upstream_command=[sys.executable, fake, "host_api_flow"],
    upstream_cwd=data_dir,
    upstream_env=dict(__import__("os").environ),
    poll_timeout=0.02,
    now=utc_now,
    listen_host="127.0.0.1",
    listen_port=0,
    tls_certificate=Path(certificate),
    tls_key=Path(key),
    host_name="Probe Host",
    admin_pipe=True,
)
runtime = HostRuntime(config)
print(__import__("json").dumps(runtime.start()), flush=True)
import threading, time
stop = threading.Event()
threading.Thread(target=lambda: (time.sleep(120), stop.set()), daemon=True).start()
result = runtime.run(stop)
runtime.stop()
print(__import__("json").dumps({"run": result}), flush=True)
""".replace("{host_src}", str(ROOT / "host/src"))


class Client:
    """A minimal HTTPS client with certificate pinning and no retries."""

    def __init__(self, port: int, certificate: Path, pin: str):
        self.port = port
        self.certificate = certificate
        self.pin = pin
        self.token: bytes | None = None

    def request(self, method: str, path: str, body: dict | None = None,
                *, headers: dict | None = None,
                timeout: float = 35) -> tuple[int, dict, dict]:
        payload = b"" if body is None else json.dumps(
            body, separators=(",", ":")).encode("utf-8")
        head = [f"{method} {path} HTTP/1.1", "Host: localhost"]
        if payload:
            head.append("Content-Type: application/json")
            head.append(f"Content-Length: {len(payload)}")
        head.append("X-Runvori-Protocol: 1")
        if self.token is not None:
            head.append(
                "Authorization: Bearer "
                + base64.b64encode(self.token).decode("ascii"))
        for name, value in (headers or {}).items():
            if value:
                head.append(f"{name}: {value}")
        head.append("Connection: close")
        raw = ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + payload
        with open_pinned(self.port, self.certificate, self.pin) as secure:
            secure.settimeout(timeout)
            secure.sendall(raw)
            response = http.client.HTTPResponse(secure)
            response.begin()
            data = response.read()
            try:
                decoded = json.loads(data) if data else {}
            except ValueError as error:
                raise ProbeFailure("invalid_json_response") from error
            return response.status, decoded, dict(response.getheaders())

    def exchange(self, secret: bytes) -> tuple[int, dict]:
        status, body, _headers = self.request(
            "POST", "/v1/pairings/exchange",
            {"pairing_secret": base64.b64encode(secret).decode("ascii"),
             "device_name": "probe-phone"},
            headers={"Authorization": ""}, timeout=20)
        if status == 200 and isinstance(body.get("device_token"), str):
            self.token = base64.b64decode(body["device_token"])
        return status, body


def wait_for(condition, timeout: float, code: str) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    raise ProbeFailure(code)


def run_probe() -> dict:
    (ROOT / ".dev").mkdir(exist_ok=True)
    report: dict = {"status": "passed", "transport": "https_loopback",
                    "upstream": "synthetic_app_server", "steps": []}
    with tempfile.TemporaryDirectory(prefix="https-host-", dir=ROOT / ".dev") as temp:
        folder = Path(temp)
        certificate, key = generate_certificate(folder, "host-api")
        pin = spki_sha256(certificate)
        wrong_pin = "0" * 64
        data_dir = folder / "data"
        data_dir.mkdir()
        host = HostProcess(data_dir, certificate, key)
        try:
            started = host.report()
            if started.get("status") != "running":
                raise ProbeFailure("host_start_failed:" + str(started.get("code")))
            api = started.get("api") or {}
            port = api.get("listen_port")
            if not isinstance(port, int) or not port:
                raise ProbeFailure("api_not_listening")
            if api.get("spki_sha256") != pin:
                raise ProbeFailure("advertised_pin_mismatch")
            _run_steps(report, data_dir, port, certificate, pin, wrong_pin)
        finally:
            host.stop()
    return report


def _run_steps(report: dict, data_dir: Path, port: int, certificate: Path,
               pin: str, wrong_pin: str) -> None:
    def step(name: str, **details) -> None:
        report["steps"].append({"step": name, **details})

    admin = LocalAdminClient(data_dir)
    client = Client(port, certificate, pin)

    # 1. A wrong pin must not send any application data.
    client.pin = wrong_pin
    try:
        client.request("GET", "/v1/host")
    except PinMismatch:
        step("tls_pin_mismatch", result="no_http_request")
    else:
        raise ProbeFailure("request_sent_before_pin_check")
    client.pin = pin

    # 2. Local administration: workspace, session, first thread, pairing.
    workspace = data_dir / "workspace"
    registered = admin.call("register-workspace", workspace_id="ws1",
                            root=str(workspace), sandbox="read-only")
    if registered.get("status") == "failed":
        raise ProbeFailure("register_workspace_failed:" + str(registered.get("code")))
    created = admin.call("create-session", session_id="sess1",
                         workspace_id="ws1", title="probe",
                         create_thread=True, request_id="thread-start-1")
    if created.get("status") == "failed":
        raise ProbeFailure("create_session_failed:" + str(created.get("code")))
    pairing = admin.call("issue-pairing")
    if pairing.get("status") != "issued":
        raise ProbeFailure("pairing_issue_failed:" + str(pairing.get("code")))
    step("local_admin", workspace="registered", session="created",
         thread_start=created.get("thread_start"),
         https_url_scheme=pairing["https_url"].split(":", 1)[0],
         pin_matches_host="spki_sha256" in pairing)

    # 3. Protocol and authentication gates before the token exists.
    status, body, _ = client.request("GET", "/v1/host")
    if status != 401 or body.get("error", {}).get("code") != "unauthorized":
        raise ProbeFailure("unauthenticated_request_not_rejected")
    status, body, _ = client.request(
        "GET", "/v1/host", headers={"X-Runvori-Protocol": "2"})
    if status != 401:
        raise ProbeFailure("protocol_checked_after_auth")
    step("auth_gate", unauthenticated=401)

    # 4. Pairing exchange, replay rejection and rate limiting.
    secret = base64.b64decode(pairing["pairing_secret"])
    status, body = client.exchange(secret)
    if status != 200 or not client.token:
        raise ProbeFailure("pairing_exchange_failed")
    step("pairing", status=status, host_id_present="host_id" in body)
    status, body = client.exchange(secret)
    if status != 409:
        raise ProbeFailure("pairing_secret_replay_accepted")
    step("pairing_replay", status=status)
    limited = False
    for _ in range(6):
        status, body, headers = client.request(
            "POST", "/v1/pairings/exchange",
            {"pairing_secret": base64.b64encode(b"\x00" * 32).decode("ascii")},
            headers={"Authorization": ""})
        if status == 429:
            limited = headers.get("Retry-After", "").isdigit()
            break
    if not limited:
        raise ProbeFailure("rate_limit_not_enforced")
    step("rate_limit", status=429, retry_after=limited)

    # 5. Host, snapshot and event sync.
    status, host_view, _ = client.request("GET", "/v1/host")
    if status != 200 or host_view.get("protocol") != "1":
        raise ProbeFailure("host_view_failed")
    step("host_view", status=status, execution_available=bool(
        host_view.get("execution_available")))
    status, snapshot, _ = client.request("GET", "/v1/sessions/sess1/snapshot")
    if status != 200:
        raise ProbeFailure("snapshot_failed:" + str(
            snapshot.get("error", {}).get("code")))
    snapshot_seq = snapshot["snapshot_seq"]
    step("snapshot", status=status, snapshot_seq=snapshot_seq,
         workspace_named=bool(snapshot.get("workspace_name")))

    # 6. Start a real run and long-poll for its approval event.
    status, accepted, _ = client.request(
        "POST", "/v1/sessions/sess1/runs",
        {"request_id": "run-1",
         "expected_session_version": snapshot["session_version"],
         "input": "run the probe command"})
    if status != 202:
        raise ProbeFailure("run_not_accepted:" + str(
            accepted.get("error", {}).get("code")))
    step("run_accepted", status=status, operation_status=accepted.get("status"))
    approval_id = wait_for(
        lambda: _find_approval(client, snapshot_seq), POLL_TIMEOUT,
        "approval_event_timeout")
    step("approval_event", observed=True)

    # 7. Read the approval review, then allow it once.
    status, approval, _ = client.request(
        "GET", f"/v1/approvals/{approval_id}")
    if status != 200:
        raise ProbeFailure("approval_detail_failed")
    if approval.get("details_status") != "complete" or not approval.get("can_allow"):
        raise ProbeFailure("approval_detail_not_reviewable")
    step("approval_detail", status=status,
         details_status=approval["details_status"],
         command_present=bool(approval.get("command")),
         cwd_inside_workspace=bool(approval.get("cwd_inside_workspace")),
         allowed_decisions=approval.get("allowed_decisions"))
    status, decided, _ = client.request(
        "POST", f"/v1/approvals/{approval_id}/decision",
        {"request_id": "decision-1",
         "expected_version": approval["version"],
         "decision": "allow_once"})
    if status != 202:
        raise ProbeFailure("decision_not_accepted:" + str(
            decided.get("error", {}).get("code")))
    step("decision_accepted", status=status)

    # 8. Operation status and the terminal event.
    status, operation, _ = client.request(
        "GET", "/v1/operations/decision-1?kind=approval")
    if status != 200:
        raise ProbeFailure("operation_query_failed")
    terminal = wait_for(lambda: _terminal_run(client), POLL_TIMEOUT,
                        "run_terminal_timeout")
    status, operation, _ = client.request(
        "GET", "/v1/operations/decision-1?kind=approval")
    step("run_completed", terminal=terminal,
         operation_status=operation.get("status"),
         operation_outcome=operation.get("outcome"))

    # 9. Disconnect and resynchronise from the first snapshot.
    status, replay, _ = client.request(
        "GET", f"/v1/sessions/sess1/events?after_seq={snapshot_seq}&limit=100")
    events = replay.get("events", []) if status == 200 else []
    seqs = [event["seq"] for event in events]
    continuous = bool(seqs) and seqs == list(
        range(snapshot_seq + 1, snapshot_seq + 1 + len(seqs)))
    if not continuous:
        raise ProbeFailure("incremental_sync_not_continuous")
    step("resync", after_seq=snapshot_seq, event_count=len(seqs),
         continuous=continuous,
         duplicate_count=len(seqs) - len(set(seqs)))

    # 10. Insufficient review must block allow and still allow deny.
    status, snapshot, _ = client.request("GET", "/v1/sessions/sess1/snapshot")
    status, accepted, _ = client.request(
        "POST", "/v1/sessions/sess1/runs",
        {"request_id": "run-2",
         "expected_session_version": snapshot["session_version"],
         "input": "file-change probe"})
    if status != 202:
        raise ProbeFailure("second_run_not_accepted")
    file_approval = wait_for(
        lambda: _find_approval(client, snapshot["snapshot_seq"],
                               kind="file_change"),
        POLL_TIMEOUT, "file_approval_timeout")
    status, approval, _ = client.request(
        "GET", f"/v1/approvals/{file_approval}")
    if approval.get("details_status") != "insufficient" or approval.get("can_allow"):
        raise ProbeFailure("insufficient_review_was_allowable")
    status, refused, _ = client.request(
        "POST", f"/v1/approvals/{file_approval}/decision",
        {"request_id": "decision-2", "expected_version": approval["version"],
         "decision": "allow_once"})
    if status != 409 or refused.get("error", {}).get("code") != \
            "approval_details_insufficient":
        raise ProbeFailure("insufficient_review_not_blocked")
    status, denied, _ = client.request(
        "POST", f"/v1/approvals/{file_approval}/decision",
        {"request_id": "decision-3", "expected_version": approval["version"],
         "decision": "deny"})
    if status != 202:
        raise ProbeFailure("deny_after_insufficient_failed")
    wait_for(lambda: _terminal_run(client), POLL_TIMEOUT,
             "second_run_terminal_timeout")
    step("insufficient_review", allow_status=409,
         allow_blocked=True, deny_status=202,
         missing=approval.get("missing_details"))

    # 11. Revoke while a long poll is in flight: it ends, later calls 401.
    result: dict = {}

    def long_poll() -> None:
        try:
            status, body, _ = client.request(
                "GET", "/v1/sessions/sess1/events?after_seq=999999&limit=10")
            result["status"] = status
        except ProbeFailure as error:
            result["error"] = str(error)
        except Exception as error:  # connection closed is acceptable
            result["error"] = type(error).__name__

    worker = threading.Thread(target=long_poll, daemon=True)
    worker.start()
    time.sleep(0.5)
    revoked = admin.call("revoke-device")
    if revoked.get("status") != "revoked":
        raise ProbeFailure("revoke_failed")
    worker.join(timeout=15)
    if worker.is_alive():
        raise ProbeFailure("long_poll_not_cancelled")
    status, body, _ = client.request("GET", "/v1/host")
    if status != 401:
        raise ProbeFailure("revoked_token_still_accepted")
    step("revocation", long_poll_ended=not worker.is_alive(),
         subsequent_status=status)


def _find_approval(client: Client, after: int, *, kind: str = "command"
                   ) -> str | None:
    status, page, _ = client.request(
        "GET", f"/v1/sessions/sess1/events?after_seq={after}&limit=100")
    if status != 200:
        return None
    for event in page.get("events", []):
        payload = event.get("payload") or {}
        if (event.get("type") == "approval.requested"
                and payload.get("kind") == kind
                and isinstance(payload.get("approval_id"), str)):
            return payload["approval_id"]
    return None


def _terminal_run(client: Client) -> str | None:
    status, snapshot, _ = client.request("GET", "/v1/sessions/sess1/snapshot")
    if status != 200 or snapshot.get("active_run"):
        return None
    status, page, _ = client.request(
        "GET", "/v1/sessions/sess1/events?after_seq=0&limit=100")
    if status != 200:
        return None
    for event in reversed(page.get("events", [])):
        if event.get("type", "").startswith("run.") and event["type"] in {
                "run.completed", "run.failed", "run.interrupted"}:
            return event["type"]
    return None


def main() -> int:
    if os.name != "nt":
        print(json.dumps({"status": "skipped", "code": "windows_required"}))
        return 0
    if not shutil.which("openssl"):
        print(json.dumps({"status": "skipped", "code": "openssl_unavailable"}))
        return 0
    try:
        report = run_probe()
    except ProbeFailure as error:
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 1
    except (OSError, ValueError, LocalAdminError, ssl.SSLError,
            http.client.HTTPException) as error:
        print(json.dumps({"status": "failed", "code": type(error).__name__}))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())