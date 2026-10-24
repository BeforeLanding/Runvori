"""Opt-in, local M0 execution probe. Never logs upstream message values."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from runvori.codex.stdio import RpcError, StdioClient, TransportError

MARKER = ".runvori-disposable-fixture"
SAFE_KEYS = frozenset({
    "threadId", "turnId", "turn", "id", "status", "item", "itemId", "type",
    "approvalId", "requestId", "reason", "command", "cwd", "startedAtMs",
    "method", "params", "result", "error", "items", "itemType", "delta",
    "message", "content", "output", "exitCode", "durationMs", "completedAt",
    "startedAt", "fileChanges", "changes", "path", "kind", "decision",
    "approvalId", "availableDecisions", "commandActions", "environmentId",
    "networkApprovalContext", "proposedExecpolicyAmendment",
    "proposedNetworkPolicyAmendments", "grantRoot",
})
PROMPT = (
    "This is a disposable Runvori protocol probe. Read the fixture marker, "
    "create probe-result.txt containing exactly RUNVORI_PROBE_OK followed by a newline, "
    "then run a command that verifies its exact contents. Do not access paths "
    "outside this workspace or use the network."
)
SAFE_COMMAND = "python --version"
WRITE_COMMAND = (
    "Set-Content -LiteralPath probe-result.txt -Value RUNVORI_PROBE_OK -NoNewline"
)
INTERRUPT_MARKER = "RUNVORI_SLEEP_STARTED"
INTERRUPT_COMMAND = f"Write-Output {INTERRUPT_MARKER}; Start-Sleep -Seconds 60"
ACCEPT_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact command {SAFE_COMMAND} and report its result. Do not use "
    "other commands, access paths outside this workspace, or use the network."
)
WRITE_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact PowerShell command {WRITE_COMMAND} and report its result. "
    "Do not use other commands, access paths outside this workspace, or use the network."
)
INTERRUPT_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact PowerShell command {INTERRUPT_COMMAND}. Wait for its result. "
    "Do not use other commands, access paths outside this workspace, or use the network."
)
FILE_CHANGE_PROMPT = (
    "This is a disposable Runvori protocol probe. Use only the apply_patch tool "
    "to add probe-result.txt with exactly one line RUNVORI_PROBE_OK. "
    "Try the patch once even if the workspace is read-only; if approval is required, "
    "request it. Do not use shell commands, other tools, other paths, or the network."
)
COMMAND_APPROVAL_KEYS = frozenset({
    "approvalId", "availableDecisions", "command", "commandActions", "cwd", "environmentId",
    "itemId", "kind", "networkApprovalContext", "proposedExecpolicyAmendment",
    "proposedNetworkPolicyAmendments", "reason", "startedAtMs", "threadId", "turnId",
})
FILE_CHANGE_APPROVAL_KEYS = frozenset({
    "threadId", "turnId", "itemId", "startedAtMs", "reason", "grantRoot",
})
FILE_CHANGE_BYTES = b"RUNVORI_PROBE_OK\n"
PENDING_RESOLUTION_GRACE_SECONDS = 2.0
COMMAND_COMPLETION_RACE_DELAY_SECONDS = 5.0


def is_safe_command(command: object, expected: str = SAFE_COMMAND) -> bool:
    if not isinstance(command, str):
        return False
    if command.strip() == expected:
        return True
    # The pinned Windows CLI wraps tool commands with its bundled pwsh.
    executable = (Path.home() / ".cache/codex-runtimes/codex-primary-runtime"
                  / "dependencies/native/powershell/pwsh.exe")
    if not executable.is_file():
        return False
    quoted_executable = f'"{str(executable).replace(chr(92), chr(92) * 2)}"'
    return command in (
        f"{quoted_executable} -Command '{expected}'",
        f"{quoted_executable} -NoProfile -Command '{expected}'",
    )


def safe_accept_decision(event: dict, *, target: Path, thread_id: str,
                         turn_id: str, expected: str = SAFE_COMMAND) -> tuple[str, str]:
    """Only the selected fixture command may receive a one-shot accept."""
    if event.get("method") != "item/commandExecution/requestApproval":
        return "decline", "unsupported_approval_type"
    params = event.get("params")
    if not isinstance(params, dict) or set(params) - COMMAND_APPROVAL_KEYS:
        return "decline", "unexpected_approval_fields"
    if (params.get("threadId") != thread_id or params.get("turnId") != turn_id
            or not isinstance(params.get("itemId"), str)
            or params.get("kind", "command") != "command"
            or not is_safe_command(params.get("command"), expected)):
        return "decline", "operation_not_allowlisted"
    # environmentId selects the CLI runtime; a proposed execpolicy rule is
    # not applied by the distinct one-shot `accept` decision.
    if (params.get("networkApprovalContext") is not None
            or params.get("proposedNetworkPolicyAmendments") is not None):
        return "decline", "expanded_scope"
    available = params.get("availableDecisions")
    if available is not None and (not isinstance(available, list) or "accept" not in available):
        return "decline", "accept_unavailable"
    cwd = params.get("cwd")
    try:
        cwd_matches = isinstance(cwd, str) and Path(cwd).resolve() == target
    except (OSError, ValueError):
        cwd_matches = False
    if not cwd_matches:
        return "decline", "cwd_mismatch"
    return "accept", "allowlisted_fixture_command"


def approval_review_shape(event: dict, target: Path, expected: str = SAFE_COMMAND) -> dict:
    """Only field names and equality flags; never command or path values."""
    params = event.get("params")
    if not isinstance(params, dict):
        return {"params_object": False}
    unknown = set(params) - COMMAND_APPROVAL_KEYS
    command = params.get("command")
    try:
        cwd_exact = (isinstance(params.get("cwd"), str)
                     and Path(params["cwd"]).resolve() == target)
    except (OSError, ValueError):
        cwd_exact = False
    return {
        "params_object": True,
        "unexpected_fields": sorted(
            key if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", key)
            else "other:" + hashlib.sha256(str(key).encode()).hexdigest()
            for key in unknown),
        "command_exact": command == expected,
        "command_allowlisted": is_safe_command(command, expected),
        "command_trimmed_exact": isinstance(command, str) and command.strip() == expected,
        "command_contains_exact": isinstance(command, str) and expected in command,
        "command_length_delta": (len(command) - len(expected)
                                 if isinstance(command, str) else None),
        "cwd_exact": cwd_exact,
        "scope_fields_present": sorted(
            key for key in ("environmentId", "networkApprovalContext",
                            "proposedExecpolicyAmendment", "proposedNetworkPolicyAmendments")
            if params.get(key) is not None),
    }


def file_change_review_shape(event: dict, thread_id: str, turn_id: str) -> dict:
    """Only structural and correlation flags; never a patch or path value."""
    params = event.get("params")
    if not isinstance(params, dict):
        return {"params_object": False}
    unknown = set(params) - FILE_CHANGE_APPROVAL_KEYS
    return {
        "params_object": True,
        "unexpected_fields": sorted(
            key if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", key)
            else "other:" + hashlib.sha256(str(key).encode()).hexdigest()
            for key in unknown),
        "thread_exact": params.get("threadId") == thread_id,
        "turn_exact": params.get("turnId") == turn_id,
        "item_id_present": isinstance(params.get("itemId"), str),
        "grant_root_kind": type(params.get("grantRoot")).__name__,
    }


def exact_file_change_item(item: object, target: Path) -> bool:
    """Match the entire one-file addition before a one-shot file approval."""
    if not isinstance(item, dict) or set(item) != {"type", "id", "changes", "status"}:
        return False
    if (item.get("type") != "fileChange" or not isinstance(item.get("id"), str)
            or not isinstance(item.get("changes"), list)
            or len(item["changes"]) != 1):
        return False
    change = item["changes"][0]
    if (not isinstance(change, dict) or set(change) != {"path", "kind", "diff"}
            or change.get("kind") != {"type": "add"}
            or change.get("diff") != FILE_CHANGE_BYTES.decode("ascii")):
        return False
    path = change.get("path")
    try:
        return (isinstance(path, str) and Path(path).is_absolute()
                and not (target / "probe-result.txt").exists()
                and Path(path).resolve() == target / "probe-result.txt")
    except (OSError, ValueError):
        return False


def safe_file_change_decision(event: dict, *, thread_id: str, turn_id: str,
                              candidate_id: str | None,
                              candidate_valid: bool) -> tuple[str, str]:
    if event.get("method") != "item/fileChange/requestApproval":
        return "decline", "unsupported_approval_type"
    params = event.get("params")
    if not isinstance(params, dict) or set(params) - FILE_CHANGE_APPROVAL_KEYS:
        return "decline", "unexpected_approval_fields"
    if (params.get("threadId") != thread_id or params.get("turnId") != turn_id
            or not isinstance(params.get("itemId"), str)
            or params.get("itemId") != candidate_id or not candidate_valid
            or type(params.get("startedAtMs")) is not int):
        return "decline", "file_change_item_not_allowlisted"
    if params.get("grantRoot") is not None:
        return "decline", "expanded_scope"
    return "accept", "allowlisted_fixture_file_change"


def fixture_path(root: Path, fixture: Path) -> Path:
    root = root.resolve()
    scratch = (root / ".dev").resolve()
    target = fixture.resolve()
    if not scratch.is_relative_to(root) or not target.is_relative_to(scratch):
        raise TransportError("fixture_outside_workspace_scratch")
    if target == scratch:
        raise TransportError("fixture_must_be_child_directory")
    return target


def prepare_fixture(root: Path, fixture: Path) -> Path:
    target = fixture_path(root, fixture)
    if target.exists() and any(target.iterdir()):
        raise TransportError("fixture_not_empty")
    target.mkdir(parents=True, exist_ok=True)
    (target / MARKER).write_text("Runvori disposable execution probe\n", encoding="utf-8")
    result = subprocess.run(["git", "init", "--quiet", str(target)],
                            capture_output=True, timeout=15, check=False)
    if result.returncode:
        raise TransportError("fixture_git_init_failed")
    return target


def validate_fixture(root: Path, fixture: Path) -> Path:
    target = fixture_path(root, fixture)
    if (not target.is_dir() or set(path.name for path in target.iterdir()) != {MARKER, ".git"}
            or (target / MARKER).is_symlink() or (target / ".git").is_symlink()
            or (target / MARKER).read_text(encoding="utf-8") != (
                "Runvori disposable execution probe\n") or not (target / ".git").is_dir()):
        raise TransportError("invalid_disposable_fixture")
    if (target / "probe-result.txt").exists():
        raise TransportError("fixture_already_used")
    return target


def shape(value: object, depth: int = 0) -> object:
    """Preserve field names/types; replace every upstream scalar value."""
    if depth >= 5:
        return "depth_limit"
    if isinstance(value, dict):
        result = {key: shape(item, depth + 1) for key, item in value.items()
                  if key in SAFE_KEYS}
        if len(result) != len(value):
            result["unlisted_field_count"] = len(value) - len(result)
        return result
    if isinstance(value, list):
        return [shape(item, depth + 1) for item in value[:3]]
    return type(value).__name__


def record_event_shape(report: dict, event: dict) -> str:
    method = event.get("method")
    if not isinstance(method, str):
        raise TransportError("invalid_notification_method")
    safe_method = (method if re.fullmatch(r"[A-Za-z][A-Za-z0-9/_-]{0,79}", method)
                   and method.split("/")[0] in {"thread", "turn", "item", "serverRequest"}
                   else "other:" + hashlib.sha256(method.encode()).hexdigest())
    report["events"].append({"method": safe_method, "shape": shape(event.get("params"))})
    if len(report["events"]) > 200:
        raise TransportError("probe_event_limit")
    return method


def run_probe(*, root: Path, fixture: Path, account_home: Path,
              executable: str, decision: str, scenario: str = "read_only",
              timeout: float = 120) -> dict:
    if decision not in ("accept", "decline", "defer"):
        raise ValueError("invalid_approval_decision")
    if scenario not in ("read_only", "write_file", "interrupt", "interrupt_pending",
                        "interrupt_on_approval", "file_change_decline",
                        "file_change_accept", "file_change_cut_after_accept",
                        "interrupt_after_turn_completed",
                        "interrupt_after_command_completed",
                        "interrupt_after_command_completed_delayed"):
        raise ValueError("invalid_probe_scenario")
    if scenario in ("write_file", "interrupt", "interrupt_on_approval",
                    "interrupt_after_turn_completed",
                    "interrupt_after_command_completed",
                    "interrupt_after_command_completed_delayed") and decision != "accept":
        raise ValueError("scenario_requires_accept_mode")
    if (scenario == "interrupt_pending") != (decision == "defer"):
        raise ValueError("pending_scenario_requires_defer_mode")
    if scenario == "file_change_decline" and decision != "decline":
        raise ValueError("file_change_scenario_requires_decline_mode")
    if scenario in ("file_change_accept", "file_change_cut_after_accept") and decision != "accept":
        raise ValueError("file_change_scenario_requires_accept_mode")
    if timeout <= 0 or timeout > 600:
        raise ValueError("invalid_probe_timeout")
    target = validate_fixture(root, fixture)
    home = account_home.resolve(strict=True)
    if not home.is_dir() or home == target or home.is_relative_to(target):
        raise TransportError("invalid_account_home")
    env = dict(os.environ, CODEX_HOME=str(home))
    for key in list(env):
        if any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
            del env[key]
    expected_command = (WRITE_COMMAND if scenario == "write_file" else
                        INTERRUPT_COMMAND if scenario in ("interrupt", "interrupt_on_approval")
                        else SAFE_COMMAND)
    prompt = (FILE_CHANGE_PROMPT if scenario in ("file_change_decline", "file_change_accept",
                                                 "file_change_cut_after_accept")
              else
              PROMPT if decision == "decline" else
              WRITE_PROMPT if scenario == "write_file" else
              INTERRUPT_PROMPT if scenario in ("interrupt", "interrupt_on_approval")
              else ACCEPT_PROMPT)
    report: dict = {"schema": 1, "status": "started", "approval_decision": decision,
                    "scenario": scenario,
                    "events": [], "approval_count": 0,
                    "file_change_approval_count": 0, "command_approval_count": 0,
                    "turn_status": "unknown",
                    "resume": "not_attempted", "approval_checks": [],
                    "accepted_command_exit_zero": None,
                    "accepted_command_exit_kind": "not_observed",
                    "interrupt_rpc": "not_requested", "interrupt_trigger": None,
                    "interrupt_marker_seen": False,
                    "pending_request_resolved": None,
                    "pending_request_allowlisted": None,
                    "accepted_file_change_status": "not_observed",
                    "file_change_item_allowlisted": None,
                    "fixture_only_expected_file": None,
                    "fixture_entries_within_allowlist": None,
                    "cutoff": None, "managed_process_close_returned": None,
                    "interrupt_delay_ms": None}
    accepted_items: set[str] = set()
    file_change_candidate_id: str | None = None
    file_change_candidate_valid = False
    accepted_file_change_id: str | None = None
    accepted_at: float | None = None
    pending_request_id: str | int | None = None
    cutoff_after_accept = False
    deadline = time.monotonic() + timeout
    with StdioClient([executable, "app-server", "--listen", "stdio://"],
                     cwd=target, env=env, timeout=15) as client:
        client.initialize()
        started = client.request("thread/start", {
            "cwd": str(target), "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandbox": ("read-only" if scenario in (
                "file_change_decline", "file_change_accept", "file_change_cut_after_accept")
                        else "workspace-write"),
            "ephemeral": False,
        })
        thread_id = started.get("thread", {}).get("id")
        if not isinstance(thread_id, str):
            raise TransportError("thread_start_shape_mismatch")
        begun = client.request("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": prompt}],
        })
        turn_id = begun.get("turn", {}).get("id")
        if not isinstance(turn_id, str):
            raise TransportError("turn_start_shape_mismatch")
        while time.monotonic() < deadline:
            if (scenario == "interrupt" and accepted_at is not None
                    and report["interrupt_rpc"] == "not_requested"
                    and (report["interrupt_marker_seen"]
                         or time.monotonic() - accepted_at >= 2)):
                report["interrupt_trigger"] = (
                    "output_marker" if report["interrupt_marker_seen"] else "accept_wait")
                client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                report["interrupt_rpc"] = "responded"
            event = client.next_event(max(0.001, min(1.0, deadline - time.monotonic())))
            if event is None:
                continue
            method = record_event_shape(report, event)
            if scenario in ("file_change_accept", "file_change_cut_after_accept") and method in ("item/started", "item/updated"):
                params = event.get("params", {})
                item = params.get("item", {}) if isinstance(params, dict) else {}
                if isinstance(item, dict) and item.get("type") == "fileChange":
                    item_id = item.get("id")
                    if (params.get("threadId") != thread_id
                            or params.get("turnId") != turn_id
                            or not isinstance(item_id, str)
                            or (file_change_candidate_id is not None
                                and file_change_candidate_id != item_id)):
                        file_change_candidate_valid = False
                    elif file_change_candidate_id is None and method != "item/started":
                        file_change_candidate_valid = False
                    elif file_change_candidate_id is None:
                        file_change_candidate_valid = exact_file_change_item(item, target)
                    else:
                        file_change_candidate_valid = (file_change_candidate_valid
                                                       and exact_file_change_item(item, target))
                    if file_change_candidate_id is None and isinstance(item_id, str):
                        file_change_candidate_id = item_id
            if "id" in event:
                report["approval_count"] += 1
                if method == "item/fileChange/requestApproval":
                    report["file_change_approval_count"] += 1
                if method == "item/commandExecution/requestApproval":
                    report["command_approval_count"] += 1
                if scenario == "interrupt_pending":
                    verdict, reason = safe_accept_decision(
                        event, target=target, thread_id=thread_id, turn_id=turn_id,
                        expected=SAFE_COMMAND)
                    report["pending_request_allowlisted"] = verdict == "accept"
                    report["approval_checks"].append({
                        "decision": "defer", "reason": "pending_interrupt_probe",
                        "review": approval_review_shape(event, target, SAFE_COMMAND),
                    })
                    if verdict != "accept":
                        raise TransportError("pending_approval_not_allowlisted:" + reason)
                    if report["interrupt_rpc"] == "not_requested":
                        pending_request_id = event["id"]
                        report["pending_request_resolved"] = False
                        report["interrupt_trigger"] = "pending_approval"
                        client.request("turn/interrupt", {
                            "threadId": thread_id, "turnId": turn_id})
                        report["interrupt_rpc"] = "responded"
                    continue
                if (scenario == "interrupt_on_approval"
                        and report["interrupt_rpc"] != "not_requested"):
                    raise TransportError("unexpected_approval_after_interrupt")
                if scenario in ("file_change_accept", "file_change_cut_after_accept"):
                    report["file_change_item_allowlisted"] = file_change_candidate_valid
                    actual, reason = (safe_file_change_decision(
                        event, thread_id=thread_id, turn_id=turn_id,
                        candidate_id=file_change_candidate_id,
                        candidate_valid=file_change_candidate_valid)
                        if accepted_file_change_id is None else
                        ("decline", "second_file_change_approval"))
                else:
                    actual, reason = (safe_accept_decision(
                        event, target=target, thread_id=thread_id, turn_id=turn_id,
                        expected=expected_command)
                        if decision == "accept" and not (
                            scenario in ("interrupt_after_turn_completed",
                                         "interrupt_after_command_completed",
                                         "interrupt_after_command_completed_delayed")
                            and accepted_items)
                        else ("decline", "second_command_approval" if decision == "accept"
                              else "decline_probe"))
                report["approval_checks"].append({
                    "decision": actual, "reason": reason,
                    "review": (file_change_review_shape(event, thread_id, turn_id)
                               if method == "item/fileChange/requestApproval"
                               else approval_review_shape(event, target, expected_command)),
                })
                client.respond_approval(event["id"], actual)
                if actual == "accept":
                    if scenario in ("file_change_accept", "file_change_cut_after_accept"):
                        accepted_file_change_id = event["params"]["itemId"]
                        if scenario == "file_change_cut_after_accept":
                            report["cutoff"] = "after_approval_write"
                            client.close()
                            report["managed_process_close_returned"] = True
                            cutoff_after_accept = True
                            break
                    else:
                        accepted_items.add(event["params"]["itemId"])
                    accepted_at = time.monotonic()
                    if scenario == "interrupt_on_approval":
                        report["interrupt_trigger"] = "approval_answered"
                        client.request("turn/interrupt", {
                            "threadId": thread_id, "turnId": turn_id})
                        report["interrupt_rpc"] = "responded"
            if method == "serverRequest/resolved" and pending_request_id is not None:
                params = event.get("params", {})
                if isinstance(params, dict) and params.get("requestId") == pending_request_id:
                    report["pending_request_resolved"] = True
            if method == "item/commandExecution/outputDelta":
                params = event.get("params", {})
                if (isinstance(params, dict) and params.get("itemId") in accepted_items
                        and isinstance(params.get("delta"), str)
                        and INTERRUPT_MARKER in params["delta"]):
                    report["interrupt_marker_seen"] = True
            if method == "item/completed":
                item = event.get("params", {}).get("item", {})
                if (scenario in ("file_change_accept", "file_change_cut_after_accept") and isinstance(item, dict)
                        and item.get("id") == accepted_file_change_id):
                    status = item.get("status")
                    report["accepted_file_change_status"] = (
                        status if status in ("completed", "failed", "declined")
                        else "unknown")
                if isinstance(item, dict) and item.get("id") in accepted_items:
                    exit_code = item.get("exitCode")
                    report["accepted_command_exit_zero"] = exit_code == 0
                    report["accepted_command_exit_kind"] = (
                        "zero" if exit_code == 0 else
                        "nonzero" if type(exit_code) is int else "missing")
                    if (scenario in ("interrupt_after_command_completed",
                                     "interrupt_after_command_completed_delayed")
                            and exit_code == 0
                            and report["interrupt_rpc"] == "not_requested"):
                        report["interrupt_trigger"] = "command_exit_zero"
                        if scenario == "interrupt_after_command_completed_delayed":
                            report["interrupt_delay_ms"] = int(
                                COMMAND_COMPLETION_RACE_DELAY_SECONDS * 1000)
                            if time.monotonic() + COMMAND_COMPLETION_RACE_DELAY_SECONDS >= deadline:
                                raise TransportError("probe_turn_timeout")
                            time.sleep(COMMAND_COMPLETION_RACE_DELAY_SECONDS)
                        try:
                            client.request("turn/interrupt", {
                                "threadId": thread_id, "turnId": turn_id})
                            report["interrupt_rpc"] = "responded"
                        except RpcError:
                            report["interrupt_rpc"] = "rejected"
            if method == "turn/completed":
                params = event.get("params", {})
                if params.get("threadId") != thread_id or params.get("turn", {}).get("id") != turn_id:
                    raise TransportError("turn_completion_mismatch")
                status = params["turn"].get("status")
                if status not in ("completed", "failed", "interrupted"):
                    raise TransportError("turn_completion_status_mismatch")
                report["turn_status"] = status
                if (scenario == "interrupt_after_turn_completed" and status == "completed"
                        and report["accepted_command_exit_kind"] == "zero"):
                    report["interrupt_trigger"] = "completed_turn"
                    try:
                        client.request("turn/interrupt", {
                            "threadId": thread_id, "turnId": turn_id})
                        report["interrupt_rpc"] = "responded"
                    except RpcError:
                        # A valid RPC error still makes this probe transport unusable.
                        # The completed turn was observed before this request.
                        report["interrupt_rpc"] = "rejected"
                break
        else:
            # A timed-out turn may still be active. The managed process is closed;
            # never claim the upstream interruption was confirmed.
            raise TransportError("probe_turn_timeout")
        if not cutoff_after_accept:
            resumed = client.request("thread/resume", {"threadId": thread_id})
            resumed_thread = resumed.get("thread")
            report["resume"] = ("recognized" if isinstance(resumed_thread, dict)
                                and resumed_thread.get("id") == thread_id else "shape_unknown")
        if scenario == "interrupt_pending" and report["pending_request_resolved"] is False:
            grace_deadline = time.monotonic() + PENDING_RESOLUTION_GRACE_SECONDS
            while time.monotonic() < grace_deadline:
                event = client.next_event(max(0.001, min(0.2,
                    grace_deadline - time.monotonic())))
                if event is None:
                    continue
                method = record_event_shape(report, event)
                params = event.get("params", {})
                if (method == "serverRequest/resolved" and isinstance(params, dict)
                        and params.get("requestId") == pending_request_id):
                    report["pending_request_resolved"] = True
                    break
    result = target / "probe-result.txt"
    expected_bytes = (b"RUNVORI_PROBE_OK" if scenario == "write_file"
                      else FILE_CHANGE_BYTES)
    report["file_match"] = (result.is_file() and not result.is_symlink()
                            and result.resolve().is_relative_to(target)
                            and result.read_bytes() == expected_bytes)
    if scenario in ("file_change_accept", "file_change_cut_after_accept"):
        fixture_entries = {path.name for path in target.iterdir()}
        report["fixture_only_expected_file"] = (
            fixture_entries == {MARKER, ".git", "probe-result.txt"})
        report["fixture_entries_within_allowlist"] = (
            fixture_entries.issubset({MARKER, ".git", "probe-result.txt"}))
    report["status"] = "observed"
    report["thread_id_sha256"] = hashlib.sha256(thread_id.encode()).hexdigest()
    return report


def write_report(root: Path, report: dict) -> Path:
    target = root.resolve() / ".dev" / "execution-probe-report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
