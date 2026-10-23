"""Opt-in, local M0 execution probe. Never logs upstream message values."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from runvori.codex.stdio import StdioClient, TransportError

MARKER = ".runvori-disposable-fixture"
SAFE_KEYS = frozenset({
    "threadId", "turnId", "turn", "id", "status", "item", "itemId", "type",
    "approvalId", "requestId", "reason", "command", "cwd", "startedAtMs",
    "method", "params", "result", "error", "items", "itemType", "delta",
    "message", "content", "output", "exitCode", "durationMs", "completedAt",
    "startedAt", "fileChanges", "changes", "path", "kind", "decision",
    "approvalId", "availableDecisions", "commandActions", "environmentId",
    "networkApprovalContext", "proposedExecpolicyAmendment",
    "proposedNetworkPolicyAmendments",
})
PROMPT = (
    "This is a disposable Runvori protocol probe. Read the fixture marker, "
    "create probe-result.txt containing exactly RUNVORI_PROBE_OK followed by a newline, "
    "then run a command that verifies its exact contents. Do not access paths "
    "outside this workspace or use the network."
)
SAFE_COMMAND = "python --version"
ACCEPT_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact command {SAFE_COMMAND} and report its result. Do not use "
    "other commands, access paths outside this workspace, or use the network."
)
COMMAND_APPROVAL_KEYS = frozenset({
    "approvalId", "availableDecisions", "command", "commandActions", "cwd", "environmentId",
    "itemId", "kind", "networkApprovalContext", "proposedExecpolicyAmendment",
    "proposedNetworkPolicyAmendments", "reason", "startedAtMs", "threadId", "turnId",
})


def is_safe_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    if command.strip() == SAFE_COMMAND:
        return True
    # The pinned Windows CLI wraps tool commands with its bundled pwsh.
    executable = (Path.home() / ".cache/codex-runtimes/codex-primary-runtime"
                  / "dependencies/native/powershell/pwsh.exe")
    if not executable.is_file():
        return False
    wrapped = f'"{str(executable).replace(chr(92), chr(92) * 2)}" -Command \'{SAFE_COMMAND}\''
    return command == wrapped


def safe_accept_decision(event: dict, *, target: Path, thread_id: str,
                         turn_id: str) -> tuple[str, str]:
    """Only the exact fixture write command may receive a one-shot accept."""
    if event.get("method") != "item/commandExecution/requestApproval":
        return "decline", "unsupported_approval_type"
    params = event.get("params")
    if not isinstance(params, dict) or set(params) - COMMAND_APPROVAL_KEYS:
        return "decline", "unexpected_approval_fields"
    if (params.get("threadId") != thread_id or params.get("turnId") != turn_id
            or not isinstance(params.get("itemId"), str)
            or params.get("kind", "command") != "command"
            or not is_safe_command(params.get("command"))):
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
    return "accept", "allowlisted_read_only_command"


def approval_review_shape(event: dict, target: Path) -> dict:
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
        "command_exact": command == SAFE_COMMAND,
        "command_allowlisted": is_safe_command(command),
        "command_trimmed_exact": isinstance(command, str) and command.strip() == SAFE_COMMAND,
        "command_contains_exact": isinstance(command, str) and SAFE_COMMAND in command,
        "command_length_delta": (len(command) - len(SAFE_COMMAND)
                                 if isinstance(command, str) else None),
        "cwd_exact": cwd_exact,
        "scope_fields_present": sorted(
            key for key in ("environmentId", "networkApprovalContext",
                            "proposedExecpolicyAmendment", "proposedNetworkPolicyAmendments")
            if params.get(key) is not None),
    }


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


def run_probe(*, root: Path, fixture: Path, account_home: Path,
              executable: str, decision: str, timeout: float = 120) -> dict:
    if decision not in ("accept", "decline"):
        raise ValueError("invalid_approval_decision")
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
    report: dict = {"schema": 1, "status": "started", "approval_decision": decision,
                    "events": [], "approval_count": 0, "turn_status": "unknown",
                    "resume": "not_attempted", "approval_checks": [],
                    "accepted_command_exit_zero": None}
    accepted_items: set[str] = set()
    deadline = time.monotonic() + timeout
    with StdioClient([executable, "app-server", "--listen", "stdio://"],
                     cwd=target, env=env, timeout=15) as client:
        client.initialize()
        started = client.request("thread/start", {
            "cwd": str(target), "approvalPolicy": "on-request",
            "approvalsReviewer": "user", "sandbox": "workspace-write",
            "ephemeral": False,
        })
        thread_id = started.get("thread", {}).get("id")
        if not isinstance(thread_id, str):
            raise TransportError("thread_start_shape_mismatch")
        begun = client.request("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": (
                ACCEPT_PROMPT if decision == "accept" else PROMPT)}],
        })
        turn_id = begun.get("turn", {}).get("id")
        if not isinstance(turn_id, str):
            raise TransportError("turn_start_shape_mismatch")
        while time.monotonic() < deadline:
            event = client.next_event(max(0.001, min(1.0, deadline - time.monotonic())))
            if event is None:
                continue
            method = event["method"]
            if not isinstance(method, str):
                raise TransportError("invalid_notification_method")
            safe_method = (method if re.fullmatch(r"[A-Za-z][A-Za-z0-9/_-]{0,79}", method)
                           and method.split("/")[0] in {"thread", "turn", "item", "serverRequest"}
                           else "other:" + hashlib.sha256(method.encode()).hexdigest())
            report["events"].append({"method": safe_method, "shape": shape(event.get("params"))})
            if len(report["events"]) > 200:
                raise TransportError("probe_event_limit")
            if "id" in event:
                report["approval_count"] += 1
                actual, reason = (safe_accept_decision(
                    event, target=target, thread_id=thread_id, turn_id=turn_id)
                    if decision == "accept" else ("decline", "decline_probe"))
                report["approval_checks"].append({
                    "decision": actual, "reason": reason,
                    "review": approval_review_shape(event, target),
                })
                client.respond_approval(event["id"], actual)
                if actual == "accept":
                    accepted_items.add(event["params"]["itemId"])
            if method == "item/completed":
                item = event.get("params", {}).get("item", {})
                if isinstance(item, dict) and item.get("id") in accepted_items:
                    report["accepted_command_exit_zero"] = item.get("exitCode") == 0
            if method == "turn/completed":
                params = event.get("params", {})
                if params.get("threadId") != thread_id or params.get("turn", {}).get("id") != turn_id:
                    raise TransportError("turn_completion_mismatch")
                status = params["turn"].get("status")
                if status not in ("completed", "failed", "interrupted"):
                    raise TransportError("turn_completion_status_mismatch")
                report["turn_status"] = status
                break
        else:
            # A timed-out turn may still be active. The managed process is closed;
            # never claim the upstream interruption was confirmed.
            raise TransportError("probe_turn_timeout")
        resumed = client.request("thread/resume", {"threadId": thread_id})
        resumed_thread = resumed.get("thread")
        report["resume"] = ("recognized" if isinstance(resumed_thread, dict)
                            and resumed_thread.get("id") == thread_id else "shape_unknown")
    result = target / "probe-result.txt"
    report["file_match"] = (result.is_file() and not result.is_symlink()
                            and result.resolve().is_relative_to(target)
                            and result.read_bytes() == b"RUNVORI_PROBE_OK\n")
    report["status"] = "observed"
    report["thread_id_sha256"] = hashlib.sha256(thread_id.encode()).hexdigest()
    return report


def write_report(root: Path, report: dict) -> Path:
    target = root.resolve() / ".dev" / "execution-probe-report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
