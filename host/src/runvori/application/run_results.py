"""Read-side run results built from persisted command evidence.

The verifier itself is reused from ``runvori.codex.verification``. Product
runs are not upstream probes: required commands are declared by the trusted
local workspace registration, are matched by exact command text and resolved
cwd, and a truncated command can never satisfy a requirement.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runvori.application import git_artifacts
from runvori.codex.verification import (
    RequiredCommandEvidence, RequiredVerificationSet,
)
from runvori.infrastructure import artifact_store
from runvori.infrastructure.event_journal import EventJournalError

TERMINAL_RUNS = frozenset({"completed", "failed", "interrupted"})
MAX_LIMIT = 100


def _epoch_ms_to_utc(value: Any) -> str | None:
    if type(value) is not int or value < 0 or value >= 2**62:
        return None
    try:
        moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.isoformat().replace("+00:00", "Z")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_git_snapshot(connection: sqlite3.Connection, run_id: str) -> None:
    workspace = connection.execute(
        "SELECT w.root FROM runs AS r "
        "JOIN session_workspaces AS sw ON sw.session_id=r.session_id "
        "JOIN workspaces AS w ON w.workspace_id=sw.workspace_id "
        "WHERE r.run_id=?", (run_id,)).fetchone()
    if workspace is None:
        return
    try:
        git_artifacts.ensure_snapshot(
            connection, run_id=run_id, workspace_root=workspace["root"],
            recorded_at=_now_utc())
    except (OSError, ValueError, sqlite3.Error):
        # The worktree view is best-effort; command and approval evidence
        # must remain readable even when Git is unavailable.
        pass


def _run_row(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    if not isinstance(run_id, str) or not run_id:
        raise EventJournalError("run_not_found")
    row = connection.execute(
        "SELECT run_id, session_id, status, version, terminal_event_seq "
        "FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise EventJournalError("run_not_found")
    return row


def _run_times(connection: sqlite3.Connection, row: sqlite3.Row) -> tuple[str | None, str | None]:
    """Find the first and terminal event timestamps for a run."""
    needle = '"run_id":' + json.dumps(row["run_id"], ensure_ascii=False)
    first = connection.execute(
        "SELECT event_json FROM events WHERE session_id=? AND instr(event_json, ?) > 0 "
        "ORDER BY seq ASC LIMIT 1",
        (row["session_id"], needle)).fetchone()
    created_at = None
    if first is not None:
        try:
            created_at = json.loads(first["event_json"]).get("recorded_at")
        except (TypeError, ValueError):
            created_at = None
    terminal_at = None
    if type(row["terminal_event_seq"]) is int:
        event = connection.execute(
            "SELECT event_json FROM events WHERE session_id=? AND seq=?",
            (row["session_id"], row["terminal_event_seq"])).fetchone()
        if event is not None:
            try:
                terminal_at = json.loads(event["event_json"]).get("recorded_at")
            except (TypeError, ValueError):
                terminal_at = None
    return created_at, terminal_at


def _command_view(row: sqlite3.Row, output_artifact_id: str | None = None
                  ) -> dict[str, Any]:
    exit_code = row["exit_code"] if type(row["exit_code"]) is int else None
    return {
        "run_id": row["run_id"],
        "item_id": row["item_id"],
        "output_artifact_id": output_artifact_id,
        "command": row["command"],
        "command_truncated": bool(row["command_truncated"]),
        "cwd": row["cwd"],
        "status": row["status"],
        "exit_code": exit_code,
        "exit_code_present": exit_code is not None,
        "started_at": _epoch_ms_to_utc(row["started_at_ms"]),
        "completed_at": _epoch_ms_to_utc(row["completed_at_ms"]),
        "duration_ms": (row["duration_ms"]
                        if type(row["duration_ms"]) is int else None),
        "output_state": row["output_state"],
        "output_bytes": int(row["output_bytes"]),
        "output_dropped_bytes": int(row["output_dropped_bytes"]),
        "started_event_seq": row["started_event_seq"],
        "completed_event_seq": row["completed_event_seq"],
    }


def run_commands(connection: sqlite3.Connection, run_id: str, *,
                 after_item_id: str | None = None,
                 limit: int = 50) -> dict[str, Any]:
    """Return a stable command page ordered by the command's start event."""
    if (type(limit) is not int or not 1 <= limit <= MAX_LIMIT
            or (after_item_id is not None
                and (not isinstance(after_item_id, str) or not after_item_id))):
        raise EventJournalError("invalid_cursor")
    connection.execute("BEGIN")
    try:
        row = _run_row(connection, run_id)
        sort_expr = "COALESCE(started_event_seq, completed_event_seq, 0)"
        params: list[Any] = [row["run_id"]]
        where = "run_id=?"
        if after_item_id is not None:
            cursor = connection.execute(
                f"SELECT {sort_expr} AS sort_seq FROM run_commands "
                "WHERE run_id=? AND item_id=?", (row["run_id"], after_item_id)
            ).fetchone()
            if cursor is None:
                raise EventJournalError("invalid_cursor")
            where += f" AND ({sort_expr} > ? OR ({sort_expr} = ? AND item_id > ?))"
            params.extend([cursor["sort_seq"], cursor["sort_seq"], after_item_id])
        rows = connection.execute(
            f"SELECT * FROM run_commands WHERE {where} "
            f"ORDER BY {sort_expr} ASC, item_id ASC LIMIT ?",
            (*params, limit + 1)).fetchall()
        selected = rows[:limit]
        artifacts = {
            row["item_id"]: row["artifact_id"]
            for row in connection.execute(
                "SELECT item_id, artifact_id FROM run_artifacts "
                "WHERE run_id=? AND kind='command_output' "
                "AND source='command_execution'", (row["run_id"],)).fetchall()
        }
        result = {
            "items": [_command_view(item, artifacts.get(item["item_id"]))
                      for item in selected],
            "next_cursor": (selected[-1]["item_id"]
                            if len(rows) > limit else None),
            "has_more": len(rows) > limit,
        }
        connection.execute("COMMIT")
        return result
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _approved_ids(connection: sqlite3.Connection, run_id: str,
                  requirements: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Map approved command item ids to requirements by exact review text."""
    rows = connection.execute(
        "SELECT item_id, details_json FROM approvals WHERE run_id=? "
        "AND kind='command' AND decision='accept' AND item_id IS NOT NULL",
        (run_id,)).fetchall()
    matches: list[tuple[str, str]] = []
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(details, dict) or details.get("command_truncated"):
            continue
        command = details.get("command")
        cwd = details.get("cwd")
        if not isinstance(command, str) or not isinstance(cwd, str):
            continue
        for requirement in requirements:
            if command != requirement["command"]:
                continue
            try:
                if (Path(cwd).resolve()
                        != Path(requirement["cwd"]).resolve()):
                    continue
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            matches.append((requirement["key"], row["item_id"]))
    return matches


def _verification_reason(status: str, command: dict[str, Any] | None) -> str:
    if status == "passed":
        return "exit_zero"
    if status == "failed":
        if command is not None and command.get("exit_code_present"):
            return "nonzero_exit"
        return "command_failed"
    if status == "unknown":
        return "missing_or_ambiguous_evidence"
    if status == "not_run":
        return "no_execution_evidence"
    if status == "running":
        return "executing"
    return "awaiting_execution"


def _verification(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    requirements = [dict(item) for item in connection.execute(
        "SELECT requirement_key AS key, command, cwd FROM run_verifications "
        "WHERE run_id=? ORDER BY requirement_key", (row["run_id"],)).fetchall()]
    if not requirements:
        return {"configured": False, "summary": "not_run", "items": []}
    binding = connection.execute(
        "SELECT thread_id, turn_id FROM upstream_turns WHERE run_id=?",
        (row["run_id"],)).fetchone()
    thread_id = "" if binding is None else binding["thread_id"]
    turn_id = "" if binding is None else binding["turn_id"]
    verifier = RequiredVerificationSet({
        item["key"]: RequiredCommandEvidence(
            thread_id=thread_id, turn_id=turn_id, cwd=Path(item["cwd"]),
            matches_command=lambda value, expected=item["command"]:
            isinstance(value, str) and value == expected)
        for item in requirements})
    command_rows = connection.execute(
        "SELECT * FROM run_commands WHERE run_id=? "
        "ORDER BY COALESCE(started_event_seq, completed_event_seq, 0), item_id",
        (row["run_id"],)).fetchall()
    by_item = {item["item_id"]: item for item in command_rows}
    for command in command_rows:
        command_text = (None if command["command_truncated"]
                        else command["command"])
        item = {
            "type": "commandExecution",
            "id": command["item_id"],
            "command": command_text,
            "cwd": command["cwd"],
            "exitCode": command["exit_code"],
            "status": command["status"],
        }
        if command["started_event_seq"] is not None:
            verifier.observe("item/started", {
                "threadId": thread_id, "turnId": turn_id,
                "startedAtMs": command["started_at_ms"], "item": item})
        if command["completed_event_seq"] is not None:
            verifier.observe("item/completed", {
                "threadId": thread_id, "turnId": turn_id,
                "completedAtMs": command["completed_at_ms"], "item": item})
    for key, item_id in _approved_ids(connection, row["run_id"], requirements):
        verifier.note_approved(key, item_id)
    snapshot = verifier.snapshot(row["status"])
    items: list[dict[str, Any]] = []
    for requirement in requirements:
        key = requirement["key"]
        tracker = verifier.requirements[key]
        evidence_id = None
        if (len(tracker.started) == 1 and len(tracker.completed) == 1
                and set(tracker.started) == set(tracker.completed)):
            evidence_id = next(iter(tracker.started))
        elif len(tracker.started) == 1:
            evidence_id = next(iter(tracker.started))
        elif len(tracker.completed) == 1:
            evidence_id = next(iter(tracker.completed))
        elif len(tracker.approved_ids) == 1:
            evidence_id = next(iter(tracker.approved_ids))
        command = None if evidence_id is None else by_item.get(evidence_id)
        command_view = None if command is None else _command_view(command)
        status = snapshot["items"][key]
        items.append({
            "key": key,
            "command": requirement["command"],
            "cwd": requirement["cwd"],
            "status": status,
            "reason": _verification_reason(status, command_view),
            "exit_code": (None if command_view is None
                          else command_view["exit_code"]),
            "exit_code_present": (False if command_view is None
                                  else command_view["exit_code_present"]),
            "evidence_item_id": evidence_id,
            "started_at": (None if command_view is None
                           else command_view["started_at"]),
            "completed_at": (None if command_view is None
                             else command_view["completed_at"]),
            "duration_ms": (None if command_view is None
                            else command_view["duration_ms"]),
            "command_truncated": (False if command_view is None
                                  else command_view["command_truncated"]),
            "output_state": ("unknown" if command_view is None
                             else command_view["output_state"]),
            "output_bytes": 0 if command_view is None else command_view["output_bytes"],
            "output_dropped_bytes": (0 if command_view is None
                                     else command_view["output_dropped_bytes"]),
            "started_event_seq": (None if command is None
                                  else command["started_event_seq"]),
            "completed_event_seq": (None if command is None
                                    else command["completed_event_seq"]),
        })
    return {"configured": True, "summary": snapshot["summary"],
            "items": items}


def _payload(connection: sqlite3.Connection, row: sqlite3.Row,
             *, include_items: bool) -> dict[str, Any]:
    created_at, terminal_at = _run_times(connection, row)
    verification = _verification(connection, row)
    if not include_items:
        verification = {key: value for key, value in verification.items()
                        if key != "items"}
    count = connection.execute(
        "SELECT COUNT(*) FROM run_commands WHERE run_id=?",
        (row["run_id"],)).fetchone()[0]
    artifacts = connection.execute(
        "SELECT COUNT(*) FROM run_artifacts WHERE run_id=?",
        (row["run_id"],)).fetchone()[0]
    return {
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "version": row["version"],
        "terminal": row["status"] in TERMINAL_RUNS,
        "created_at": created_at,
        "terminal_at": terminal_at,
        "verification": verification,
        "command_count": int(count),
        "artifact_count": int(artifacts),
    }


def run_detail(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    connection.execute("BEGIN")
    try:
        result = _payload(connection, _run_row(connection, run_id),
                          include_items=False)
        connection.execute("COMMIT")
        return result
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def run_results(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    _run_row(connection, run_id)
    _ensure_git_snapshot(connection, run_id)
    connection.execute("BEGIN")
    try:
        result = _payload(connection, _run_row(connection, run_id),
                          include_items=True)
        connection.execute("COMMIT")
        return result
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def run_artifacts(connection: sqlite3.Connection, run_id: str, *,
                  after_artifact_id: str | None = None,
                  limit: int = 50) -> dict[str, Any]:
    """List artifact metadata for one run; content stays behind its own id."""
    _run_row(connection, run_id)
    _ensure_git_snapshot(connection, run_id)
    connection.execute("BEGIN")
    try:
        _run_row(connection, run_id)
        try:
            page = artifact_store.list_for_run(
                connection, run_id, after_artifact_id=after_artifact_id,
                limit=limit)
        except artifact_store.ArtifactError as error:
            code = str(error)
            if code not in {"invalid_cursor"}:
                code = "invalid_cursor"
            raise EventJournalError(code) from error
        connection.execute("COMMIT")
        return page
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def artifact_content(connection: sqlite3.Connection, artifact_id: str, *,
                     offset: int = 0,
                     limit: int = artifact_store.ARTIFACT_CHUNK_BYTES
                     ) -> dict[str, Any]:
    """Read one bounded chunk of an artifact by its opaque id."""
    connection.execute("BEGIN")
    try:
        try:
            result = artifact_store.read_content(
                connection, artifact_id, offset=offset, limit=limit)
        except artifact_store.ArtifactError as error:
            code = str(error)
            if code not in {"artifact_not_found", "artifact_unavailable",
                            "invalid_range", "invalid_artifact_id"}:
                code = "artifact_unavailable"
            raise EventJournalError(code) from error
        connection.execute("COMMIT")
        return result
    except BaseException:
        connection.execute("ROLLBACK")
        raise
