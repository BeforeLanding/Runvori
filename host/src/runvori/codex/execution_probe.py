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
from runvori.codex.verification import RequiredCommandEvidence, RequiredVerificationSet
from runvori.infrastructure.artifact_probe import capture_worktree_file

MARKER = ".runvori-disposable-fixture"
SAFE_KEYS = frozenset({
    "threadId", "turnId", "turn", "id", "status", "item", "itemId", "type",
    "approvalId", "requestId", "reason", "command", "cwd", "startedAtMs",
    "method", "params", "result", "error", "items", "itemType", "delta",
    "message", "content", "output", "exitCode", "durationMs", "completedAt",
    "startedAt", "fileChanges", "changes", "path", "kind", "decision",
    "move_path",
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
SECOND_VERIFY_COMMAND = "git --version"
NONZERO_VERIFY_COMMAND = "exit 7"
WRITE_COMMAND = (
    "Set-Content -LiteralPath probe-result.txt -Value RUNVORI_PROBE_OK -NoNewline"
)
BINARY_COMMAND = (
    'python -c "from pathlib import Path; '
    "Path('binary.bin').write_bytes(bytes.fromhex('00ff') * 1024)\""
)
BINARY_BYTES = bytes.fromhex("00ff") * 1024
BINARY_BASELINE = b"baseline\0"
BINARY_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact command {BINARY_COMMAND}. Do not use other commands, other "
    "tools, other paths, or the network."
)
INTERRUPT_MARKER = "RUNVORI_SLEEP_STARTED"
INTERRUPT_COMMAND = f"Write-Output {INTERRUPT_MARKER}; Start-Sleep -Seconds 60"
ACCEPT_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact command {SAFE_COMMAND} and report its result. Do not use "
    "other commands, access paths outside this workspace, or use the network."
)
TWO_VERIFY_PROMPT = (
    "This is a disposable Runvori protocol probe. Run these two commands "
    f"separately in this workspace: {SAFE_COMMAND} and {SECOND_VERIFY_COMMAND}. "
    "Run each once and report the results. Do not combine commands, use other "
    "tools, access paths outside this workspace, or use the network."
)
NONZERO_VERIFY_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run "
    f"only the exact PowerShell command {NONZERO_VERIFY_COMMAND}. Its nonzero "
    "exit is intentional. Report the result without running any other command, "
    "using other tools, accessing paths outside this workspace, or using the network."
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
FILE_APPROVAL_NAME = "approval-target.txt"
FILE_APPROVAL_BEFORE = b"RUNVORI_APPROVAL_BEFORE\n"
FILE_APPROVAL_ALLOWED = b"RUNVORI_APPROVAL_AFTER\n"
FILE_APPROVAL_DENIED = b"RUNVORI_APPROVAL_DENIED\n"
REAL_FILE_APPROVAL_ALLOW_PROMPT = (
    "This is a disposable Runvori protocol probe. Use only the apply_patch "
    "tool to change approval-target.txt so its single line is exactly "
    "RUNVORI_APPROVAL_AFTER followed by a newline, replacing "
    "RUNVORI_APPROVAL_BEFORE. Make exactly one patch attempt; if approval is "
    "required, request it. Do not use shell commands, other tools, other "
    "paths, or the network."
)
REAL_FILE_APPROVAL_DENY_PROMPT = (
    "This is a disposable Runvori protocol probe. Use only the apply_patch "
    "tool to change approval-target.txt so its single line is exactly "
    "RUNVORI_APPROVAL_DENIED followed by a newline, replacing "
    "RUNVORI_APPROVAL_BEFORE. Make exactly one patch attempt; if approval is "
    "required, request it. Do not retry, do not use shell commands, other "
    "tools, other paths, or the network."
)
REAL_VERIFY_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    "the exact command python --version once and report its result. Wait for "
    "approval if it is requested. Do not use other commands, other tools, "
    "access paths outside this workspace, or use the network."
)
# The phone-driven interrupt scenarios use their own prompts. The execution
# prompt emits a real output marker and caps the run at 60 seconds, so the test
# can trigger only after the command actually started. The approval prompt
# makes exactly one approval request and never decides it; the turn is ended
# through the product interrupt entry instead.
REAL_EXECUTION_INTERRUPT_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact PowerShell command {INTERRUPT_COMMAND} and wait for it to "
    "finish. Wait for approval if it is requested. Do not use other commands, "
    "other tools, access paths outside this workspace, or use the network."
)
REAL_PENDING_INTERRUPT_PROMPT = (
    "This is a disposable Runvori protocol probe. In this workspace, run only "
    f"the exact command {SAFE_COMMAND} once. If approval is requested, wait "
    "for the decision without retrying. Do not use other commands, other "
    "tools, access paths outside this workspace, or use the network."
)
MOVE_SOURCE_BYTES = b"RUNVORI_MOVE_OLD\n"
MOVE_DESTINATION_BYTES = b"RUNVORI_MOVE_NEW\n"
MOVE_PROMPT = (
    "This is a disposable Runvori protocol probe. Use only apply_patch to move "
    "move-source.txt to move-destination.txt, changing its single line from "
    "RUNVORI_MOVE_OLD to RUNVORI_MOVE_NEW. Try the patch once even if approval "
    "is required. Do not use shell commands, other tools, other paths, or the network."
)
DIRTY_TRACKED_BASE = b"tracked baseline\n"
DIRTY_TRACKED_BEFORE = b"tracked preexisting edit\n"
DIRTY_UNTRACKED_BEFORE = b"preexisting untracked\n"
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
    double_quoted_command = expected.replace('"', '\\"')
    return command in (
        f"{quoted_executable} -Command '{expected}'",
        f"{quoted_executable} -NoProfile -Command '{expected}'",
        f'{quoted_executable} -Command "{double_quoted_command}"',
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


def _git_status_entries(target: Path) -> set[bytes]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=target, capture_output=True, timeout=15, check=False,
    )
    if result.returncode or (result.stdout and not result.stdout.endswith(b"\0")):
        raise TransportError("fixture_git_status_failed")
    if not result.stdout:
        return set()
    entries = result.stdout.split(b"\0")
    if entries[-1] != b"" or len(entries[:-1]) != len(set(entries[:-1])):
        raise TransportError("fixture_git_status_invalid")
    return set(entries[:-1])


def prepare_dirty_fixture(root: Path, fixture: Path) -> Path:
    target = prepare_fixture(root, fixture)
    (target / "tracked.txt").write_bytes(DIRTY_TRACKED_BASE)
    commands = (
        ["git", "add", "--", MARKER, "tracked.txt"],
        ["git", "-c", "user.name=RunvoriProbe",
         "-c", "user.email=probe@invalid.local", "-c", "commit.gpgsign=false",
         "commit", "--quiet", "-m", "probe baseline"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=target, capture_output=True,
                                timeout=15, check=False)
        if result.returncode:
            raise TransportError("fixture_git_baseline_failed")
    (target / "tracked.txt").write_bytes(DIRTY_TRACKED_BEFORE)
    (target / "existing-untracked.txt").write_bytes(DIRTY_UNTRACKED_BEFORE)
    validate_dirty_fixture(root, target)
    return target


def prepare_move_fixture(root: Path, fixture: Path) -> Path:
    target = prepare_fixture(root, fixture)
    (target / "move-source.txt").write_bytes(MOVE_SOURCE_BYTES)
    for command in (
        ["git", "add", "--", MARKER, "move-source.txt"],
        ["git", "-c", "user.name=RunvoriProbe",
         "-c", "user.email=probe@invalid.local", "-c", "commit.gpgsign=false",
         "commit", "--quiet", "-m", "move baseline"],
    ):
        result = subprocess.run(command, cwd=target, capture_output=True,
                                timeout=15, check=False)
        if result.returncode:
            raise TransportError("fixture_git_baseline_failed")
    return validate_move_fixture(root, target)


def prepare_binary_fixture(root: Path, fixture: Path) -> Path:
    target = prepare_fixture(root, fixture)
    (target / "binary.bin").write_bytes(BINARY_BASELINE)
    for command in (
        ["git", "add", "--", MARKER, "binary.bin"],
        ["git", "-c", "user.name=RunvoriProbe",
         "-c", "user.email=probe@invalid.local", "-c", "commit.gpgsign=false",
         "commit", "--quiet", "-m", "binary baseline"],
    ):
        result = subprocess.run(command, cwd=target, capture_output=True,
                                timeout=15, check=False)
        if result.returncode:
            raise TransportError("fixture_git_baseline_failed")
    return validate_binary_fixture(root, target)


def validate_binary_fixture(root: Path, fixture: Path) -> Path:
    target = fixture_path(root, fixture)
    expected = {MARKER, ".git", "binary.bin"}
    if (not target.is_dir() or {path.name for path in target.iterdir()} != expected
            or any((target / name).is_symlink() or
                   (hasattr(target / name, "is_junction") and
                    (target / name).is_junction()) for name in expected)
            or not (target / ".git").is_dir()
            or (target / MARKER).read_text(encoding="utf-8") !=
            "Runvori disposable execution probe\n"
            or (target / "binary.bin").read_bytes() != BINARY_BASELINE
            or _git_status_entries(target)):
        raise TransportError("invalid_binary_fixture")
    return target


def validate_move_fixture(root: Path, fixture: Path) -> Path:
    target = fixture_path(root, fixture)
    expected = {MARKER, ".git", "move-source.txt"}
    if (not target.is_dir() or {path.name for path in target.iterdir()} != expected
            or any((target / name).is_symlink() or
                   (hasattr(target / name, "is_junction") and
                    (target / name).is_junction()) for name in expected)
            or not (target / ".git").is_dir()
            or (target / MARKER).read_text(encoding="utf-8") !=
            "Runvori disposable execution probe\n"
            or (target / "move-source.txt").read_bytes() != MOVE_SOURCE_BYTES
            or _git_status_entries(target)):
        raise TransportError("invalid_move_fixture")
    return target


def prepare_file_approval_fixture(root: Path, fixture: Path) -> Path:
    """A committed one-line file whose replacement needs review."""
    target = prepare_fixture(root, fixture)
    (target / FILE_APPROVAL_NAME).write_bytes(FILE_APPROVAL_BEFORE)
    for command in (
        ["git", "add", "--", MARKER, FILE_APPROVAL_NAME],
        ["git", "-c", "user.name=RunvoriProbe",
         "-c", "user.email=probe@invalid.local", "-c", "commit.gpgsign=false",
         "commit", "--quiet", "-m", "approval baseline"],
    ):
        result = subprocess.run(command, cwd=target, capture_output=True,
                                timeout=15, check=False)
        if result.returncode:
            raise TransportError("fixture_git_baseline_failed")
    return validate_file_approval_fixture(root, target)


def validate_file_approval_fixture(root: Path, fixture: Path) -> Path:
    target = fixture_path(root, fixture)
    expected = {MARKER, ".git", FILE_APPROVAL_NAME}
    if (not target.is_dir() or {path.name for path in target.iterdir()} != expected
            or any((target / name).is_symlink() or
                   (hasattr(target / name, "is_junction") and
                    (target / name).is_junction()) for name in expected)
            or not (target / ".git").is_dir()
            or (target / MARKER).read_text(encoding="utf-8") !=
            "Runvori disposable execution probe\n"
            or (target / FILE_APPROVAL_NAME).read_bytes() != FILE_APPROVAL_BEFORE
            or _git_status_entries(target)):
        raise TransportError("invalid_file_approval_fixture")
    return target


def move_item_matches(item: object, target: Path) -> bool:
    if not isinstance(item, dict) or item.get("type") != "fileChange":
        return False
    changes = item.get("changes")
    if not isinstance(changes, list) or len(changes) != 1:
        return False
    change = changes[0]
    if not isinstance(change, dict) or not isinstance(change.get("kind"), dict):
        return False
    kind = change["kind"]
    if kind.get("type") != "update" or not isinstance(kind.get("move_path"), str):
        return False
    try:
        return (Path(change.get("path")).resolve() == target / "move-source.txt"
                and Path(kind["move_path"]).resolve() == target / "move-destination.txt")
    except (TypeError, OSError, ValueError):
        return False


def exact_move_item(item: object, target: Path) -> bool:
    if (not move_item_matches(item, target) or not isinstance(item, dict)
            or set(item) != {"type", "id", "status", "changes"}
            or not isinstance(item.get("id"), str) or not item["id"]):
        return False
    change = item["changes"][0]
    destination = target / "move-destination.txt"
    expected_diff = ("@@ -1 +1 @@\n-RUNVORI_MOVE_OLD\n+RUNVORI_MOVE_NEW\n\n\n"
                     f"Moved to: {destination}")
    return (set(change) == {"path", "kind", "diff"}
            and change["path"] == str(target / "move-source.txt")
            and change["kind"] == {"type": "update", "move_path": str(destination)}
            and change["diff"] == expected_diff
            and (target / "move-source.txt").is_file()
            and not (target / "move-source.txt").is_symlink()
            and (target / "move-source.txt").read_bytes() == MOVE_SOURCE_BYTES
            and not destination.exists())


def validate_dirty_fixture(root: Path, fixture: Path) -> Path:
    target = fixture_path(root, fixture)
    expected_names = {MARKER, ".git", "tracked.txt", "existing-untracked.txt"}
    if (not target.is_dir() or {path.name for path in target.iterdir()} != expected_names
            or any((target / name).is_symlink() for name in expected_names)
            or not (target / ".git").is_dir()
            or not all((target / name).is_file() for name in (
                MARKER, "tracked.txt", "existing-untracked.txt"))
            or (target / MARKER).read_text(encoding="utf-8") !=
            "Runvori disposable execution probe\n"
            or (target / "tracked.txt").read_bytes() != DIRTY_TRACKED_BEFORE
            or (target / "existing-untracked.txt").read_bytes() !=
            DIRTY_UNTRACKED_BEFORE
            or _git_status_entries(target) != {
                b" M tracked.txt", b"?? existing-untracked.txt"}):
        raise TransportError("invalid_dirty_fixture")
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
    if scenario not in ("read_only", "verify_command", "verify_two_commands",
                        "verify_nonzero", "binary_command",
                        "write_file", "interrupt", "interrupt_pending",
                        "interrupt_on_approval", "file_change_decline",
                        "file_change_accept", "file_change_accept_dirty",
                        "file_change_cut_after_accept", "file_change_move_decline",
                        "file_change_move_accept",
                        "interrupt_after_turn_completed",
                        "interrupt_after_command_completed",
                        "interrupt_after_command_completed_delayed"):
        raise ValueError("invalid_probe_scenario")
    if scenario in ("verify_command", "verify_two_commands", "verify_nonzero",
                    "binary_command", "write_file",
                    "interrupt", "interrupt_on_approval",
                    "interrupt_after_turn_completed",
                    "interrupt_after_command_completed",
                    "interrupt_after_command_completed_delayed") and decision != "accept":
        raise ValueError("scenario_requires_accept_mode")
    if (scenario == "interrupt_pending") != (decision == "defer"):
        raise ValueError("pending_scenario_requires_defer_mode")
    if scenario in ("file_change_decline", "file_change_move_decline") and decision != "decline":
        raise ValueError("file_change_scenario_requires_decline_mode")
    if scenario in ("file_change_accept", "file_change_accept_dirty",
                    "file_change_cut_after_accept", "file_change_move_accept") and decision != "accept":
        raise ValueError("file_change_scenario_requires_accept_mode")
    if timeout <= 0 or timeout > 600:
        raise ValueError("invalid_probe_timeout")
    target = (validate_dirty_fixture(root, fixture)
              if scenario == "file_change_accept_dirty" else
              validate_binary_fixture(root, fixture)
              if scenario == "binary_command" else
              validate_move_fixture(root, fixture)
              if scenario in ("file_change_move_decline", "file_change_move_accept") else
              validate_fixture(root, fixture))
    home = account_home.resolve(strict=True)
    if not home.is_dir() or home == target or home.is_relative_to(target):
        raise TransportError("invalid_account_home")
    env = dict(os.environ, CODEX_HOME=str(home))
    for key in list(env):
        if any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
            del env[key]
    expected_command = (NONZERO_VERIFY_COMMAND if scenario == "verify_nonzero" else
                        BINARY_COMMAND if scenario == "binary_command" else
                        WRITE_COMMAND if scenario == "write_file" else
                        INTERRUPT_COMMAND if scenario in ("interrupt", "interrupt_on_approval")
                        else SAFE_COMMAND)
    prompt = (FILE_CHANGE_PROMPT if scenario in (
        "file_change_decline", "file_change_accept", "file_change_accept_dirty",
        "file_change_cut_after_accept")
              else
              MOVE_PROMPT if scenario in ("file_change_move_decline", "file_change_move_accept") else
              PROMPT if decision == "decline" else
              BINARY_PROMPT if scenario == "binary_command" else
              WRITE_PROMPT if scenario == "write_file" else
              INTERRUPT_PROMPT if scenario in ("interrupt", "interrupt_on_approval")
              else TWO_VERIFY_PROMPT if scenario == "verify_two_commands"
              else NONZERO_VERIFY_PROMPT if scenario == "verify_nonzero"
              else ACCEPT_PROMPT)
    report: dict = {"schema": 1, "status": "started", "approval_decision": decision,
                    "scenario": scenario,
                    "events": [], "approval_count": 0,
                    "file_change_approval_count": 0, "command_approval_count": 0,
                    "turn_status": "unknown",
                    "resume": "not_attempted", "approval_checks": [],
                    "accepted_command_exit_zero": None,
                    "accepted_command_exit_kind": "not_observed",
                    "fixed_nonzero_exit_seven": None,
                    "required_verification_status": None,
                    "required_verification_items": None,
                    "required_verification_summary": None,
                    "interrupt_rpc": "not_requested", "interrupt_trigger": None,
                    "interrupt_marker_seen": False,
                    "pending_request_resolved": None,
                    "pending_request_allowlisted": None,
                    "accepted_file_change_status": "not_observed",
                    "file_change_item_allowlisted": None,
                    "move_item_observed": False,
                    "move_kind_paths_matched": False,
                    "move_source_unchanged": None,
                    "move_destination_absent": None,
                    "move_source_absent": None,
                    "move_destination_match": None,
                    "binary_match": None,
                    "binary_git_marker": None,
                    "binary_representation": None,
                    "binary_content_available": None,
                    "binary_size_bytes": None,
                    "fixture_only_expected_file": None,
                    "fixture_entries_within_allowlist": None,
                    "preexisting_files_unchanged": None,
                    "git_status_expected_transition": None,
                    "git_diff_excludes_untracked": None,
                    "cutoff": None, "managed_process_close_returned": None,
                    "interrupt_delay_ms": None}
    accepted_items: set[str] = set()
    file_change_candidate_id: str | None = None
    file_change_candidate_valid = False
    accepted_file_change_id: str | None = None
    accepted_at: float | None = None
    pending_request_id: str | int | None = None
    cutoff_after_accept = False
    verification_group: RequiredVerificationSet | None = None
    deadline = time.monotonic() + timeout
    with StdioClient([executable, "app-server", "--listen", "stdio://"],
                     cwd=target, env=env, timeout=15) as client:
        client.initialize()
        started = client.request("thread/start", {
            "cwd": str(target), "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandbox": ("read-only" if scenario in (
                "file_change_decline", "file_change_accept", "file_change_accept_dirty",
                "file_change_move_decline", "file_change_move_accept",
                "file_change_cut_after_accept")
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
        if scenario in ("verify_command", "verify_two_commands", "verify_nonzero"):
            commands = ({"failure": NONZERO_VERIFY_COMMAND}
                        if scenario == "verify_nonzero" else {"python": SAFE_COMMAND})
            if scenario == "verify_two_commands":
                commands["git"] = SECOND_VERIFY_COMMAND
            verification_group = RequiredVerificationSet({
                key: RequiredCommandEvidence(
                    thread_id=thread_id, turn_id=turn_id, cwd=target,
                    matches_command=lambda value, expected=command:
                    is_safe_command(value, expected))
                for key, command in commands.items()})
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
            if verification_group is not None:
                verification_group.observe(method, event.get("params"))
            if scenario in ("file_change_move_decline", "file_change_move_accept") and method in ("item/started", "item/updated"):
                params = event.get("params")
                item = params.get("item") if isinstance(params, dict) else None
                if isinstance(item, dict) and item.get("type") == "fileChange":
                    report["move_item_observed"] = True
                    if (params.get("threadId") == thread_id
                            and params.get("turnId") == turn_id
                            and move_item_matches(item, target)):
                        report["move_kind_paths_matched"] = True
            if scenario in ("file_change_accept", "file_change_accept_dirty",
                            "file_change_cut_after_accept", "file_change_move_accept") and method in ("item/started", "item/updated"):
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
                        file_change_candidate_valid = (
                            exact_move_item(item, target) if scenario == "file_change_move_accept"
                            else exact_file_change_item(item, target))
                    else:
                        file_change_candidate_valid = (
                            file_change_candidate_valid and
                            (exact_move_item(item, target) if scenario == "file_change_move_accept"
                             else exact_file_change_item(item, target)))
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
                if scenario in ("file_change_accept", "file_change_accept_dirty",
                                "file_change_cut_after_accept", "file_change_move_accept"):
                    report["file_change_item_allowlisted"] = file_change_candidate_valid
                    actual, reason = (safe_file_change_decision(
                        event, thread_id=thread_id, turn_id=turn_id,
                        candidate_id=file_change_candidate_id,
                        candidate_valid=file_change_candidate_valid)
                        if accepted_file_change_id is None else
                        ("decline", "second_file_change_approval"))
                elif scenario == "verify_two_commands":
                    params = event.get("params", {})
                    command = params.get("command") if isinstance(params, dict) else None
                    key = next((key for key, expected in (
                        ("python", SAFE_COMMAND), ("git", SECOND_VERIFY_COMMAND))
                        if is_safe_command(command, expected)), None)
                    expected_for_review = (SAFE_COMMAND if key is None or key == "python"
                                           else SECOND_VERIFY_COMMAND)
                    if key is None:
                        actual, reason = "decline", "operation_not_allowlisted"
                    elif verification_group is not None and verification_group.requirements[key].approved_ids:
                        actual, reason = "decline", "second_command_approval"
                    else:
                        actual, reason = safe_accept_decision(
                            event, target=target, thread_id=thread_id,
                            turn_id=turn_id, expected=expected_for_review)
                else:
                    actual, reason = (safe_accept_decision(
                        event, target=target, thread_id=thread_id, turn_id=turn_id,
                        expected=expected_command)
                        if decision == "accept" and not (
                            scenario in ("verify_command", "verify_nonzero",
                                         "binary_command",
                                         "interrupt_after_turn_completed",
                                         "interrupt_after_command_completed",
                                         "interrupt_after_command_completed_delayed")
                            and accepted_items)
                        else ("decline", "second_command_approval" if decision == "accept"
                              else "decline_probe"))
                report["approval_checks"].append({
                    "decision": actual, "reason": reason,
                    "review": (file_change_review_shape(event, thread_id, turn_id)
                               if method == "item/fileChange/requestApproval"
                               else approval_review_shape(event, target,
                                   expected_for_review if scenario == "verify_two_commands"
                                   else expected_command)),
                })
                client.respond_approval(event["id"], actual)
                if actual == "accept":
                    if scenario in ("file_change_accept", "file_change_accept_dirty",
                                    "file_change_cut_after_accept", "file_change_move_accept"):
                        accepted_file_change_id = event["params"]["itemId"]
                        if scenario == "file_change_cut_after_accept":
                            report["cutoff"] = "after_approval_write"
                            client.close()
                            report["managed_process_close_returned"] = True
                            cutoff_after_accept = True
                            break
                    else:
                        accepted_items.add(event["params"]["itemId"])
                        if verification_group is not None:
                            verification_group.note_approved(
                                key if scenario == "verify_two_commands" else
                                "failure" if scenario == "verify_nonzero" else "python",
                                event["params"]["itemId"])
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
                if (scenario in ("file_change_accept", "file_change_accept_dirty",
                                 "file_change_cut_after_accept", "file_change_move_accept") and isinstance(item, dict)
                        and item.get("id") == accepted_file_change_id):
                    status = item.get("status")
                    report["accepted_file_change_status"] = (
                        status if status in ("completed", "failed", "declined")
                        else "unknown")
                if isinstance(item, dict) and item.get("id") in accepted_items:
                    exit_code = item.get("exitCode")
                    if scenario == "verify_nonzero":
                        report["fixed_nonzero_exit_seven"] = (
                            type(exit_code) is int and exit_code == 7)
                    if scenario != "verify_two_commands":
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
    report["file_match"] = (
        None if scenario in ("file_change_move_decline", "file_change_move_accept",
                             "binary_command")
        else result.is_file() and not result.is_symlink()
        and result.resolve().is_relative_to(target)
        and result.read_bytes() == expected_bytes)
    if scenario in ("file_change_move_decline", "file_change_move_accept"):
        source = target / "move-source.txt"
        destination = target / "move-destination.txt"
        report["move_source_unchanged"] = (
            source.is_file() and not source.is_symlink()
            and source.read_bytes() == MOVE_SOURCE_BYTES)
        report["move_destination_absent"] = not destination.exists()
        report["move_source_absent"] = not source.exists()
        report["move_destination_match"] = (
            destination.is_file() and not destination.is_symlink()
            and destination.read_bytes() == MOVE_DESTINATION_BYTES)
        if scenario == "file_change_move_decline":
            if (not report["move_source_unchanged"] or not report["move_destination_absent"]
                    or _git_status_entries(target)):
                raise TransportError("move_decline_fixture_changed")
        else:
            report["git_status_expected_transition"] = _git_status_entries(target) == {
                b" D move-source.txt", b"?? move-destination.txt"}
    if scenario == "binary_command":
        binary = target / "binary.bin"
        report["binary_match"] = (
            binary.is_file() and not binary.is_symlink()
            and binary.read_bytes() == BINARY_BYTES)
        report["git_status_expected_transition"] = _git_status_entries(target) == {
            b" M binary.bin"}
        if report["binary_match"]:
            captured = capture_worktree_file(target, "binary.bin")
            report["binary_representation"] = captured["representation"]
            report["binary_content_available"] = captured["content_available"]
            report["binary_size_bytes"] = captured["size_bytes"]
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "--numstat", "-z",
             "--", "binary.bin"], cwd=target, capture_output=True, timeout=15,
            check=False)
        report["binary_git_marker"] = (
            diff.returncode == 0 and diff.stdout == b"-\t-\tbinary.bin\0")
    if verification_group is not None:
        snapshot = verification_group.snapshot(report["turn_status"])
        report["required_verification_items"] = snapshot["items"]
        report["required_verification_summary"] = snapshot["summary"]
        if scenario in ("verify_command", "verify_nonzero"):
            report["required_verification_status"] = snapshot["items"][
                "failure" if scenario == "verify_nonzero" else "python"]
    if scenario in ("file_change_accept", "file_change_accept_dirty",
                    "file_change_cut_after_accept"):
        fixture_entries = {path.name for path in target.iterdir()}
        expected_entries = ({MARKER, ".git", "tracked.txt",
                             "existing-untracked.txt", "probe-result.txt"}
                            if scenario == "file_change_accept_dirty" else
                            {MARKER, ".git", "probe-result.txt"})
        report["fixture_only_expected_file"] = (
            fixture_entries == expected_entries)
        report["fixture_entries_within_allowlist"] = (
            fixture_entries.issubset(expected_entries))
    if scenario == "file_change_accept_dirty":
        report["preexisting_files_unchanged"] = (
            (target / "tracked.txt").is_file()
            and not (target / "tracked.txt").is_symlink()
            and (target / "tracked.txt").read_bytes() == DIRTY_TRACKED_BEFORE
            and (target / "existing-untracked.txt").is_file()
            and not (target / "existing-untracked.txt").is_symlink()
            and (target / "existing-untracked.txt").read_bytes() ==
            DIRTY_UNTRACKED_BEFORE)
        report["git_status_expected_transition"] = (
            _git_status_entries(target) == {
                b" M tracked.txt", b"?? existing-untracked.txt",
                b"?? probe-result.txt"})
        git_diff = subprocess.run(
            ["git", "diff", "--name-only", "--", "tracked.txt", "probe-result.txt"],
            cwd=target, capture_output=True, timeout=15, check=False)
        report["git_diff_excludes_untracked"] = (
            git_diff.returncode == 0 and git_diff.stdout == b"tracked.txt\n")
    report["status"] = "observed"
    report["thread_id_sha256"] = hashlib.sha256(thread_id.encode()).hexdigest()
    return report


def write_report(root: Path, report: dict) -> Path:
    target = root.resolve() / ".dev" / "execution-probe-report.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
