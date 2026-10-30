"""Bounded, reviewable approval details; insufficient detail must block allow.

The adapter normalizes one observed upstream request into the plain shapes
below. ``normalize`` then decides whether the result is safe to display and
whether a one-time allow may be offered at all. Nothing here guesses: a value
that cannot be verified is dropped and recorded as a missing reason.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import quote


MAX_DETAILS_BYTES = 32 * 1024
MAX_DIFF_BYTES = 16 * 1024
MAX_COMMAND_BYTES = 8 * 1024
MAX_REASON_BYTES = 2 * 1024
MAX_PATH_BYTES = 1024
MAX_CHANGES = 64
MAX_ID_BYTES = 256

COMPLETE = "complete"
INSUFFICIENT = "insufficient"
CHANGE_TYPES = frozenset({"add", "delete", "update"})


class ApprovalDetailError(ValueError):
    """A safe rejection code without command, path or diff content."""


def _text(value: Any, limit: int) -> tuple[str | None, bool]:
    """Return UTF-8 bounded text; the flag means content was dropped."""
    if not isinstance(value, str) or not value:
        return None, False
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return None, True
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", "ignore"), True


def _identifier(value: Any) -> str | None:
    text, truncated = _text(value, MAX_ID_BYTES)
    return None if truncated else text


def _epoch_ms_to_utc(value: Any) -> str | None:
    if type(value) is not int or value <= 0 or value >= 2**62:
        return None
    try:
        moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.isoformat().replace("+00:00", "Z")


def command_details(params: dict) -> dict:
    """Normalize one command-execution approval request."""
    if not isinstance(params, dict):
        raise ApprovalDetailError("invalid_approval_details")
    command, command_truncated = _text(params.get("command"), MAX_COMMAND_BYTES)
    reason, reason_truncated = _text(params.get("reason"), MAX_REASON_BYTES)
    cwd_value = params.get("cwd")
    return {
        "kind": "command",
        "command": command,
        "command_truncated": command_truncated,
        "cwd": cwd_value if isinstance(cwd_value, str) and cwd_value else None,
        "reason": reason,
        "requested_at": _epoch_ms_to_utc(params.get("startedAtMs")),
        "upstream_approval_id": _identifier(params.get("approvalId")),
        "item_id": _identifier(params.get("itemId")),
        "command_kind": (params.get("kind")
                         if isinstance(params.get("kind"), str) else None),
        "changes": [],
        "review_truncated": False,
    }


def file_change_details(params: dict) -> dict:
    """Normalize one file-change approval request (its params carry no paths)."""
    if not isinstance(params, dict):
        raise ApprovalDetailError("invalid_approval_details")
    reason, reason_truncated = _text(params.get("reason"), MAX_REASON_BYTES)
    return {
        "kind": "file_change",
        "command": None,
        "command_truncated": False,
        "cwd": None,
        "reason": reason,
        "requested_at": _epoch_ms_to_utc(params.get("startedAtMs")),
        "upstream_approval_id": None,
        "item_id": _identifier(params.get("itemId")),
        "command_kind": None,
        "changes": [],
        "review_truncated": reason_truncated,
    }


def item_changes(item: dict) -> dict:
    """Extract the reviewable summary of one observed fileChange thread item."""
    if not isinstance(item, dict) or item.get("type") != "fileChange":
        raise ApprovalDetailError("invalid_file_change_item")
    item_id = _identifier(item.get("id"))
    if item_id is None:
        raise ApprovalDetailError("invalid_file_change_item")
    raw_changes = item.get("changes")
    if raw_changes is None:
        raw_changes = []
    if not isinstance(raw_changes, list):
        raise ApprovalDetailError("invalid_file_change_item")
    changes: list[dict] = []
    truncated = False
    for entry in raw_changes[:MAX_CHANGES]:
        if not isinstance(entry, dict):
            raise ApprovalDetailError("invalid_file_change_item")
        path, path_truncated = _text(entry.get("path"), MAX_PATH_BYTES)
        kind = entry.get("kind")
        change_type = kind.get("type") if isinstance(kind, dict) else None
        move_path = None
        if isinstance(kind, dict):
            move_path, move_truncated = _text(kind.get("move_path"), MAX_PATH_BYTES)
            truncated = truncated or move_truncated
        diff, diff_truncated = _text(entry.get("diff"), MAX_DIFF_BYTES)
        truncated = truncated or path_truncated or diff_truncated
        changes.append({
            "path": path,
            "change_type": change_type,
            "move_path": move_path,
            "diff": diff,
            "diff_truncated": diff_truncated,
        })
    if len(raw_changes) > MAX_CHANGES:
        truncated = True
    return {"item_id": item_id, "status": item.get("status"),
            "changes": changes, "review_truncated": truncated}


def _relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if not value.isprintable() or "\x00" in value:
            return None
        if len(value.encode("utf-8")) > MAX_PATH_BYTES:
            return None
        path = PureWindowsPath(value) if "\\" in value else Path(value)
        if (path.is_absolute() or path.drive or path.root or not path.parts
                or value.startswith(("/", "\\"))):
            return None
        if any(part in ("..", "") for part in path.parts):
            return None
    except (TypeError, ValueError):
        return None
    return value


def _workspace_relative(value: Any, workspace_root: str | None) -> Any:
    """Turn an absolute path inside the registered workspace into a relative
    POSIX path. Anything else is returned unchanged so the review fails closed.
    """
    if (not isinstance(value, str) or not value
            or not isinstance(workspace_root, str) or not workspace_root):
        return value
    # A relative path is already in the review form.
    if _relative_path(value) is not None:
        return value
    try:
        if not value.isprintable() or "\x00" in value:
            return value
        if len(value.encode("utf-8")) > MAX_PATH_BYTES:
            return value
        candidate = (PureWindowsPath(value) if "\\" in value
                     or (len(value) > 1 and value[1] == ":") else Path(value))
        if not candidate.is_absolute():
            return value
        # UNC and device paths are never workspace-relative.
        drive = str(candidate.drive)
        if drive.startswith("\\\\") or candidate.anchor.startswith("\\\\"):
            return value
        if any(part in ("..", "") for part in candidate.parts):
            return value
        root = Path(workspace_root).resolve(strict=True)
        target = Path(value).resolve(strict=False)
        if target == root or not target.is_relative_to(root):
            return value
        relative = target.relative_to(root)
        if not relative.parts or any(part in ("", "..") for part in relative.parts):
            return value
        return relative.as_posix()
    except (OSError, RuntimeError, TypeError, ValueError):
        return value


def normalize_item_paths(summary: dict, workspace_root: str | None) -> dict:
    """Normalize one stored fileChange item summary before it is merged.

    Upstream items name files with absolute paths. The review and artifacts
    only accept relative paths inside the registered workspace, so the
    conversion happens here, once, before any review or artifact is written.
    A path that cannot be proven inside the workspace is kept as supplied and
    later recorded as ``unsafe_change_path``.
    """
    if (not isinstance(summary, dict)
            or not isinstance(workspace_root, str) or not workspace_root):
        return summary
    changes = summary.get("changes")
    if not isinstance(changes, list):
        return summary
    normalized: list[Any] = []
    changed = False
    for entry in changes:
        if not isinstance(entry, dict):
            normalized.append(entry)
            continue
        rewritten = dict(entry)
        path = _workspace_relative(entry.get("path"), workspace_root)
        if path != entry.get("path"):
            rewritten["path"] = path
            changed = True
        move_path = _workspace_relative(entry.get("move_path"), workspace_root)
        if move_path != entry.get("move_path"):
            rewritten["move_path"] = move_path
            changed = True
        normalized.append(rewritten)
    if not changed:
        return summary
    return {**summary, "changes": normalized}


def _cwd_inside_workspace(cwd: Any, workspace_root: str | None
                          ) -> tuple[str | None, bool]:
    """Return a displayable cwd only when it resolves inside the workspace."""
    if not isinstance(cwd, str) or not cwd or not isinstance(workspace_root, str):
        return None, False
    try:
        resolved = Path(cwd).resolve(strict=False)
        inside = resolved.is_relative_to(Path(workspace_root).resolve(strict=False))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None, False
    return (cwd, True) if inside else (None, False)


def _apply_changes(review: dict, raw_changes: Any, *, item_truncated: bool) -> None:
    """Fill review.changes and record why content cannot be judged."""
    missing = set(review["_missing"])
    truncated = bool(item_truncated)
    if not isinstance(raw_changes, list) or not raw_changes:
        missing.add("file_changes_missing")
        raw_changes = []
    for entry in raw_changes[:MAX_CHANGES]:
        if not isinstance(entry, dict):
            missing.add("file_change_invalid")
            continue
        path = _relative_path(entry.get("path"))
        if path is None:
            missing.add("unsafe_change_path")
        raw_move = entry.get("move_path")
        move_path = _relative_path(raw_move)
        if isinstance(raw_move, str) and raw_move and move_path is None:
            missing.add("unsafe_change_path")
        diff = entry.get("diff")
        if not isinstance(diff, str) or not diff:
            # A review that cannot show what will change must stay
            # inspectable but must not offer a one-time allow.
            missing.add("diff_missing")
        review["changes"].append({
            "path": path,
            "change_type": (entry.get("change_type")
                            if entry.get("change_type") in CHANGE_TYPES else None),
            "move_path": move_path,
            "diff": diff,
            "diff_truncated": bool(entry.get("diff_truncated")),
        })
    if item_truncated:
        review["review_truncated"] = True
    review["_missing"] = sorted(missing)


def _finish(review: dict) -> dict:
    """Apply the size budget and finalize status; idempotent on stored reviews."""
    missing = review.pop("_missing")
    review["missing"] = sorted(set(missing))
    review["details_status"] = INSUFFICIENT if review["missing"] else COMPLETE
    if len(_encoded(review)) > MAX_DETAILS_BYTES:
        review["changes"] = [
            {**change, "diff": None, "diff_truncated": bool(change.get("diff"))}
            for change in review["changes"]]
        review["review_truncated"] = True
        if len(_encoded(review)) > MAX_DETAILS_BYTES:
            review["changes"] = []
            review["missing"] = sorted({*review["missing"], "details_too_large"})
            review["details_status"] = INSUFFICIENT
    return review


def normalize(details: dict, *, workspace_root: str | None,
              recorded_at: str) -> dict:
    """Combine adapter input with item evidence into the stored review form.

    ``details_status`` is complete only when the content needed to judge the
    request is present and safe to display. Anything else keeps the record
    inspectable but forbids a one-time allow.
    """
    if not isinstance(details, dict) or details.get("kind") not in {
            "command", "file_change"}:
        raise ApprovalDetailError("invalid_approval_details")
    kind = details["kind"]
    review = {
        "kind": kind,
        "reason": details.get("reason"),
        "requested_at": details.get("requested_at") or recorded_at,
        "upstream_approval_id": details.get("upstream_approval_id"),
        "item_id": details.get("item_id"),
        "command": None,
        "command_truncated": False,
        "command_kind": details.get("command_kind"),
        "cwd": None,
        "cwd_inside_workspace": False,
        "changes": [],
        "review_truncated": bool(details.get("review_truncated")),
        "_missing": [],
    }
    if kind == "command":
        command = details.get("command")
        if not isinstance(command, str) or not command:
            review["_missing"].append("command_missing")
        else:
            review["command"] = command
            review["command_truncated"] = bool(details.get("command_truncated"))
        if details.get("command_kind") not in (None, "command"):
            # writeStdin and future kinds are not rendered by this slice.
            review["_missing"].append("unsupported_command_kind")
        cwd, inside = _cwd_inside_workspace(details.get("cwd"), workspace_root)
        review["cwd"] = cwd
        review["cwd_inside_workspace"] = inside
        if not inside:
            review["_missing"].append("cwd_unverified")
        if not details.get("item_id"):
            review["_missing"].append("item_id_missing")
    else:
        _apply_changes(review, details.get("changes"),
                       item_truncated=bool(details.get("review_truncated")))
        if not details.get("item_id"):
            review["_missing"].append("item_id_missing")
    return _finish(review)


def merge_item(review: dict, item_summary: dict) -> dict:
    """Fold one fileChange item into a stored file-review without guessing.

    Only a file_change review whose item identity matches is affected; other
    reviews are returned unchanged so callers can compare identity.
    """
    if (not isinstance(review, dict) or review.get("kind") != "file_change"
            or not isinstance(item_summary, dict)
            or review.get("item_id") != item_summary.get("item_id")):
        return review
    merged = {
        **review,
        "changes": [],
        "_missing": [reason for reason in review.get("missing", [])
                     if reason in {"item_id_missing"}],
        "review_truncated": bool(review.get("review_truncated")),
    }
    merged.pop("missing", None)
    merged.pop("details_status", None)
    _apply_changes(merged, item_summary.get("changes"),
                   item_truncated=bool(item_summary.get("review_truncated")))
    return _finish(merged)


def _encoded(review: dict) -> bytes:
    return json.dumps(review, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def encode(review: dict) -> str:
    """Serialize for storage; the status also lives in its own column."""
    return _encoded(review).decode("utf-8")


def redacted(missing_reason: str, recorded_at: str) -> dict:
    """Detail placeholder for records whose content was never stored."""
    return {
        "kind": None, "reason": None, "requested_at": recorded_at,
        "upstream_approval_id": None, "item_id": None, "command": None,
        "command_truncated": False, "command_kind": None, "cwd": None,
        "cwd_inside_workspace": False, "changes": [], "review_truncated": False,
        "missing": [missing_reason], "details_status": INSUFFICIENT,
    }


def decode(raw: str | None, fallback_reason: str, recorded_at: str) -> dict:
    """Read stored details; a damaged record is insufficient, never trusted."""
    if not isinstance(raw, str) or not raw:
        return redacted(fallback_reason, recorded_at)
    try:
        review = json.loads(raw)
    except (TypeError, ValueError):
        return redacted("details_unreadable", recorded_at)
    if (not isinstance(review, dict)
            or review.get("details_status") not in (COMPLETE, INSUFFICIENT)):
        return redacted("details_unreadable", recorded_at)
    return review


def can_allow(review: dict, *, status: str, decided: bool) -> bool:
    """A one-time allow needs complete, untruncated content and an open request."""
    return (status == "pending" and not decided
            and review.get("details_status") == COMPLETE
            and not review.get("review_truncated"))


def workspace_name(root: str | None) -> str | None:
    """A display alias must not leak the absolute local path."""
    if not isinstance(root, str) or not root:
        return None
    try:
        return Path(root).name or None
    except (TypeError, ValueError):
        return None


def diff_reference(change: dict, *, run_id: str | None) -> str | None:
    """Opaque, non-path reference the client can use for artifact lookups."""
    if not isinstance(change, dict) or not isinstance(change.get("path"), str):
        return None
    run = run_id if isinstance(run_id, str) and run_id else "run"
    return f"diffref:{run}:{quote(change['path'], safe='')}"