"""Drive the real APK against a real HostRuntime over HTTPS.

The orchestrator starts the Host (synthetic upstream by default; the pinned
real Codex only when explicitly enabled), provisions a workspace and sessions
through the local named pipe, points the device at the host through the
emulator's `10.0.2.2` NAT alias or an explicitly supplied LAN/VPN/DNS address,
and then runs the instrumented Android suite scenario by scenario. Between
scenarios it performs Host-side actions the phone cannot do itself (revoking
the device; changing the required verification commands).

Each scenario is a separate instrumentation invocation because Android's test
runner cannot be driven step by step from outside. Everything printed in the
report is a code, count, boolean or digest; tokens, pairing secrets, prompts
and absolute paths never appear.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
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
from runvori.codex import command_match
from runvori.codex.execution_probe import (
    ACCEPT_PROMPT, FILE_APPROVAL_ALLOWED, FILE_APPROVAL_BEFORE,
    FILE_APPROVAL_DENIED, FILE_APPROVAL_NAME, INTERRUPT_MARKER,
    REAL_EXECUTION_INTERRUPT_PROMPT, REAL_FILE_APPROVAL_ALLOW_PROMPT,
    REAL_FILE_APPROVAL_DENY_PROMPT, REAL_PENDING_INTERRUPT_PROMPT,
    REAL_VERIFY_PROMPT, prepare_file_approval_fixture, prepare_fixture,
)
from runvori.infrastructure.tls_identity import spki_sha256


FAKE = ROOT / "host/tests/fake_app_server.py"
RUNNER = "dev.runvori.app.test/androidx.test.runner.AndroidJUnitRunner"
EXPECTED_REAL_VERSION = "codex-cli 0.164.0-alpha.11.1"
REAL_SCENARIOS = ("s", "a", "b", "c", "g", "r", "e")
SYNTHETIC_ONLY = frozenset({
    "d", "f", "h", "i", "j", "k", "n", "o", "q", "w", "x", "t", "y", "z",
    "s2", "s3", "fs", "off", "rec", "rd", "rk"})
# `ri`/`ra` are the two real interrupt specials: one interrupts a running
# command after its output proves execution started, the other ends a turn
# while an approval is still pending.
REAL_ONLY = frozenset({"l", "m", "v", "ri", "ra"})
# Device-baseline-only scenarios. They are not part of the historical default
# synthetic set so an existing emulator invocation keeps its cost and scope.
BASELINE_ONLY = frozenset({"fs", "off", "rec"})
ALL_SCENARIOS = ("s", "a", "b", "c", "d", "f", "h", "g", "o", "q", "w", "x",
                 "i", "l", "m", "v", "ri", "ra", "r", "j", "k", "n", "rd", "rk", "y",
                 "z", "s2", "s3", "t", "fs", "off", "rec", "e")
# Zero-model real-device baseline. Every scenario uses the synthetic upstream,
# so the budget is structurally zero; `b` and `fs` may create local synthetic
# runs, but neither reaches a model.
DEVICE_BASELINE = ("s", "a", "b", "c", "o", "y", "z", "i", "j", "k",
                   "rd", "rk", "fs", "off", "rec", "e")
TURN_SCENARIOS = frozenset({"b", "l", "m", "v", "ri", "ra"})
# Scenarios that reuse a credential and therefore need the pairing bootstrap
# when `b` (which pairs itself) is not selected.
CREDENTIAL_SCENARIOS = frozenset({
    "c", "d", "f", "h", "g", "i", "j", "k", "l", "m", "v", "r", "n",
    "o", "q", "w", "x", "t", "y", "z", "s2", "s3", "ri", "ra",
    "fs", "off", "rec", "rd", "rk",
})
METHODS = {
    "s": "s_credential_store_round_trips_and_needs_the_keystore",
    "a": "a_wrong_fingerprint_is_refused_before_any_request",
    "b": "b_full_loop_lists_reads_approves_and_reports",
    "c": "c_reconnect_after_process_loss_has_no_duplicates_or_gaps",
    "d": "d_expired_approval_cannot_be_allowed",
    "f": "f_insufficient_review_offers_only_deny",
    "h": "h_interrupt_ends_the_active_run",
    "g": "g_duplicate_operation_query_is_stable",
    "i": "i_results_show_completed_run_with_failed_verification",
    "j": "j_file_approval_complete_allows_and_shows_artifacts",
    "k": "k_file_approval_denied_keeps_proposal_visible",
    "n": "n_file_approval_absolute_updated_after_open",
    "o": "o_approval_survives_pause_and_process_restore",
    "q": "q_decision_receipt_loss_is_recovered_without_resubmit",
    "w": "w_missing_approval_is_stale_and_not_actionable",
    "x": "x_artifact_switch_ignores_stale_chunk",
    "t": "t_approval_survives_activity_rotation",
    "u": "u_process_loss_setup_leaves_approval_and_lost_receipt",
    "s2": "s2_stale_approval_after_host_restart",
    "s3": "s3_approval_page_interrupt_expires_request",
    "fs": "fs_font_scale_200_key_pages_remain_operable",
    "off": "off_offline_shows_cached_sessions_and_timeline_read_only",
    "rec": "rec_recovery_resyncs_from_cursor_without_duplicates",
    "rd": "rd_browsing_keeps_position_while_output_streams",
    "rk": "rk_reading_anchor_survives_process_restore",
    "l": "l_real_file_approval_allow_shows_diff_and_report",
    "m": "m_real_file_approval_denied_keeps_proposal_visible",
    "v": "v_real_verification_result_is_passed",
    "ri": "ri_execution_interrupt_has_confirmed_terminal_and_released_slot",
    "ra": "ra_turn_termination_expires_waiting_approval",
    "r": "r_restart_readback_keeps_command_and_artifact_hashes",
    "p": "p_pairing_bootstrap",
    "e": "e_revoked_device_cannot_read",
}
SCENARIO_CLASSES = {
    "t": "dev.runvori.app.LifecycleRotationAcceptanceTest",
}
PROMPTS = {
    "b": ("run_prompt_b64", ACCEPT_PROMPT),
    "l": ("run_prompt_l_b64", REAL_FILE_APPROVAL_ALLOW_PROMPT),
    "m": ("run_prompt_m_b64", REAL_FILE_APPROVAL_DENY_PROMPT),
    "v": ("run_prompt_v_b64", REAL_VERIFY_PROMPT),
    "ri": ("run_prompt_ri_b64", REAL_EXECUTION_INTERRUPT_PROMPT),
    "ra": ("run_prompt_ra_b64", REAL_PENDING_INTERRUPT_PROMPT),
}
# Frozen presentation of the two real specials for `--dry-run` and reports.
INTERRUPT_CHECKS = {
    "ri": {
        "trigger": "command.output contains " + INTERRUPT_MARKER,
        "start_timeout_seconds": 180,
        "command_max_seconds": 60,
        "terminal_timeout_seconds": 240,
        "success": ("run.interrupted for the same run, interrupt operation "
                    "confirmed/run_interrupted, active slot released"),
        "race": ("natural completion before the interrupt is recorded as "
                 "terminal_race_natural_completion, never as success"),
    },
    "ra": {
        "trigger": "product interrupt entry while the approval page stays open",
        "terminal_timeout_seconds": 240,
        "success": ("turn-terminal expiry present in approval history, final "
                    "status expired, old decision refused, zero approval "
                    "operations; a later upstream request_closed may replace "
                    "the current resolution_kind"),
    },
}


class OrchestrationFailure(RuntimeError):
    """A stable failure code without payloads or credentials."""


def planned_model_turns(real: bool, wanted: set[str]) -> int:
    """One Host-accepted run per selected real turn scenario."""
    if not real:
        return 0
    return len(wanted & TURN_SCENARIOS)


def turn_count_matches(expected: int, observed: object) -> bool:
    """A missing or non-integer count never matches an expected budget."""
    return type(observed) is int and observed == expected


LAUNCHER = r"""
import json, sys
sys.path.insert(0, r"{host_src}")
from pathlib import Path
from runvori.host import HostRuntime, HostConfig, utc_now

upstream, workspace, data_dir, certificate, key, expected_version, codex_home, real, listen_port, listen_host = sys.argv[1:11]
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
    listen_host=listen_host,
    listen_port=int(listen_port),
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
try:
    (Path(data_dir) / "run-result.json").write_text(
        json.dumps(result), encoding="utf-8")
except OSError:
    pass
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


def is_emulator(serial: str) -> bool:
    result = adb(["-s", serial, "emu", "avd", "name"])
    return result.returncode == 0


def clean_endpoint(value: str, *, what: str) -> tuple[str, object | None]:
    """Accept a bare IP or DNS name, never a URL, port or path.

    The Host chooses the port and the pairing payload carries it. Accepting a
    port here would create two sources of truth for the same endpoint.
    """
    text = value.strip()
    if not text:
        raise OrchestrationFailure(what + "_empty")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if ("://" in text or "/" in text or "?" in text or "#" in text
            or any(char.isspace() for char in text)):
        raise OrchestrationFailure(what + "_must_be_bare_host")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        address = None
    if address is None:
        if ":" in text or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\.?", text):
            raise OrchestrationFailure(what + "_invalid")
    return text.rstrip("."), address


def build_transport(serial: str, host_address: str,
                    listen_address: str,
                    *, emulator: bool | None = None) -> dict:
    """Separate what the phone dials from where this Host binds.

    An emulator keeps the historical `10.0.2.2` NAT path and loopback bind. A
    physical phone must name its own reachable LAN/VPN address; the bind
    defaults to that literal address (never all interfaces) unless the caller
    explicitly chooses another local listener.
    """
    if host_address:
        endpoint, address = clean_endpoint(host_address, what="host_address")
        if address is None:
            kind = "dns"
        elif address.is_loopback:
            kind = "loopback"
        else:
            kind = "lan_or_vpn"
    else:
        if emulator is None:
            emulator = is_emulator(serial)
        if not emulator:
            raise OrchestrationFailure("host_address_required_for_device")
        endpoint, address = "10.0.2.2", ipaddress.ip_address("10.0.2.2")
        kind = "emulator_nat"

    if listen_address:
        listen, listen_ip = clean_endpoint(
            listen_address, what="listen_address")
        if listen_ip is None and listen.lower() == "localhost":
            listen_ip = ipaddress.ip_address("127.0.0.1")
        if listen_ip is None:
            raise OrchestrationFailure("listen_address_must_be_ip")
    elif kind in {"emulator_nat", "loopback"} \
            or endpoint.lower() == "localhost":
        listen, listen_ip = "127.0.0.1", ipaddress.ip_address("127.0.0.1")
    elif address is not None:
        listen, listen_ip = endpoint, address
    else:
        raise OrchestrationFailure("listen_address_required_for_dns")

    if ":" in endpoint:
        url_host = f"[{endpoint}]"
    else:
        url_host = endpoint
    san = f"IP:{endpoint}" if address is not None else f"DNS:{endpoint}"
    loopback = bool(listen_ip and listen_ip.is_loopback)
    report = {
        "kind": kind,
        "bind": "loopback" if loopback else "non_loopback",
        "hostname_san": "ip" if address is not None else "dns",
        "tls": {
            "chain": "required",
            "hostname": "required",
            "spki_pin": "required",
        },
    }
    if kind == "emulator_nat":
        report["endpoint"] = endpoint
    else:
        report["endpoint_digest"] = hashlib.sha256(
            endpoint.encode("utf-8")).hexdigest()[:16]
    return {
        "endpoint": endpoint,
        "url_host": url_host,
        "listen": listen,
        "san": san,
        "report": report,
    }


def devices() -> list[str]:
    result = adb(["devices"])
    found = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            found.append(parts[0])
    return found


def device_properties(serial: str) -> dict:
    def prop(name: str) -> str:
        return adb(["shell", "getprop", name], serial=serial,
                   timeout=30).stdout.strip()
    identity: dict = {
        "model": prop("ro.product.model") or None,
        "manufacturer": prop("ro.product.manufacturer") or None,
        "api": prop("ro.build.version.sdk") or None,
        "abi": prop("ro.product.cpu.abi") or None,
        "android_release": prop("ro.build.version.release") or None,
    }
    if is_emulator(serial):
        identity["serial"] = serial
        avd = adb(["-s", serial, "emu", "avd", "name"]).stdout.splitlines()
        if avd:
            identity["avd"] = avd[0].strip()
    else:
        identity["serial_digest"] = hashlib.sha256(
            serial.encode("utf-8")).hexdigest()[:16]
    return identity


def package_version(serial: str) -> dict:
    text = adb(["shell", "dumpsys", "package", "dev.runvori.app"],
               serial=serial, timeout=60).stdout
    name = re.search(r"versionName=([^\s]+)", text)
    code = re.search(r"versionCode=(\d+)", text)
    return {
        "installed": "package:" in adb(
            ["shell", "pm", "path", "dev.runvori.app"],
            serial=serial, timeout=30).stdout,
        "version_name": name.group(1) if name else None,
        "version_code": int(code.group(1)) if code else None,
    }


def apk_report(path: Path) -> dict:
    result = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        "version_name": None,
        "version_code": None,
    }
    metadata = path.parent / "output-metadata.json"
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        element = (payload.get("elements") or [{}])[0]
        result["version_name"] = element.get("versionName")
        result["version_code"] = element.get("versionCode")
    except (OSError, ValueError, AttributeError, IndexError):
        pass
    return result


def preflight_device(serial: str, apk: str, test_apk: str,
                     install: bool) -> dict:
    """Record the real device/build and optionally install this run's APKs."""
    device = device_properties(serial)
    report: dict = {"status": "ok", "device": device}
    apk_path = Path(apk) if apk else None
    test_path = Path(test_apk) if test_apk else None
    if apk_path is not None and not apk_path.is_file():
        raise OrchestrationFailure("apk_not_found")
    if test_path is not None and not test_path.is_file():
        raise OrchestrationFailure("test_apk_not_found")
    if install and apk_path is not None:
        installed = adb(["install", "-r", "-t", str(apk_path)],
                        serial=serial, timeout=300)
        if "Success" not in installed.stdout:
            raise OrchestrationFailure("apk_install_failed")
        report["apk"] = apk_report(apk_path)
    if install and test_path is not None:
        installed = adb(["install", "-r", "-t", str(test_path)],
                        serial=serial, timeout=300)
        if "Success" not in installed.stdout:
            raise OrchestrationFailure("test_apk_install_failed")
        report["test_apk"] = apk_report(test_path)
    version = package_version(serial)
    report["package"] = version
    if not version["installed"]:
        raise OrchestrationFailure("app_not_installed")
    test_present = "package:" in adb(
        ["shell", "pm", "path", "dev.runvori.app.test"],
        serial=serial, timeout=30).stdout
    report["test_package_installed"] = test_present
    if not test_present:
        raise OrchestrationFailure("test_apk_not_installed")
    if apk_path is not None and "apk" in report:
        expected = report["apk"]
        if (expected["version_name"] is not None
                and version["version_name"] != expected["version_name"]):
            raise OrchestrationFailure("apk_version_mismatch")
        if (expected["version_code"] is not None
                and version["version_code"] != expected["version_code"]):
            raise OrchestrationFailure("apk_version_mismatch")
    return report


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


def count_approval_operations(data_dir: Path) -> int:
    """Count decisions the Host recorded; content and ids never leave."""
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM approval_operations").fetchone()[0])
    finally:
        connection.close()


def active_slot_run(data_dir: Path) -> str | None:
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def latest_pending_approval(data_dir: Path) -> str | None:
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT approval_id FROM approvals WHERE status='pending' "
            "ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def interrupt_evidence(data_dir: Path, session_id: str) -> dict:
    """Read the Host's own record of the scenario run without content values.

    The phone proves what it displayed; this proves the durable terminal,
    interrupt outcome and slot. Command output is only scanned for the real
    start marker, and only a boolean leaves this function.
    """
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        runs = connection.execute(
            "SELECT run_id, status FROM runs WHERE session_id=? "
            "ORDER BY rowid DESC", (session_id,)).fetchall()
        result = {
            "session": session_id,
            "session_runs": len(runs),
            "run_id": None,
            "run_status": None,
            "active_slot_run_id": None,
            "interrupt_status": None,
            "interrupt_outcome": None,
            "approvals": 0,
            "pending_approvals": 0,
            "approval_status": None,
            "approval_resolution_kind": None,
            "approval_turn_terminal_observed": False,
            "approval_late_closure_observed": False,
            "approval_operations": 0,
            "start_signal_observed": False,
        }
        slot = connection.execute(
            "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
        result["active_slot_run_id"] = None if slot is None else slot["run_id"]
        if not runs:
            return result
        run = runs[0]
        result["run_id"] = run["run_id"]
        result["run_status"] = run["status"]
        interrupt = connection.execute(
            "SELECT status, outcome FROM interrupt_operations "
            "WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
            (run["run_id"],)).fetchone()
        if interrupt is not None:
            result["interrupt_status"] = interrupt["status"]
            result["interrupt_outcome"] = interrupt["outcome"]
        approvals = connection.execute(
            "SELECT approval_id, status, resolution_kind FROM approvals "
            "WHERE run_id=? ORDER BY rowid", (run["run_id"],)).fetchall()
        result["approvals"] = len(approvals)
        result["pending_approvals"] = sum(
            1 for approval in approvals if approval["status"] == "pending")
        last_approval_id = None
        if approvals:
            last_approval_id = approvals[-1]["approval_id"]
            result["approval_status"] = approvals[-1]["status"]
            result["approval_resolution_kind"] = approvals[-1]["resolution_kind"]
        # Real Codex can close the upstream request just after the turn
        # terminal expired it, which rewrites the current resolution_kind to
        # request_closed. The durable turn-terminal expiry remains in the
        # event stream, so the cause is read from history as well as the row.
        expired_by_turn: set[str] = set()
        for row in connection.execute(
                "SELECT event_json FROM events WHERE session_id=? "
                "AND event_json LIKE '%\"type\":\"approval.updated\"%' "
                "ORDER BY seq", (session_id,)):
            try:
                event = json.loads(row["event_json"])
            except ValueError:
                continue
            if (event.get("run_id") != run["run_id"]
                    or event.get("type") != "approval.updated"):
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            approval_id = payload.get("approval_id")
            status = payload.get("status")
            if (status == "expired"
                    and (payload.get("reason") == "turn_terminal"
                         or payload.get("resolution_kind") == "turn_terminal")):
                if isinstance(approval_id, str):
                    expired_by_turn.add(approval_id)
            elif (payload.get("resolution_kind") == "request_closed"
                    and status in {"expired", "resolved"}
                    and approval_id == last_approval_id
                    and approval_id in expired_by_turn):
                result["approval_late_closure_observed"] = True
        result["approval_turn_terminal_observed"] = (
            last_approval_id in expired_by_turn)
        result["approval_operations"] = int(connection.execute(
            "SELECT COUNT(*) FROM approval_operations AS o "
            "JOIN approvals AS a ON a.approval_id=o.approval_id "
            "WHERE a.run_id=?", (run["run_id"],)).fetchone()[0])
        # The marker can be split across output deltas; check the concatenated
        # stream, not one event. The command prints it first, so a bounded
        # prefix is enough.
        combined = ""
        for row in connection.execute(
                "SELECT event_json FROM events WHERE session_id=? "
                "AND event_json LIKE '%\"type\":\"command.output\"%' "
                "ORDER BY seq", (session_id,)):
            try:
                event = json.loads(row["event_json"])
            except ValueError:
                continue
            if (event.get("run_id") != run["run_id"]
                    or event.get("type") != "command.output"):
                continue
            payload = event.get("payload")
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str):
                continue
            combined += text
            if INTERRUPT_MARKER in combined:
                result["start_signal_observed"] = True
                break
            if len(combined) > 65536:
                break
        return result
    finally:
        connection.close()


def classify_execution_interrupt(evidence: dict) -> dict:
    """A returned RPC or 202 is never enough; only the durable result passes."""
    if not evidence.get("start_signal_observed"):
        return {"status": "failed", "code": "start_signal_timeout"}
    status = evidence.get("run_status")
    if status is None:
        return {"status": "failed", "code": "terminal_timeout"}
    if status == "completed":
        # The command finished before the interrupt won; the natural terminal
        # is a competition result, not an interrupt success.
        return {"status": "failed",
                "code": "terminal_race_natural_completion"}
    if status != "interrupted":
        return {"status": "failed", "code": "unexpected_terminal:" + str(status)}
    if (evidence.get("interrupt_status") != "confirmed"
            or evidence.get("interrupt_outcome") != "run_interrupted"):
        return {"status": "failed", "code": "interrupt_unconfirmed"}
    if evidence.get("active_slot_run_id") is not None:
        return {"status": "failed", "code": "active_slot_not_released"}
    return {"status": "passed"}


def classify_approval_interrupt(evidence: dict) -> dict:
    """Turn termination must invalidate the approval without recording one."""
    status = evidence.get("run_status")
    if status is None:
        return {"status": "failed", "code": "terminal_timeout"}
    if status == "completed":
        return {"status": "failed",
                "code": "terminal_race_natural_completion"}
    if status != "interrupted":
        return {"status": "failed", "code": "unexpected_terminal:" + str(status)}
    if (evidence.get("interrupt_status") != "confirmed"
            or evidence.get("interrupt_outcome") != "run_interrupted"):
        return {"status": "failed", "code": "interrupt_unconfirmed"}
    if evidence.get("active_slot_run_id") is not None:
        return {"status": "failed", "code": "active_slot_not_released"}
    if not evidence.get("approvals"):
        return {"status": "failed", "code": "approval_missing"}
    if evidence.get("pending_approvals"):
        return {"status": "failed", "code": "approval_still_pending"}
    # A later upstream request closure may replace the current
    # resolution_kind, but the turn-terminal expiry must be in the history.
    if (evidence.get("approval_status") != "expired"
            or not evidence.get("approval_turn_terminal_observed")
            or evidence.get("approval_resolution_kind") not in {
                "turn_terminal", "request_closed"}):
        return {"status": "failed", "code": "approval_not_invalidated"}
    if evidence.get("approval_operations") != 0:
        return {"status": "failed", "code": "approval_decision_recorded"}
    return {"status": "passed"}


def app_pid(serial: str) -> str:
    result = adb(["shell", "pidof", "dev.runvori.app"], serial=serial)
    return result.stdout.strip()


def start_app(serial: str) -> None:
    adb(["shell", "am", "start", "-W", "-n",
         "dev.runvori.app/.MainActivity"], serial=serial, timeout=60)
    time.sleep(2)


def ui_contains(serial: str, *texts: str, timeout: float = 30) -> bool:
    """Check the rendered accessibility tree; no screenshots or text dumps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        adb(["shell", "uiautomator", "dump", "/sdcard/runvori-window.xml"],
            serial=serial, timeout=30)
        xml = adb(["shell", "cat", "/sdcard/runvori-window.xml"],
                  serial=serial, timeout=30).stdout
        if all(text in xml for text in texts):
            return True
        time.sleep(1)
    return False


def lifecycle_kill(serial: str, method: str, data_dir: Path) -> dict:
    """Record a system kill and a force-stop as separate transitions.

    Both run against the same pending approval and the same lost receipt. The
    only difference is how Android terminates the process; neither may resend
    the decision or start another run.
    """
    start_app(serial)
    if not ui_contains(serial, "审批详情", "结果待确认"):
        return {"step": "lifecycle_kill", "method": method, "status": "failed",
                "code": "approval_not_restored_before_kill"}
    before_runs = count_runs(data_dir)
    before_ops = count_approval_operations(data_dir)
    adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=serial)
    # `am kill` only terminates cached processes; give the system time to move
    # the stopped Activity to that state before asking for the kill.
    time.sleep(5)
    if method == "system_kill":
        adb(["shell", "am", "kill", "dev.runvori.app"], serial=serial)
    else:
        adb(["shell", "am", "force-stop", "dev.runvori.app"], serial=serial)
    time.sleep(3)
    pid_after = app_pid(serial)
    start_app(serial)
    restored = ui_contains(serial, "审批详情", "结果待确认")
    after_runs = count_runs(data_dir)
    after_ops = count_approval_operations(data_dir)
    ok = (pid_after == "" and restored and after_runs == before_runs
          and after_ops == before_ops)
    return {
        "step": "lifecycle_kill",
        "method": method,
        "status": "ok" if ok else "failed",
        "process_gone": pid_after == "",
        "approval_restored": restored,
        "runs_unchanged": after_runs == before_runs,
        "decisions_unchanged": after_ops == before_ops,
    }


def restart_target(data_dir: Path) -> dict:
    """Capture one terminal devsess0 run before the Host is restarted.

    Only hashes, ids and sizes leave this function; command or artifact
    content never enters the report or an instrumentation argument.
    """
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        active = connection.execute(
            "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
        pending = connection.execute(
            "SELECT COUNT(*) FROM approvals WHERE status='pending'"
        ).fetchone()[0]
        row = connection.execute(
            "SELECT DISTINCT r.run_id FROM runs AS r "
            "JOIN run_artifacts AS a ON a.run_id=r.run_id "
            "WHERE r.session_id='devsess0' "
            "AND r.status IN ('completed','failed','interrupted') "
            "AND a.kind='command_output' AND a.source='command_execution' "
            "AND a.available=1 AND a.representation='text' "
            "AND a.sha256 IS NOT NULL AND a.size_bytes<=65536 "
            "ORDER BY r.rowid DESC LIMIT 1").fetchone()
        if row is None:
            row = connection.execute(
                "SELECT r.run_id FROM runs AS r "
                "JOIN run_commands AS c ON c.run_id=r.run_id "
                "WHERE r.session_id='devsess0' "
                "AND r.status IN ('completed','failed','interrupted') "
                "ORDER BY r.rowid DESC LIMIT 1").fetchone()
        result = {
            "session": "devsess0",
            "active_run_id": None if active is None else active["run_id"],
            "pending_approvals": int(pending),
            "run_id": None,
        }
        if row is None:
            return result
        run_id = row["run_id"]
        result["run_id"] = run_id
        command = connection.execute(
            "SELECT item_id, command FROM run_commands WHERE run_id=? "
            "AND command IS NOT NULL AND command_truncated=0 "
            "ORDER BY COALESCE(started_event_seq, completed_event_seq, 0), "
            "item_id LIMIT 1", (run_id,)).fetchone()
        if command is not None:
            result["command_item_id"] = command["item_id"]
            result["command_sha256"] = hashlib.sha256(
                command["command"].encode("utf-8")).hexdigest()
        artifact = connection.execute(
            "SELECT artifact_id, size_bytes, sha256 FROM run_artifacts "
            "WHERE run_id=? AND kind='command_output' "
            "AND source='command_execution' AND available=1 "
            "AND representation='text' AND sha256 IS NOT NULL "
            "AND size_bytes<=65536 ORDER BY rowid LIMIT 1",
            (run_id,)).fetchone()
        if artifact is not None:
            result["artifact_id"] = artifact["artifact_id"]
            result["artifact_sha256"] = artifact["sha256"]
            result["artifact_size"] = int(artifact["size_bytes"])
        return result
    finally:
        connection.close()


def file_state(path: Path, expected: bytes) -> dict:
    try:
        data = path.read_bytes()
    except OSError:
        return {"status": "failed", "present": False, "matches": False,
                "size_bytes": None, "sha256": None}
    matches = data == expected
    return {"status": "ok" if matches else "failed", "present": True,
            "matches": matches, "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def verification_samples(data_dir: Path) -> dict:
    """Sanitized wrapper classification; no command text is returned."""
    database = data_dir / "host.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute(
            "SELECT v.run_id FROM run_verifications AS v "
            "JOIN runs AS r ON r.run_id=v.run_id "
            "WHERE r.session_id='devsess0' "
            "ORDER BY r.rowid DESC LIMIT 1").fetchone()
        if run is None:
            return {"present": False}
        requirements = connection.execute(
            "SELECT requirement_key, command FROM run_verifications "
            "WHERE run_id=? ORDER BY requirement_key",
            (run["run_id"],)).fetchall()
        commands = connection.execute(
            "SELECT command FROM run_commands WHERE run_id=? "
            "AND command IS NOT NULL AND command_truncated=0 "
            "ORDER BY COALESCE(started_event_seq, completed_event_seq, 0), "
            "item_id", (run["run_id"],)).fetchall()
        samples = []
        for requirement in requirements:
            chosen = None
            classifications = []
            for index, command in enumerate(commands[:16]):
                classification = command_match.classify(
                    command["command"], requirement["command"])
                classifications.append({"index": index, **classification})
                if (chosen is None
                        and classification["kind"] != command_match.UNRECOGNIZED):
                    chosen = classification
            if chosen is None and commands:
                chosen = command_match.classify(
                    commands[0]["command"], requirement["command"])
            samples.append({
                "key": requirement["requirement_key"],
                "command_count": len(commands),
                "commands": classifications,
                **(chosen or {"kind": command_match.UNRECOGNIZED,
                              "inner_exact": False, "length": None}),
            })
        return {"present": True, "requirements": samples}
    finally:
        connection.close()


def start_host(*, data_dir: Path, workspace: Path, certificate: Path,
               key: Path, upstream: list[str],
               expected_version: str | None = None,
               codex_home: Path | None = None,
               real: bool = False,
               listen_port: int = 0,
               listen_host: str = "127.0.0.1") -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "host/src"), str(ROOT / "host/tests")])
    log_path = os.environ.get("RUNVORI_ACCEPT_HOST_LOG")
    stderr_target = subprocess.PIPE
    if log_path:
        # Local diagnosis only; kept under the ignored .dev directory by the
        # caller. The report never reads this file.
        stderr_target = open(log_path, "ab", buffering=0)
    process = subprocess.Popen(
        [sys.executable, "-c", LAUNCHER, "|".join(upstream), str(workspace),
         str(data_dir), str(certificate), str(key), expected_version or "",
         str(codex_home or ""), "1" if real else "0", str(listen_port),
         listen_host],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=stderr_target, text=True)
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
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def provision(data_dir: Path, workspace: Path, sessions: int,
              verification_commands=None, file_workspaces=None) -> None:
    admin = LocalAdminClient(data_dir)
    created = admin.call(
        "register-workspace", workspace_id="ws-device",
        root=str(workspace), sandbox="workspace-write",
        verification_commands=verification_commands)
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
    for workspace_id, root, session_id in file_workspaces or ():
        registered = admin.call(
            "register-workspace", workspace_id=workspace_id,
            root=str(root), sandbox="read-only")
        if registered.get("status") == "failed":
            raise OrchestrationFailure("register_file_workspace_failed")
        result = admin.call(
            "create-session", session_id=session_id,
            workspace_id=workspace_id, title=session_id,
            create_thread=True, request_id=f"thread-{session_id}")
        if result.get("status") == "failed":
            raise OrchestrationFailure("create_file_session_failed:" +
                                       str(result.get("code")))


def run_scenario(serial: str, name: str, method: str,
                 arguments: dict[str, str],
                 port: int | None = None) -> dict:
    class_name = SCENARIO_CLASSES.get(
        name, "dev.runvori.app.DeviceAcceptanceTest")
    command = ["-s", serial, "shell", "am", "instrument", "-w", "-r",
               "-e", "class", f"{class_name}#{method}"]
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
    if not kept:
        # Compose wait timeouts and runner failures do not use the JUnit
        # prefixes above. Keep only diagnostic lines, never request payloads.
        for line in text.splitlines():
            stripped = line.strip()
            if any(word in stripped for word in (
                    "Exception", "Error", "timed out", "Timeout", "FAILED")):
                kept.append(stripped[:600])
    seen, result = set(), []
    for line in kept:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result[-8:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="")
    parser.add_argument("--host-address", default="",
                        help=("address the phone dials (LAN, VPN or DNS); "
                              "defaults to the emulator NAT alias"))
    parser.add_argument("--listen-address", default="",
                        help=("local bind address; defaults to loopback for "
                              "the emulator and the literal Host address "
                              "otherwise"))
    parser.add_argument("--host-port", type=int, default=0,
                        help=("optional fixed TLS port; useful for a firewall "
                              "rule on a physical-device LAN/VPN path"))
    parser.add_argument("--apk", default="",
                        help="debug APK to install and hash before the run")
    parser.add_argument("--test-apk", default="",
                        help="instrumentation APK to install and hash")
    parser.add_argument("--profile", choices=("all", "device-baseline"),
                        default="all",
                        help=("device-baseline is synthetic-only and adds "
                              "offline, recovery and 200%% font checks"))
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
    wanted = {item.strip() for item in args.only.split(",") if item.strip()}
    unknown = wanted - set(ALL_SCENARIOS)
    if unknown:
        print(json.dumps({
            "status": "failed",
            "code": "unknown_scenario:" + ",".join(sorted(unknown))}))
        return 1
    if args.host_port and not 1 <= args.host_port <= 65535:
        print(json.dumps({"status": "failed", "code": "host_port_invalid"}))
        return 1
    if args.profile == "device-baseline" and args.real:
        print(json.dumps({
            "status": "failed", "code": "baseline_is_model_free"}))
        return 1
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
    else:
        forbidden = wanted & REAL_ONLY
        if forbidden:
            print(json.dumps({
                "status": "failed",
                "code": "real_only_scenario:" +
                        ",".join(sorted(forbidden))}))
            return 1
        if not wanted:
            wanted = (set(DEVICE_BASELINE)
                      if args.profile == "device-baseline"
                      else set(ALL_SCENARIOS) - REAL_ONLY - BASELINE_ONLY)
    ordering = DEVICE_BASELINE if args.profile == "device-baseline" \
        else ALL_SCENARIOS
    ordered_wanted = [letter for letter in ordering if letter in wanted]
    ordered_wanted += [letter for letter in ALL_SCENARIOS
                       if letter in wanted and letter not in ordered_wanted]
    expected_model_turns = planned_model_turns(args.real, wanted)
    turn_scenarios = ([letter for letter in ordered_wanted
                       if letter in TURN_SCENARIOS] if args.real else [])
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
        transport = build_transport(
            serial, args.host_address, args.listen_address)
        device = device_properties(serial)
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
            "profile": args.profile,
            "real": args.real,
            "upstream": "real_codex" if args.real else "synthetic_app_server",
            "device": device,
            "transport": transport["report"],
            "scenarios": ordered_wanted,
            "expected_model_turns": expected_model_turns,
            "model_turn_scenarios": turn_scenarios,
            "pairing_bootstrap": ("b" not in wanted
                                  and bool(wanted & CREDENTIAL_SCENARIOS)),
            "not_run": [letter for letter in ALL_SCENARIOS
                        if letter not in wanted],
        }
        if args.real:
            plan.update({
                "codex_version": EXPECTED_REAL_VERSION,
                "prompts": {letter: PROMPTS[letter][0]
                            for letter in turn_scenarios},
                "approval_scopes": {
                    "b": "allow_once:python --version",
                    "l": "allow_once:file_change",
                    "m": "deny:file_change",
                    "v": "allow_once:python --version",
                    "ri": "allow_once:interrupt_probe_sleep_command",
                    "ra": ("no_decision:pending_approval_invalidated_by_"
                           "turn_termination"),
                },
                "interrupt_checks": {
                    letter: INTERRUPT_CHECKS[letter]
                    for letter in ordered_wanted
                    if letter in INTERRUPT_CHECKS},
                "command_match_asserted": False,
            })
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0
    report: dict = {
        "status": "failed",
        "profile": args.profile,
        "real": args.real,
        "upstream": "real_codex" if args.real else "synthetic_app_server",
        "device": device,
        "transport": transport["report"],
        "scenarios": ordered_wanted,
        "expected_model_turns": expected_model_turns,
        "model_turn_scenarios": turn_scenarios,
        "pairing_bootstrap": ("b" not in wanted
                              and bool(wanted & CREDENTIAL_SCENARIOS)),
        "not_run": [letter for letter in ALL_SCENARIOS if letter not in wanted],
        "steps": [],
    }
    if args.real:
        report.update({
            "codex_version": EXPECTED_REAL_VERSION,
            "approval_scopes": {
                "b": "allow_once:python --version",
                "l": "allow_once:file_change",
                "m": "deny:file_change",
                "v": "allow_once:python --version",
                "ri": "allow_once:interrupt_probe_sleep_command",
                "ra": ("no_decision:pending_approval_invalidated_by_"
                       "turn_termination"),
            },
            "interrupt_checks": {
                letter: INTERRUPT_CHECKS[letter]
                for letter in ordered_wanted if letter in INTERRUPT_CHECKS},
            "command_match_asserted": False,
        })
    folder = Path(tempfile.mkdtemp(prefix="device-acceptance-", dir=ROOT / ".dev"))
    host = None
    data_dir = None
    workspace_allow = None
    workspace_deny = None
    try:
        preflight = preflight_device(
            serial, args.apk, args.test_apk, install=True)
        report["device"] = preflight.pop("device")
        report["preflight"] = preflight
        certificate, key = generate_certificate(
            folder, "device",
            extra_sans=([] if transport["san"] == "DNS:localhost"
                        else [transport["san"]]))
        pin = spki_sha256(certificate)
        data_dir = folder / "data"
        data_dir.mkdir()
        workspace = folder / "workspace"
        file_workspaces = []
        if args.real:
            prepare_fixture(ROOT, workspace)
            if "l" in wanted:
                workspace_allow = folder / "workspace-allow"
                prepare_file_approval_fixture(ROOT, workspace_allow)
                file_workspaces.append(
                    ("ws-file-allow", workspace_allow, "devsessallow"))
            if "m" in wanted:
                workspace_deny = folder / "workspace-deny"
                prepare_file_approval_fixture(ROOT, workspace_deny)
                file_workspaces.append(
                    ("ws-file-deny", workspace_deny, "devsessdeny"))
            upstream = [str(codex), "app-server", "--listen", "stdio://"]
        else:
            workspace.mkdir()
            upstream = [sys.executable, str(FAKE), "host_api_device"]
        host = start_host(
            data_dir=data_dir, workspace=workspace, certificate=certificate,
            key=key, upstream=upstream,
            expected_version=EXPECTED_REAL_VERSION if args.real else None,
            codex_home=codex_home, real=args.real,
            listen_host=transport["listen"],
            listen_port=args.host_port)
        admin = LocalAdminClient(data_dir)
        status = admin.call("status")
        api = status.get("api") or {}
        port = api.get("listen_port")
        if status.get("status") != "running" or not isinstance(port, int) \
                or not port:
            raise OrchestrationFailure("host_api_not_listening")
        if api.get("spki_sha256") != pin:
            raise OrchestrationFailure("advertised_pin_mismatch")
        provision(
            data_dir, workspace, args.sessions,
            verification_commands=[
                {"key": "python", "command": "python --version"}],
            file_workspaces=file_workspaces)
        if args.real:
            sessions = [f"devsess{index}"
                        for index in range(args.sessions)]
            sessions += [item[2] for item in file_workspaces]
            wait_for_thread_bindings(data_dir, sessions)
        report["steps"].append({"step": "host_running", "sessions": args.sessions,
                                "port": port})
        # Device -> Host transport. Emulator runs keep the historical NAT alias
        # and loopback bind; a physical phone uses the supplied LAN/VPN/DNS
        # address. The certificate names exactly the dialled endpoint, and the
        # client still verifies chain, hostname and SPKI pin.
        endpoint = transport["endpoint"]
        report["steps"].append({"step": "transport",
                                **transport["report"], "status": "ok"})

        pairing = admin.call("issue-pairing")
        if pairing.get("status") != "issued":
            raise OrchestrationFailure("pairing_issue_failed")
        # The payload is passed to the device process through instrumentation
        # arguments; it never enters a URL, a file or a log.
        # `am instrument -e` splits on whitespace and mangles quotes, so the
        # pairing payload travels as base64 and is decoded on the device. The
        # The Host prints `localhost`, which on the device means the device
        # itself, so the endpoint is rewritten to the address the phone dials.
        pairing = dict(pairing)
        pairing["https_url"] = f"https://{transport['url_host']}:{port}/v1"
        encoded_payload = base64.b64encode(json.dumps(
            pairing, ensure_ascii=False).encode("utf-8")).decode("ascii")
        base_arguments = {
            "pairing_info_b64": encoded_payload,
            "host_port": str(port),
        }
        if args.real:
            base_arguments["real_mode"] = "1"

        def scenario_arguments(letter: str) -> dict[str, str]:
            values = dict(base_arguments)
            prompt = PROMPTS.get(letter)
            if args.real and prompt is not None:
                values[prompt[0]] = base64.b64encode(
                    prompt[1].encode("utf-8")).decode("ascii")
            return values

        def run_letter(letter: str) -> dict:
            # Every scenario that starts or drives a run begins from an idle
            # Host. `e` only checks that a revoked read is refused and may
            # follow the intentional pending approval from `u`/`y`/`z`.
            slot = active_slot_run(data_dir) if letter != "e" else None
            if slot is not None:
                report["steps"].append({
                    "step": "active_slot_before", "scenario": letter,
                    "status": "failed", "code": "active_slot_before"})
                raise OrchestrationFailure("active_slot_before:" + letter)
            step = run_scenario(serial, letter, METHODS[letter],
                                scenario_arguments(letter), port)
            report["steps"].append(step)
            return step

        def require_real_turn(letter: str, step: dict) -> None:
            if args.real and letter in TURN_SCENARIOS \
                    and step["status"] != "passed":
                report["stopped_after_failure"] = letter
                raise OrchestrationFailure("real_scenario_failed:" + letter)

        for letter in ("s", "a", "b"):
            if letter in wanted:
                require_real_turn(letter, run_letter(letter))
        # b pairs itself. Any other credential-dependent selection needs the
        # one-shot pairing payload consumed once, before its first scenario.
        if "b" not in wanted and wanted & CREDENTIAL_SCENARIOS:
            if run_letter("p")["status"] != "passed":
                raise OrchestrationFailure("pairing_bootstrap_failed")
        for letter in ("c", "d", "f", "h", "g", "o", "q", "w", "x", "s3"):
            if letter in wanted:
                run_letter(letter)
        if "i" in wanted:
            configured = admin.call(
                "register-workspace", workspace_id="ws-device",
                root=str(workspace), sandbox="workspace-write",
                verification_commands=[{"key": "failure", "command": "exit 7"}])
            count = configured.get("verification_count")
            report["steps"].append({
                "step": "verification_config",
                "status": ("ok" if type(count) is int and count > 0
                           else "failed"),
                "count": count})
            run_letter("i")
        if "l" in wanted:
            step = run_letter("l")
            report["steps"].append({
                **file_state(workspace_allow / FILE_APPROVAL_NAME,
                             FILE_APPROVAL_ALLOWED),
                "step": "file_after_allow"})
            require_real_turn("l", step)
        if "m" in wanted:
            step = run_letter("m")
            report["steps"].append({
                **file_state(workspace_deny / FILE_APPROVAL_NAME,
                             FILE_APPROVAL_BEFORE),
                "step": "file_after_deny"})
            require_real_turn("m", step)
        if "v" in wanted:
            configured = admin.call(
                "register-workspace", workspace_id="ws-device",
                root=str(workspace), sandbox="workspace-write",
                verification_commands=[
                    {"key": "python", "command": "python --version"}])
            count = configured.get("verification_count")
            report["steps"].append({
                "step": "verification_config_v",
                "status": ("ok" if type(count) is int and count > 0
                           else "failed"),
                "count": count})
            step = run_letter("v")
            if step["status"] == "passed":
                report["command_match_asserted"] = True
            require_real_turn("v", step)
        def append_interrupt_check(
                letter: str, session_id: str, classifier,
                phone_test_passed: bool) -> dict:
            """Record Host-side evidence even when the phone assertion failed."""
            try:
                evidence = interrupt_evidence(data_dir, session_id)
            except (OSError, sqlite3.Error):
                report["steps"].append({
                    "step": "interrupt_evidence", "scenario": letter,
                    "status": "failed", "code": "evidence_unreadable",
                    "phone_test_passed": phone_test_passed})
                return {"status": "failed", "code": "evidence_unreadable"}
            verdict = classifier(evidence)
            step = {"step": "interrupt_evidence", "scenario": letter,
                    "status": verdict["status"],
                    "phone_test_passed": phone_test_passed}
            step.update({key: value for key, value in evidence.items()
                         if key != "session"})
            if "code" in verdict:
                step["code"] = verdict["code"]
            report["steps"].append(step)
            return verdict

        if "ri" in wanted:
            # One model turn. The phone interrupts only after the real command
            # output proves execution started; the Host record decides whether
            # the interrupt or a natural completion won.
            step = run_letter("ri")
            verdict = append_interrupt_check(
                "ri", "devsess0", classify_execution_interrupt,
                phone_test_passed=step["status"] == "passed")
            if step["status"] != "passed" or verdict["status"] != "passed":
                report["stopped_after_failure"] = "ri"
                raise OrchestrationFailure("real_scenario_failed:ri")
        if "ra" in wanted:
            # One model turn. The phone keeps the approval page open and ends
            # the turn through the product interrupt entry; the old request
            # must be invalidated without recording a decision.
            step = run_letter("ra")
            verdict = append_interrupt_check(
                "ra", "devsess1", classify_approval_interrupt,
                phone_test_passed=step["status"] == "passed")
            if step["status"] != "passed" or verdict["status"] != "passed":
                report["stopped_after_failure"] = "ra"
                raise OrchestrationFailure("real_scenario_failed:ra")
        if "r" in wanted:
            target = restart_target(data_dir)
            report["restart_target"] = {
                key: target[key] for key in (
                    "session", "run_id", "command_item_id", "command_sha256",
                    "artifact_id", "artifact_sha256", "artifact_size",
                    "active_run_id", "pending_approvals")
                if key in target}
            if target["active_run_id"] is not None:
                raise OrchestrationFailure("restart_run_active")
            if target["pending_approvals"]:
                raise OrchestrationFailure("restart_approval_pending")
            if target["run_id"] is None or not (
                    "command_sha256" in target or "artifact_sha256" in target):
                raise OrchestrationFailure("restart_target_missing")
            before_status = admin.call("status")
            before_epoch = before_status.get("epoch")
            stop_host(host)
            host = None
            host = start_host(
                data_dir=data_dir, workspace=workspace,
                certificate=certificate, key=key, upstream=upstream,
                expected_version=(EXPECTED_REAL_VERSION if args.real
                                  else None),
                codex_home=codex_home, real=args.real, listen_port=port,
                listen_host=transport["listen"])
            admin = LocalAdminClient(data_dir)
            after_status = admin.call("status")
            after_api = after_status.get("api") or {}
            port_same = after_api.get("listen_port") == port
            epoch_advanced = (type(before_epoch) is int
                              and type(after_status.get("epoch")) is int
                              and after_status["epoch"] > before_epoch)
            restart_ok = (after_status.get("status") == "running"
                          and port_same and epoch_advanced
                          and after_status.get("active_run_id") is None)
            report["steps"].append({
                "step": "restart",
                "status": "ok" if restart_ok else "failed",
                "port_same": port_same,
                "epoch_advanced": epoch_advanced,
            })
            if not restart_ok:
                raise OrchestrationFailure("restart_state_mismatch")
            restart_arguments = scenario_arguments("r")
            restart_arguments.update({
                "restart_session": target["session"],
                "restart_run_id": target["run_id"],
                "restart_command_item_id": target.get("command_item_id", ""),
                "restart_command_sha256": target.get("command_sha256", ""),
                "restart_artifact_id": target.get("artifact_id", ""),
                "restart_artifact_sha256": target.get("artifact_sha256", ""),
                "restart_artifact_size": str(target.get("artifact_size", "")),
            })
            step = run_scenario(serial, "r", METHODS["r"],
                                restart_arguments, port)
            report["steps"].append(step)
            after_target = restart_target(data_dir)
            same = (after_target.get("run_id") == target["run_id"]
                    and after_target.get("command_sha256")
                    == target.get("command_sha256")
                    and after_target.get("artifact_sha256")
                    == target.get("artifact_sha256"))
            report["steps"].append({
                "step": "restart_state",
                "status": "ok" if same else "failed",
                "run_same": after_target.get("run_id") == target["run_id"],
                "command_same": (after_target.get("command_sha256")
                                 == target.get("command_sha256")),
                "artifact_same": (after_target.get("artifact_sha256")
                                  == target.get("artifact_sha256")),
            })
        for letter in ("j", "k", "n"):
            if letter in wanted:
                run_letter(letter)
        # Stable reading under continuous synthetic output; the run ends on
        # its own, so the active slot is free again for what follows.
        for letter in ("rd", "rk"):
            if letter in wanted:
                run_letter(letter)
        if wanted & {"off", "rec"}:
            # Offline is simulated by stopping the Host: the app sees the same
            # unreachable endpoint it would see after a network loss. The
            # Host is restarted on the same port and identity before recovery
            # is checked, so no TLS or identity check is weakened.
            before_status = admin.call("status")
            before_epoch = before_status.get("epoch")
            before_runs = count_runs(data_dir)
            before_ops = count_approval_operations(data_dir)
            if "off" in wanted:
                stop_host(host)
                host = None
                report["steps"].append({
                    "step": "offline_host_stopped", "status": "ok"})
                run_letter("off")
            if host is None:
                host = start_host(
                    data_dir=data_dir, workspace=workspace,
                    certificate=certificate, key=key, upstream=upstream,
                    expected_version=(EXPECTED_REAL_VERSION if args.real
                                      else None),
                    codex_home=codex_home, real=args.real, listen_port=port,
                    listen_host=transport["listen"])
                admin = LocalAdminClient(data_dir)
                after_status = admin.call("status")
                after_api = after_status.get("api") or {}
                restart_ok = (
                    after_status.get("status") == "running"
                    and after_api.get("listen_port") == port
                    and type(before_epoch) is int
                    and type(after_status.get("epoch")) is int
                    and after_status["epoch"] > before_epoch
                    and after_status.get("active_run_id") is None)
                report["steps"].append({
                    "step": "offline_restart",
                    "status": "ok" if restart_ok else "failed",
                    "port_same": after_api.get("listen_port") == port,
                    "epoch_advanced": (
                        type(before_epoch) is int
                        and type(after_status.get("epoch")) is int
                        and after_status["epoch"] > before_epoch),
                })
                if not restart_ok:
                    raise OrchestrationFailure("offline_restart_mismatch")
            if "rec" in wanted:
                run_letter("rec")
            after_runs = count_runs(data_dir)
            after_ops = count_approval_operations(data_dir)
            unchanged = (after_runs == before_runs
                         and after_ops == before_ops)
            report["steps"].append({
                "step": "offline_state",
                "status": "ok" if unchanged else "failed",
                "runs_unchanged": after_runs == before_runs,
                "decisions_unchanged": after_ops == before_ops,
            })
        if "fs" in wanted:
            previous = adb(
                ["shell", "settings", "get", "system", "font_scale"],
                serial=serial).stdout.strip()
            changed = adb(
                ["shell", "settings", "put", "system", "font_scale", "2.0"],
                serial=serial)
            adb(["shell", "am", "force-stop", "dev.runvori.app"],
                serial=serial)
            observed = adb(
                ["shell", "settings", "get", "system", "font_scale"],
                serial=serial).stdout.strip()
            try:
                run_letter("fs")
            finally:
                if previous in ("", "null"):
                    adb(["shell", "settings", "delete", "system",
                         "font_scale"], serial=serial)
                else:
                    adb(["shell", "settings", "put", "system", "font_scale",
                         previous], serial=serial)
                adb(["shell", "am", "force-stop", "dev.runvori.app"],
                    serial=serial)
            report["steps"].append({
                "step": "font_scale",
                "status": ("ok" if changed.returncode == 0
                           and observed == "2.0" else "failed"),
                "requested": "2.0",
                "observed": observed,
            })
        if wanted & {"y", "z", "s2"}:
            setup = run_scenario(serial, "u", METHODS["u"],
                                 scenario_arguments("u"), port)
            report["steps"].append(setup)
            if setup["status"] != "passed":
                raise OrchestrationFailure("process_loss_setup_failed")
            for letter, method in (("y", "system_kill"),
                                   ("z", "force-stop")):
                if letter in wanted:
                    report["steps"].append(
                        lifecycle_kill(serial, method, data_dir))
            if "s2" in wanted:
                stale_id = latest_pending_approval(data_dir)
                if stale_id is None:
                    raise OrchestrationFailure("stale_approval_missing")
                before_status = admin.call("status")
                before_epoch = before_status.get("epoch")
                stop_host(host)
                host = None
                host = start_host(
                    data_dir=data_dir, workspace=workspace,
                    certificate=certificate, key=key, upstream=upstream,
                    expected_version=(EXPECTED_REAL_VERSION if args.real
                                      else None),
                    codex_home=codex_home, real=args.real, listen_port=port,
                listen_host=transport["listen"])
                admin = LocalAdminClient(data_dir)
                after_status = admin.call("status")
                after_api = after_status.get("api") or {}
                restart_ok = (
                    after_status.get("status") == "running"
                    and after_api.get("listen_port") == port
                    and type(before_epoch) is int
                    and type(after_status.get("epoch")) is int
                    and after_status["epoch"] > before_epoch
                    and after_status.get("active_run_id") is None)
                report["steps"].append({
                    "step": "stale_restart",
                    "status": "ok" if restart_ok else "failed",
                    "port_same": after_api.get("listen_port") == port,
                    "epoch_advanced": (
                        type(before_epoch) is int
                        and type(after_status.get("epoch")) is int
                        and after_status["epoch"] > before_epoch),
                    "pending_before": True,
                    "pending_after": latest_pending_approval(data_dir)
                                     is not None,
                })
                if not restart_ok:
                    raise OrchestrationFailure("stale_restart_mismatch")
                arguments = scenario_arguments("s2")
                arguments["stale_approval_id"] = stale_id
                report["steps"].append(run_scenario(
                    serial, "s2", METHODS["s2"], arguments, port))
        if "t" in wanted:
            run_letter("t")
        # Revoke on the Host, then the revoked device must be refused.
        if "e" in wanted:
            revoked = admin.call("revoke-device")
            report["steps"].append({
                "step": "revoke", "status": revoked.get("status")})
            run_letter("e")
        checks = {"file_after_allow", "file_after_deny", "restart",
                  "restart_state", "verification_config_v", "lifecycle_kill",
                  "stale_restart", "offline_host_stopped",
                  "offline_restart", "offline_state", "font_scale",
                  "active_slot_before"}
        failures = [step for step in report["steps"]
                    if step.get("status") not in ("passed", "ok")
                    and (step.get("scenario") or step.get("step") in checks)]
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
            # A Host that is still running is stopped by the orchestrator; only
            # a process that ended on its own gets an exit diagnosis in the
            # report.
            if host.poll() is not None:
                report["host_exit_code"] = host.returncode
                if data_dir is not None:
                    try:
                        finished = json.loads(
                            (data_dir / "run-result.json").read_text(
                                encoding="utf-8"))
                        if isinstance(finished, dict):
                            report["host_exit_reason"] = finished.get("reason")
                            report["host_exit_cause"] = finished.get("cause")
                    except (OSError, ValueError):
                        pass
                if host.stdout is not None:
                    try:
                        tail = host.stdout.read()
                        for line in reversed(tail.splitlines()):
                            try:
                                payload = json.loads(line)
                            except ValueError:
                                continue
                            result = payload.get("run")
                            if isinstance(result, dict):
                                report["host_exit_reason"] = result.get("reason")
                                report["host_exit_cause"] = result.get("cause")
                                break
                    except OSError:
                        pass
            stop_host(host)
        if args.real and data_dir is not None and data_dir.exists():
            try:
                observed = count_runs(data_dir)
            except (OSError, sqlite3.Error):
                observed = None
            report["observed_runs"] = observed
            try:
                report["command_samples"] = verification_samples(data_dir)
            except (OSError, sqlite3.Error):
                report["command_samples"] = {
                    "present": False, "error": "unreadable"}
            if report.get("status") == "passed" \
                    and not turn_count_matches(expected_model_turns, observed):
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
        else:
            # Local pointer only; the folder holds raw prompts and commands
            # and must not become part of the sanitized report or evidence.
            print(f"kept_folder={folder}", file=sys.stderr, flush=True)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    sys.exit(main())