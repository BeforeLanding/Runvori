"""Drive the real APK against a real HostRuntime over HTTPS.

The orchestrator starts the Host (synthetic upstream by default; the pinned
real Codex only when explicitly enabled), provisions a workspace and sessions
through the local named pipe, reverses the Host port onto the device, and then
runs the instrumented Android suite scenario by scenario. Between scenarios it
performs Host-side actions the phone cannot do itself (revoking the device).

Each scenario is a separate instrumentation invocation because Android's test
runner cannot be driven step by step from outside. Everything printed in the
report is a code, count, boolean or digest; tokens, pairing secrets, prompts
and absolute paths never appear.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host/src"))
sys.path.insert(0, str(ROOT / "host/tests"))
sys.path.append(str(ROOT / "scripts"))

from probe_tls_identity import generate_certificate
from runvori.application.local_admin_server import LocalAdminClient
from runvori.codex.execution_probe import ACCEPT_PROMPT, prepare_fixture
from runvori.infrastructure.tls_identity import spki_sha256


FAKE = ROOT / "host/tests/fake_app_server.py"
RUNNER = "dev.runvori.app.test/androidx.test.runner.AndroidJUnitRunner"
EXPECTED_REAL_VERSION = "codex-cli 0.164.0-alpha.11.1"
REAL_SCENARIOS = ("s", "a", "b", "c", "g", "e")
SYNTHETIC_ONLY = frozenset({"d", "f", "h"})


class OrchestrationFailure(RuntimeError):
    """A stable failure code without payloads or credentials."""


LAUNCHER = r"""
import json, sys
sys.path.insert(0, r"{host_src}")
from pathlib import Path
from runvori.host import HostRuntime, HostConfig, utc_now

upstream, workspace, data_dir, certificate, key, expected_version, codex_home, real = sys.argv[1:9]
env = dict(__import__("os").environ)
if real == "1":
    env["CODEX_HOME"] = codex_home
    for name in list(env):
        if any(word in name.upper()
               for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
            del env[name]
else:
    env["RUNVORI_FAKE_WORKSPACE"] = str(Path(workspace))
config = HostConfig(
    data_dir=Path(data_dir),
    upstream_command=upstream.split("|"),
    upstream_cwd=Path(workspace),
    upstream_env=env,
    poll_timeout=0.02,
    now=utc_now,
    expected_upstream_version=expected_version or None,
    listen_host="127.0.0.1",
    listen_port=0,
    tls_certificate=Path(certificate),
    tls_key=Path(key),
    host_name="Runvori Device Host",
    admin_pipe=True,
)
runtime = HostRuntime(config)
print(json.dumps(runtime.start()), flush=True)
import threading, time
stop = threading.Event()
threading.Thread(target=lambda: (time.sleep(3600), stop.set()), daemon=True).start()
result = runtime.run(stop)
runtime.stop()
print(json.dumps({"run": result}), flush=True)
""".replace("{host_src}", str(ROOT / "host/src"))


def adb(arguments: list[str], *, timeout: float = 120,
        serial: str | None = None) -> subprocess.CompletedProcess:
    executable = os.environ.get("ADB") or str(
        Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb.exe")
    if not Path(executable).is_file():
        raise OrchestrationFailure("adb_not_found")
    prefix = ["-s", serial] if serial else []
    # adb echoes device output in UTF-8 while the Windows console default is
    # GBK; decoding explicitly keeps Chinese test messages readable.
    result = subprocess.run([executable, *prefix, *arguments],
                            capture_output=True, timeout=timeout, check=False)
    return subprocess.CompletedProcess(
        result.args, result.returncode,
        (result.stdout or b"").decode("utf-8", "replace"),
        (result.stderr or b"").decode("utf-8", "replace"))


def host_alias(serial: str) -> str:
    """The emulator's address for this machine; a physical phone needs its
    own route (LAN address or VPN) and is out of scope for this harness."""
    result = adb(["-s", serial, "emu", "avd", "name"])
    if result.returncode != 0:
        raise OrchestrationFailure("emulator_required_for_alias")
    return "10.0.2.2"


def devices() -> list[str]:
    result = adb(["devices"])
    found = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            found.append(parts[0])
    return found


def real_environment() -> tuple[Path, Path]:
    codex = os.environ.get("RUNVORI_REAL_CODEX", "")
    home = os.environ.get("RUNVORI_REAL_CODEX_HOME", "")
    if not codex or not home:
        raise OrchestrationFailure("real_codex_environment_missing")
    executable = Path(codex)
    codex_home = Path(home)
    if not executable.is_absolute() or not executable.is_file():
        raise OrchestrationFailure("codex_path_not_absolute")
    if not codex_home.is_absolute() or not codex_home.is_dir():
        raise OrchestrationFailure("codex_home_not_found")
    try:
        probe = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True,
            timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrchestrationFailure("codex_version_probe_failed") from error
    actual = (probe.stdout or "").strip()
    if probe.returncode != 0 or actual != EXPECTED_REAL_VERSION:
        raise OrchestrationFailure("codex_version_mismatch")
    return executable, codex_home


def wait_for_thread_bindings(data_dir: Path, session_ids: list[str],
                             timeout: float = 120) -> None:
    """Wait until every provisioned session has a durable thread binding."""
    if not session_ids:
        return
    database = data_dir / "host.db"
    placeholders = ",".join("?" for _ in session_ids)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            connection = sqlite3.connect(
                f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
            try:
                rows = connection.execute(
                    f"SELECT session_id FROM session_threads "
                    f"WHERE session_id IN ({placeholders})",
                    session_ids).fetchall()
            finally:
                connection.close()
            if {row[0] for row in rows} >= set(session_ids):
                return
        except (OSError, sqlite3.Error):
            pass
        time.sleep(0.5)
    raise OrchestrationFailure("thread_binding_timeout")


def count_runs(data_dir: Path) -> int:
    """Count accepted runs after the Host has stopped; no prompt or payload."""
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM runs").fetchone()[0])
    finally:
        connection.close()


def start_host(*, data_dir: Path, workspace: Path, certificate: Path,
               key: Path, upstream: list[str],
               expected_version: str | None = None,
               codex_home: Path | None = None,
               real: bool = False) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "host/src"), str(ROOT / "host/tests")])
    process = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER, "|".join(upstream), str(workspace),
         str(data_dir), str(certificate), str(key), expected_version or "",
         str(codex_home or ""), "1" if real else "0"],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    line = process.stdout.readline()
    try:
        report = json.loads(line)
    except ValueError as error:
        process.kill()
        raise OrchestrationFailure("host_startup_unreadable") from error
    if report.get("status") != "running":
        process.kill()
        raise OrchestrationFailure("host_start_failed:" +
                                   str(report.get("code")))
    return process


def stop_host(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass


def provision(data_dir: Path, workspace: Path, sessions: int) -> None:
    admin = LocalAdminClient(data_dir)
    created = admin.call("register-workspace", workspace_id="ws-device",
                         root=str(workspace), sandbox="workspace-write")
    if created.get("status") == "failed":
        raise OrchestrationFailure("register_workspace_failed")
    for index in range(sessions):
        result = admin.call(
            "create-session", session_id=f"devsess{index}",
            workspace_id="ws-device", title=f"device session {index + 1}",
            create_thread=True, request_id=f"thread-device-{index}")
        if result.get("status") == "failed":
            raise OrchestrationFailure("create_session_failed:" +
                                       str(result.get("code")))


def run_scenario(serial: str, name: str, method: str,
                 arguments: dict[str, str],
                 port: int | None = None) -> dict:
    command = ["-s", serial, "shell", "am", "instrument", "-w", "-r",
               "-e", "class",
               f"dev.runvori.app.DeviceAcceptanceTest#{method}"]
    for key, value in arguments.items():
        command += ["-e", key, value]
    command.append(RUNNER)
    try:
        result = adb(command, timeout=900)
    except subprocess.TimeoutExpired:
        return {"scenario": name, "status": "failed", "code": "timeout"}
    text = (result.stdout or "") + (result.stderr or "")
    ok = "OK (1 test)" in text
    failed = "FAILURES!!!" in text or "AssertionError" in text
    step = {"scenario": name, "status": "passed" if ok and not failed
            else "failed"}
    if not (ok and not failed):
        step["assertions"] = summarise_failure(text)
    return step


def summarise_failure(text: str) -> list[str]:
    """Keep the failure type, message and the failing test line.

    Failed assertions name the expectation, which is what makes a red run
    diagnosable. Request bodies, credentials and absolute paths never appear in
    the suite's own messages, and the stack is reduced to the frames that name
    the test so the failing line is visible without dumping the whole trace.
    """
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in ("java.lang.AssertionError:",
                       "junit.framework.AssertionFailedError:",
                       "java.lang.IllegalStateException:",
                       "dev.runvori.app.domain.",
                       "dev.runvori.app.data.",
                       "javax.net.ssl."):
            if stripped.startswith(prefix):
                kept.append(stripped[:600])
                break
        if (stripped.startswith("at dev.runvori.app.DeviceAcceptanceTest")
                and "Test.kt:" in stripped):
            kept.append(stripped[:200])
    seen, result = set(), []
    for line in kept:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result[-8:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="")
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--real", action="store_true",
                        help="use the pinned real Codex (explicit opt-in)")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the plan without starting Codex")
    parser.add_argument("--report", default="",
                        help="write a sanitised JSON report to this path")
    parser.add_argument("--only", default="",
                        help="comma separated scenario letters (a,b,…)")
    args = parser.parse_args()
    if os.name != "nt":
        print(json.dumps({"status": "skipped", "code": "windows_required"}))
        return 0
    all_letters = ("s", "a", "b", "c", "d", "f", "h", "g", "e")
    wanted = {item.strip() for item in args.only.split(",") if item.strip()}
    if args.real:
        forbidden = wanted & SYNTHETIC_ONLY
        if forbidden:
            print(json.dumps({
                "status": "failed",
                "code": "synthetic_only_scenario:" +
                        ",".join(sorted(forbidden))}))
            return 1
        if not wanted:
            wanted = set(REAL_SCENARIOS)
    elif not wanted:
        wanted = set(all_letters)
    ordered_wanted = [letter for letter in all_letters if letter in wanted]
    expected_model_turns = 1 if args.real and "b" in wanted else 0
    if args.real and not args.dry_run \
            and os.environ.get("RUNVORI_REAL_HOST_TESTS") != "1":
        print(json.dumps({"status": "skipped",
                          "code": "real_host_tests_disabled"}))
        return 0
    serial = args.serial or (devices() or [""])[0]
    if not serial:
        print(json.dumps({"status": "failed", "code": "no_device"}))
        return 1
    try:
        alias = host_alias(serial)
    except OrchestrationFailure as error:
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 1
    codex = codex_home = None
    if args.real:
        try:
            codex, codex_home = real_environment()
        except OrchestrationFailure as error:
            print(json.dumps({"status": "failed", "code": str(error)}))
            return 1
    if args.dry_run:
        plan = {
            "status": "ready",
            "dry_run": True,
            "real": args.real,
            "upstream": "real_codex" if args.real else "synthetic_app_server",
            "device": {"serial": serial},
            "endpoint": alias,
            "scenarios": ordered_wanted,
            "expected_model_turns": expected_model_turns,
            "not_run": [letter for letter in all_letters
                        if letter not in wanted],
        }
        if args.real:
            plan.update({
                "codex_version": EXPECTED_REAL_VERSION,
                "prompt": "ACCEPT_PROMPT",
                "approval_scope": "allow_once:python --version",
                "command_match_asserted": False,
            })
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    report: dict = {
        "status": "failed",
        "real": args.real,
        "upstream": "real_codex" if args.real else "synthetic_app_server",
        "device": {"serial": serial},
        "scenarios": ordered_wanted,
        "expected_model_turns": expected_model_turns,
        "not_run": [letter for letter in all_letters if letter not in wanted],
        "steps": [],
    }
    if args.real:
        report.update({
            "codex_version": EXPECTED_REAL_VERSION,
            "approval_scope": "allow_once:python --version",
            "command_match_asserted": False,
        })
    folder = Path(tempfile.mkdtemp(prefix="device-acceptance-", dir=ROOT / ".dev"))
    host = None
    data_dir = None
    try:
        certificate, key = generate_certificate(folder, "device",
                                                extra_sans=[f"IP:{alias}"])
        pin = spki_sha256(certificate)
        data_dir = folder / "data"
        data_dir.mkdir()
        workspace = folder / "workspace"
        if args.real:
            prepare_fixture(ROOT, workspace)
            upstream = [str(codex), "app-server", "--listen", "stdio://"]
        else:
            workspace.mkdir()
            upstream = [sys.executable, str(FAKE), "host_api_device"]
        host = start_host(
            data_dir=data_dir, workspace=workspace, certificate=certificate,
            key=key, upstream=upstream,
            expected_version=EXPECTED_REAL_VERSION if args.real else None,
            codex_home=codex_home, real=args.real)
        admin = LocalAdminClient(data_dir)
        status = admin.call("status")
        api = status.get("api") or {}
        port = api.get("listen_port")
        if status.get("status") != "running" or not isinstance(port, int) \
                or not port:
            raise OrchestrationFailure("host_api_not_listening")
        if api.get("spki_sha256") != pin:
            raise OrchestrationFailure("advertised_pin_mismatch")
        provision(data_dir, workspace, args.sessions)
        if args.real:
            wait_for_thread_bindings(
                data_dir, [f"devsess{index}"
                           for index in range(args.sessions)])
        report["steps"].append({"step": "host_running", "sessions": args.sessions,
                                "port": port})
        # Device -> Host transport. The emulator's host alias `10.0.2.2` is
        # translated by the emulator's own NAT to a loopback connection on this
        # machine, so the Host listens on 127.0.0.1 only and nothing is exposed
        # to the LAN. The certificate names the alias as an IP SAN because the
        # client refuses a certificate that does not name its endpoint.
        endpoint = alias
        report["steps"].append({"step": "transport", "endpoint": endpoint,
                                "host_bind": "loopback", "status": "ok"})

        pairing = admin.call("issue-pairing")
        if pairing.get("status") != "issued":
            raise OrchestrationFailure("pairing_issue_failed")
        # The payload is passed to the device process through instrumentation
        # arguments; it never enters a URL, a file or a log.
        # `am instrument -e` splits on whitespace and mangles quotes, so the
        # pairing payload travels as base64 and is decoded on the device. The
        # Host printed `localhost`, which on the device means the device
        # itself, so the endpoint is rewritten to the alias the device dials.
        pairing = dict(pairing)
        pairing["https_url"] = f"https://{endpoint}:{port}/v1"
        encoded_payload = base64.b64encode(json.dumps(
            pairing, ensure_ascii=False).encode("utf-8")).decode("ascii")
        arguments = {
            "pairing_info_b64": encoded_payload,
            "host_port": str(port),
        }
        if args.real:
            arguments["real_mode"] = "1"
            arguments["run_prompt_b64"] = base64.b64encode(
                ACCEPT_PROMPT.encode("utf-8")).decode("ascii")
        stored_arguments = dict(arguments)
        scenarios = [
            ("s", "s_credential_store_round_trips_and_needs_the_keystore"),
            ("a", "a_wrong_fingerprint_is_refused_before_any_request"),
            ("b", "b_full_loop_lists_reads_approves_and_reports"),
        ]
        for letter, method in scenarios:
            if letter not in wanted:
                continue
            report["steps"].append(run_scenario(serial, letter, method,
                                                arguments, port))
        # The scenarios that need a valid credential run without one: they
        # reuse the credential the pairing scenario stored on the device.
        for letter, method in (
                ("c", "c_reconnect_after_process_loss_has_no_duplicates_or_gaps"),
                ("d", "d_expired_approval_cannot_be_allowed"),
                ("f", "f_insufficient_review_offers_only_deny"),
                ("h", "h_interrupt_ends_the_active_run"),
                ("g", "g_duplicate_operation_query_is_stable")):
            if letter not in wanted:
                continue
            report["steps"].append(run_scenario(serial, letter, method,
                                                stored_arguments, port))
        # Revoke on the Host, then the revoked device must be refused.
        if "e" in wanted:
            revoked = admin.call("revoke-device")
            report["steps"].append({
                "step": "revoke", "status": revoked.get("status")})
            report["steps"].append(run_scenario(
                serial, "e", "e_revoked_device_cannot_read", stored_arguments,
                port))
        failures = [step for step in report["steps"]
                    if step.get("scenario") and step["status"] != "passed"]
        report["status"] = "passed" if not failures else "failed"
        return 0 if report["status"] == "passed" else 1
    except OrchestrationFailure as error:
        report["code"] = str(error)
        return 1
    except Exception as error:  # noqa: BLE001 - only the class name is reported
        report["code"] = type(error).__name__
        return 1
    finally:
        if host is not None:
            stop_host(host)
        if args.real and data_dir is not None and data_dir.exists():
            try:
                observed = count_runs(data_dir)
            except (OSError, sqlite3.Error):
                observed = None
            report["observed_runs"] = observed
            if report.get("status") == "passed" \
                    and observed != expected_model_turns:
                report["status"] = "failed"
                report["code"] = "model_turn_count_mismatch"
        if args.report:
            try:
                report_path = Path(args.report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, sort_keys=True,
                               indent=2),
                    encoding="utf-8")
            except OSError:
                report["report_write_failed"] = True
        if not args.keep:
            shutil.rmtree(folder, ignore_errors=True)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    sys.exit(main())