"""Bounded, persisted artifacts derived from journal evidence.

Artifacts are addressed by opaque deterministic ids.  Their content is a copy
of what the journal actually persisted, never a fresh read of the upstream
process or of the workspace: a byte the timeline budget dropped stays dropped
and is reported as ``dropped_bytes``.

The table and its chunks are written in the same SQLite transaction as the
event or approval they describe.  A restart therefore cannot expose an artifact
whose metadata claims content the database does not have.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any, Iterable


ARTIFACT_ID_PATTERN = re.compile(r"^art_[0-9a-f]{32}$")
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
ARTIFACT_CHUNK_BYTES = 64 * 1024
MAX_ARTIFACT_READ_BYTES = 256 * 1024
MAX_ARTIFACT_PAGE = 100
MAX_ARTIFACT_PATH_BYTES = 1024
MAX_ARTIFACT_FILES = 64
KINDS = frozenset({"command_output", "file_diff"})
SOURCES = frozenset({
    "command_execution", "approval_proposal", "upstream_report",
    "git_worktree_snapshot",
})
REPRESENTATIONS = frozenset({"text", "binary", "unknown"})
ATTRIBUTIONS = frozenset({"proposed", "reported", "unknown", "not_applicable"})
CHANGE_TYPES = frozenset({"add", "delete", "update", "move", "copy", "type_change"})


ARTIFACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    kind TEXT NOT NULL CHECK (kind IN
        ('command_output', 'file_diff')),
    source TEXT NOT NULL CHECK (source IN
        ('command_execution', 'approval_proposal', 'upstream_report',
         'git_worktree_snapshot')),
    item_id TEXT,
    approval_id TEXT,
    path TEXT,
    move_path TEXT,
    operation TEXT,
    source_status TEXT,
    attribution TEXT NOT NULL DEFAULT 'unknown' CHECK (attribution IN
        ('proposed', 'reported', 'unknown', 'not_applicable')),
    representation TEXT NOT NULL DEFAULT 'unknown' CHECK (representation IN
        ('text', 'binary', 'unknown')),
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    sha256 TEXT,
    truncated INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    dropped_bytes INTEGER NOT NULL DEFAULT 0 CHECK (dropped_bytes >= 0),
    available INTEGER NOT NULL DEFAULT 0 CHECK (available IN (0, 1)),
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_chunks (
    artifact_id TEXT NOT NULL REFERENCES run_artifacts(artifact_id)
        ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    data BLOB NOT NULL,
    PRIMARY KEY (artifact_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS run_artifacts_run_created
    ON run_artifacts(run_id, created_at, artifact_id);
"""


class ArtifactError(ValueError):
    """A stable, content-free artifact refusal."""


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return None
    if len(encoded) > limit:
        return None
    return value


def _path(value: Any) -> str | None:
    return _text(value, MAX_ARTIFACT_PATH_BYTES)


def validate_artifact_id(artifact_id: Any) -> str:
    if not isinstance(artifact_id, str) or not ARTIFACT_ID_PATTERN.fullmatch(
            artifact_id):
        raise ArtifactError("invalid_artifact_id")
    return artifact_id


def make_id(*, run_id: str, kind: str, source: str, item_id: str | None = None,
            approval_id: str | None = None, path: str | None = None,
            change_index: int | None = None) -> str:
    """Deterministic id; no local path or database row id is exposed."""
    if (not isinstance(run_id, str) or not run_id
            or kind not in KINDS or source not in SOURCES):
        raise ArtifactError("invalid_artifact_identity")
    parts = [
        run_id, kind, source, item_id or "", approval_id or "", path or "",
        "" if change_index is None else str(change_index),
    ]
    try:
        encoded = "\0".join(parts).encode("utf-8")
    except UnicodeError as error:
        raise ArtifactError("invalid_artifact_identity") from error
    return "art_" + hashlib.sha256(encoded).hexdigest()[:32]


def _row(connection: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
    artifact_id = validate_artifact_id(artifact_id)
    row = connection.execute(
        "SELECT * FROM run_artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if row is None:
        raise ArtifactError("artifact_not_found")
    return row


def _view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artifact_id": row["artifact_id"],
        "run_id": row["run_id"],
        "kind": row["kind"],
        "source": row["source"],
        "item_id": row["item_id"],
        "approval_id": row["approval_id"],
        "path": row["path"],
        "move_path": row["move_path"],
        "operation": row["operation"],
        "source_status": row["source_status"],
        "attribution": row["attribution"],
        "representation": row["representation"],
        "size_bytes": int(row["size_bytes"]),
        "sha256": row["sha256"],
        "truncated": bool(row["truncated"]),
        "dropped_bytes": int(row["dropped_bytes"]),
        "content_available": bool(row["available"]),
        "reason": row["reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def ensure(connection: sqlite3.Connection, *, artifact_id: str, run_id: str,
           kind: str, source: str, item_id: str | None = None,
           approval_id: str | None = None, path: str | None = None,
           move_path: str | None = None, operation: str | None = None,
           source_status: str | None = None,
           attribution: str = "unknown", representation: str = "unknown",
           reason: str | None = None, created_at: str,
           updated_at: str) -> str:
    """Create a metadata row once; later calls only refresh descriptive fields."""
    artifact_id = validate_artifact_id(artifact_id)
    if (not isinstance(run_id, str) or not run_id or kind not in KINDS
            or source not in SOURCES or representation not in REPRESENTATIONS
            or attribution not in ATTRIBUTIONS
            or not isinstance(created_at, str) or not created_at
            or not isinstance(updated_at, str) or not updated_at):
        raise ArtifactError("invalid_artifact_metadata")
    connection.execute(
        "INSERT INTO run_artifacts(artifact_id, run_id, kind, source, item_id, "
        "approval_id, path, move_path, operation, source_status, attribution, "
        "representation, reason, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(artifact_id) DO UPDATE SET "
        "source_status=COALESCE(excluded.source_status, source_status), "
        "attribution=excluded.attribution, "
        "operation=COALESCE(excluded.operation, operation), "
        "move_path=COALESCE(excluded.move_path, move_path), "
        "reason=COALESCE(excluded.reason, reason), updated_at=excluded.updated_at",
        (artifact_id, run_id, kind, source, _text(item_id, 256),
         _text(approval_id, 256), _path(path), _path(move_path),
         _text(operation, 64), _text(source_status, 64), attribution,
         representation, _text(reason, 128), created_at, updated_at))
    return artifact_id


def append_content(connection: sqlite3.Connection, artifact_id: str,
                   data: bytes, *, updated_at: str) -> int:
    """Append bytes at the end of an artifact; never fabricate dropped content."""
    artifact_id = validate_artifact_id(artifact_id)
    if not isinstance(data, bytes):
        raise ArtifactError("invalid_artifact_content")
    row = _row(connection, artifact_id)
    if not isinstance(updated_at, str) or not updated_at:
        raise ArtifactError("invalid_artifact_metadata")
    remaining = MAX_ARTIFACT_BYTES - int(row["size_bytes"])
    if remaining <= 0:
        if data:
            connection.execute(
                "UPDATE run_artifacts SET truncated=1, "
                "dropped_bytes=dropped_bytes+?, reason='artifact_size_limit', "
                "updated_at=? WHERE artifact_id=?",
                (len(data), updated_at, artifact_id))
        return 0
    stored = data[:remaining]
    dropped = len(data) - len(stored)
    if stored:
        index = connection.execute(
            "SELECT COALESCE(MAX(chunk_index), -1)+1 FROM artifact_chunks "
            "WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
        connection.execute(
            "INSERT INTO artifact_chunks(artifact_id, chunk_index, data) "
            "VALUES (?, ?, ?)", (artifact_id, index, stored))
        connection.execute(
            "UPDATE run_artifacts SET size_bytes=size_bytes+?, updated_at=? "
            "WHERE artifact_id=?", (len(stored), updated_at, artifact_id))
    if dropped:
        connection.execute(
            "UPDATE run_artifacts SET truncated=1, "
            "dropped_bytes=dropped_bytes+?, reason='artifact_size_limit', "
            "updated_at=? WHERE artifact_id=?",
            (dropped, updated_at, artifact_id))
    return len(stored)


def _insert_chunks(connection: sqlite3.Connection, artifact_id: str,
                   data: bytes) -> None:
    for index, start in enumerate(range(0, len(data), ARTIFACT_CHUNK_BYTES)):
        connection.execute(
            "INSERT INTO artifact_chunks(artifact_id, chunk_index, data) "
            "VALUES (?, ?, ?)", (artifact_id, index,
                                 data[start:start + ARTIFACT_CHUNK_BYTES]))


def replace_content(connection: sqlite3.Connection, artifact_id: str,
                    data: bytes | None, *, representation: str,
                    truncated: bool, dropped_bytes: int, available: bool,
                    reason: str | None, source_status: str | None,
                    updated_at: str) -> None:
    """Replace content for a mutable source (a file review may gain its diff)."""
    artifact_id = validate_artifact_id(artifact_id)
    if (representation not in REPRESENTATIONS or type(truncated) is not bool
            or type(dropped_bytes) is not int or dropped_bytes < 0
            or type(available) is not bool or not isinstance(updated_at, str)
            or not updated_at):
        raise ArtifactError("invalid_artifact_metadata")
    _row(connection, artifact_id)
    if data is not None and not isinstance(data, bytes):
        raise ArtifactError("invalid_artifact_content")
    connection.execute("DELETE FROM artifact_chunks WHERE artifact_id=?",
                       (artifact_id,))
    if data:
        if len(data) > MAX_ARTIFACT_BYTES:
            dropped_bytes += len(data) - MAX_ARTIFACT_BYTES
            data = data[:MAX_ARTIFACT_BYTES]
            truncated = True
        _insert_chunks(connection, artifact_id, data)
    digest = None if data is None else hashlib.sha256(data).hexdigest()
    connection.execute(
        "UPDATE run_artifacts SET representation=?, size_bytes=?, sha256=?, "
        "truncated=?, dropped_bytes=?, available=?, reason=?, "
        "source_status=COALESCE(?, source_status), updated_at=? "
        "WHERE artifact_id=?",
        (representation, 0 if data is None else len(data), digest,
         1 if truncated else 0, dropped_bytes, 1 if available else 0,
         _text(reason, 128), _text(source_status, 64), updated_at, artifact_id))


def _content(connection: sqlite3.Connection, artifact_id: str) -> bytes:
    chunks = connection.execute(
        "SELECT data FROM artifact_chunks WHERE artifact_id=? "
        "ORDER BY chunk_index", (artifact_id,)).fetchall()
    return b"".join(bytes(row["data"]) for row in chunks)


def update_status(connection: sqlite3.Connection, artifact_id: str, *,
                  truncated: bool, dropped_bytes: int, available: bool,
                  reason: str | None, source_status: str | None,
                  representation: str = "text", updated_at: str) -> None:
    """Finalize metadata without replacing the already persisted chunks."""
    artifact_id = validate_artifact_id(artifact_id)
    if (type(truncated) is not bool or type(dropped_bytes) is not int
            or dropped_bytes < 0 or type(available) is not bool
            or representation not in REPRESENTATIONS
            or not isinstance(updated_at, str) or not updated_at):
        raise ArtifactError("invalid_artifact_metadata")
    _row(connection, artifact_id)
    data = _content(connection, artifact_id)
    connection.execute(
        "UPDATE run_artifacts SET representation=?, size_bytes=?, sha256=?, "
        "truncated=?, dropped_bytes=?, available=?, reason=?, "
        "source_status=COALESCE(?, source_status), updated_at=? "
        "WHERE artifact_id=?",
        (representation, len(data), hashlib.sha256(data).hexdigest(),
         1 if truncated else 0, dropped_bytes, 1 if available else 0,
         _text(reason, 128), _text(source_status, 64), updated_at, artifact_id))


def get(connection: sqlite3.Connection, artifact_id: str) -> dict[str, Any]:
    return _view(_row(connection, artifact_id))


def list_for_run(connection: sqlite3.Connection, run_id: str, *,
                 after_artifact_id: str | None = None,
                 limit: int = 50) -> dict[str, Any]:
    if (not isinstance(run_id, str) or not run_id
            or type(limit) is not int or not 1 <= limit <= MAX_ARTIFACT_PAGE
            or (after_artifact_id is not None
                and (not isinstance(after_artifact_id, str)
                     or not after_artifact_id))):
        raise ArtifactError("invalid_cursor")
    cursor = None
    if after_artifact_id is not None:
        cursor = connection.execute(
            "SELECT created_at, artifact_id FROM run_artifacts "
            "WHERE run_id=? AND artifact_id=?",
            (run_id, after_artifact_id)).fetchone()
        if cursor is None:
            raise ArtifactError("invalid_cursor")
    if cursor is None:
        rows = connection.execute(
            "SELECT * FROM run_artifacts WHERE run_id=? "
            "ORDER BY created_at ASC, artifact_id ASC LIMIT ?",
            (run_id, limit + 1)).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM run_artifacts WHERE run_id=? AND "
            "(created_at > ? OR (created_at = ? AND artifact_id > ?)) "
            "ORDER BY created_at ASC, artifact_id ASC LIMIT ?",
            (run_id, cursor["created_at"], cursor["created_at"],
             cursor["artifact_id"], limit + 1)).fetchall()
    selected = rows[:limit]
    return {
        "items": [_view(row) for row in selected],
        "next_cursor": selected[-1]["artifact_id"] if len(rows) > limit else None,
        "has_more": len(rows) > limit,
    }


def read_content(connection: sqlite3.Connection, artifact_id: str, *,
                 offset: int = 0, limit: int = ARTIFACT_CHUNK_BYTES
                 ) -> dict[str, Any]:
    if (type(offset) is not int or type(limit) is not int or offset < 0
            or not 1 <= limit <= MAX_ARTIFACT_READ_BYTES):
        raise ArtifactError("invalid_range")
    row = _row(connection, artifact_id)
    if not bool(row["available"]):
        raise ArtifactError("artifact_unavailable")
    size = int(row["size_bytes"])
    if offset > size:
        raise ArtifactError("invalid_range")
    data = _content(connection, artifact_id)
    if len(data) != size:
        # A damaged row must not silently return a shorter body.
        raise ArtifactError("artifact_unavailable")
    chunk = data[offset:offset + limit]
    return {
        "artifact_id": artifact_id,
        "data": chunk,
        "size_bytes": size,
        "offset": offset,
        "next_offset": offset + len(chunk),
        "sha256": row["sha256"],
        "representation": row["representation"],
        "truncated": bool(row["truncated"]),
        "dropped_bytes": int(row["dropped_bytes"]),
    }


def sync_file_changes(connection: sqlite3.Connection, *, run_id: str,
                      source: str, approval_id: str | None, item_id: str | None,
                      changes: Iterable[Any], source_status: str | None,
                      attribution: str, created_at: str,
                      updated_at: str) -> list[str]:
    """Persist one artifact per reviewable change without trusting a local path."""
    if source not in {"approval_proposal", "upstream_report",
                      "git_worktree_snapshot"}:
        raise ArtifactError("invalid_artifact_source")
    if not isinstance(run_id, str) or not run_id:
        raise ArtifactError("invalid_artifact_identity")
    result: list[str] = []
    for index, change in enumerate(changes):
        if index >= MAX_ARTIFACT_FILES:
            break
        if not isinstance(change, dict):
            continue
        raw_path = change.get("path")
        path = _path(raw_path)
        operation = (change.get("change_type")
                     if change.get("change_type") in CHANGE_TYPES else None)
        move_path = _path(change.get("move_path"))
        diff = change.get("diff")
        diff = diff if isinstance(diff, str) else None
        diff_truncated = bool(change.get("diff_truncated"))
        artifact_id = make_id(
            run_id=run_id, kind="file_diff", source=source,
            item_id=item_id, approval_id=approval_id, path=path,
            change_index=index)
        ensure(
            connection, artifact_id=artifact_id, run_id=run_id,
            kind="file_diff", source=source, item_id=item_id,
            approval_id=approval_id, path=path, move_path=move_path,
            operation=operation, source_status=source_status,
            attribution=attribution,
            representation="text" if diff is not None else "unknown",
            reason="diff_unavailable" if diff is None else None,
            created_at=created_at, updated_at=updated_at)
        existing = _row(connection, artifact_id)
        if diff is None and bool(existing["available"]):
            # A later item update with less content must not erase an earlier
            # reviewable diff; it only refreshes the source status.
            connection.execute(
                "UPDATE run_artifacts SET source_status=COALESCE(?, "
                "source_status), updated_at=? WHERE artifact_id=?",
                (_text(source_status, 64), updated_at, artifact_id))
            result.append(artifact_id)
            continue
        if path is None:
            replace_content(
                connection, artifact_id, None, representation="unknown",
                truncated=False, dropped_bytes=0, available=False,
                reason="unsafe_path", source_status=source_status,
                updated_at=updated_at)
        else:
            data = diff.encode("utf-8") if diff is not None else None
            replace_content(
                connection, artifact_id, data,
                representation="text" if data is not None else "unknown",
                truncated=diff_truncated, dropped_bytes=0,
                available=data is not None,
                reason=("diff_truncated" if diff_truncated else
                        None if data is not None else "diff_unavailable"),
                source_status=source_status, updated_at=updated_at)
        result.append(artifact_id)
    return result
