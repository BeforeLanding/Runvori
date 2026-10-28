"""Best-effort current Git worktree snapshot artifacts.

This is deliberately a *snapshot at first artifact read*, not a claim about
what the agent changed.  Every artifact is attributed ``unknown`` because a
worktree can contain changes that predate the run.  Git is invoked with an
argument list and a timeout; no client-supplied path ever reaches it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from runvori.infrastructure import artifact_store


MAX_GIT_FILES = 16
GIT_TIMEOUT_SECONDS = 5


def _git(root: Path, args: list[str]) -> tuple[int | None, bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None, b""
    return result.returncode, result.stdout


def _marker(connection, *, run_id: str, recorded_at: str, reason: str,
            source_status: str) -> None:
    artifact_id = artifact_store.make_id(
        run_id=run_id, kind="file_diff", source="git_worktree_snapshot",
        path=None, change_index=0)
    artifact_store.ensure(
        connection, artifact_id=artifact_id, run_id=run_id,
        kind="file_diff", source="git_worktree_snapshot", path=None,
        operation="snapshot", source_status=source_status,
        attribution="unknown", representation="unknown", reason=reason,
        created_at=recorded_at, updated_at=recorded_at)


def _entries(status: bytes) -> list[dict[str, Any]]:
    tokens = status.split(b"\0")
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if len(token) < 4 or token[2:3] != b" ":
            continue
        code = token[:2].decode("ascii", "replace")
        path = token[3:].decode("utf-8", "replace")
        move_path = None
        if "R" in code or "C" in code:
            if index < len(tokens):
                move_path = tokens[index].decode("utf-8", "replace")
                index += 1
        result.append({"code": code, "path": path, "move_path": move_path})
    return result


def _operation(code: str) -> str:
    if code == "??":
        return "add"
    if "R" in code:
        return "move"
    if "C" in code:
        return "copy"
    if "A" in code:
        return "add"
    if "D" in code:
        return "delete"
    return "update"


def ensure_snapshot(connection, *, run_id: str, workspace_root: str,
                    recorded_at: str) -> None:
    """Create one snapshot's artifacts; repeated calls are a no-op."""
    existing = connection.execute(
        "SELECT 1 FROM run_artifacts WHERE run_id=? "
        "AND source='git_worktree_snapshot' LIMIT 1", (run_id,)).fetchone()
    if existing is not None:
        return
    try:
        root = Path(workspace_root)
    except (TypeError, ValueError):
        return
    if not root.is_dir():
        return

    returncode, _ = _git(root, ["rev-parse", "--is-inside-work-tree"])
    if returncode is None:
        _persist(connection, run_id=run_id, recorded_at=recorded_at,
                 entries=[], marker=("git_unavailable", "unavailable"))
        return
    if returncode != 0:
        _persist(connection, run_id=run_id, recorded_at=recorded_at,
                 entries=[], marker=("not_a_git_worktree", "unavailable"))
        return
    returncode, status = _git(
        root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if returncode != 0:
        _persist(connection, run_id=run_id, recorded_at=recorded_at,
                 entries=[], marker=("git_unavailable", "unavailable"))
        return
    parsed = _entries(status)
    if not parsed:
        _persist(connection, run_id=run_id, recorded_at=recorded_at,
                 entries=[], marker=("no_changes", "captured"))
        return

    snapshots: list[dict[str, Any]] = []
    for entry in parsed[:MAX_GIT_FILES]:
        path = entry["path"]
        operation = _operation(entry["code"])
        if entry["code"] == "??":
            snapshots.append({
                "path": path, "move_path": None, "operation": "add",
                "content": None, "available": False,
                "reason": "untracked_content_not_captured",
                "representation": "unknown", "truncated": False,
                "dropped_bytes": 0,
            })
            continue
        returncode, diff = _git(
            root, ["diff", "--no-ext-diff", "--no-textconv", "--binary",
                   "HEAD", "--", path])
        if returncode != 0 or not diff:
            snapshots.append({
                "path": path, "move_path": entry["move_path"],
                "operation": operation, "content": None, "available": False,
                "reason": "diff_unavailable", "representation": "unknown",
                "truncated": False, "dropped_bytes": 0,
            })
            continue
        binary = b"GIT binary patch" in diff or b"Binary files " in diff
        if binary:
            snapshots.append({
                "path": path, "move_path": entry["move_path"],
                "operation": operation, "content": None, "available": False,
                "reason": "binary_metadata_only", "representation": "binary",
                "truncated": False, "dropped_bytes": 0,
            })
            continue
        dropped = max(0, len(diff) - artifact_store.MAX_ARTIFACT_BYTES)
        content = diff[:artifact_store.MAX_ARTIFACT_BYTES]
        snapshots.append({
            "path": path, "move_path": entry["move_path"],
            "operation": operation, "content": content, "available": True,
            "reason": "artifact_size_limit" if dropped else None,
            "representation": "text", "truncated": bool(dropped),
            "dropped_bytes": dropped,
        })
    marker = ("entry_limit", "truncated") if len(parsed) > MAX_GIT_FILES else None
    _persist(connection, run_id=run_id, recorded_at=recorded_at,
             entries=snapshots, marker=marker)


def _persist(connection, *, run_id: str, recorded_at: str,
             entries: list[dict[str, Any]],
             marker: tuple[str, str] | None) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT 1 FROM run_artifacts WHERE run_id=? "
            "AND source='git_worktree_snapshot' LIMIT 1", (run_id,)).fetchone()
        if existing is not None:
            connection.execute("COMMIT")
            return
        for index, entry in enumerate(entries):
            artifact_id = artifact_store.make_id(
                run_id=run_id, kind="file_diff",
                source="git_worktree_snapshot", path=entry["path"],
                change_index=index)
            artifact_store.ensure(
                connection, artifact_id=artifact_id, run_id=run_id,
                kind="file_diff", source="git_worktree_snapshot",
                path=entry["path"], move_path=entry["move_path"],
                operation=entry["operation"],
                source_status="snapshot_at_read", attribution="unknown",
                representation=entry["representation"], reason=entry["reason"],
                created_at=recorded_at, updated_at=recorded_at)
            artifact_store.replace_content(
                connection, artifact_id, entry["content"],
                representation=entry["representation"],
                truncated=entry["truncated"],
                dropped_bytes=entry["dropped_bytes"],
                available=entry["available"], reason=entry["reason"],
                source_status="snapshot_at_read", updated_at=recorded_at)
        if marker is not None:
            _marker(connection, run_id=run_id, recorded_at=recorded_at,
                    reason=marker[0], source_status=marker[1])
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
