"""Transactional event and snapshot slice for the planned Host protocol.

This module has no Codex or HTTP adapter. Callers supply already validated,
normalized events and the resulting snapshot in one append operation.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Callable


MAX_EVENT_BYTES = 64 * 1024
MAX_PAGE_BYTES = 256 * 1024
MAX_PAGE_ITEMS = 100
MAX_SEQ = 2**63 - 1
MAX_INPUT_BYTES = 16 * 1024
MAX_THREAD_ID_BYTES = 256
# Leave room for the response envelope, list separators, and cursor fields.
PAGE_EVENT_BUDGET = MAX_PAGE_BYTES - 2048


class EventJournalError(ValueError):
    """Stable local rejection; no database internals or event contents."""


def _json_bytes(value: dict[str, Any]) -> bytes:
    if not isinstance(value, dict):
        raise EventJournalError("invalid_object")
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EventJournalError("invalid_json") from exc


class EventJournal:
    def __init__(self, path: Path, *,
                 write_gate: Callable[[], bool] | None = None):
        self._write_gate = write_gate
        self.connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
                raise EventJournalError("unsupported_schema")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    seq INTEGER NOT NULL CHECK (seq > 0),
                    event_json TEXT NOT NULL,
                    event_bytes INTEGER NOT NULL CHECK (event_bytes > 0),
                    PRIMARY KEY (session_id, seq)
                );
            """)
            if "session_version" not in {
                    row["name"] for row in self.connection.execute("PRAGMA table_info(sessions)")}:
                self.connection.execute(
                    "ALTER TABLE sessions ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version > 0),
                    terminal_event_seq INTEGER
                );
                CREATE TABLE IF NOT EXISTS active_slot (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS run_operations (
                    actor_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    request_digest BLOB NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    accepted_event_seq INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('accepted', 'dispatching', 'confirmed', 'failed', 'unknown')),
                    outcome TEXT,
                    PRIMARY KEY (actor_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS approval_operations (
                    actor_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    request_digest BLOB NOT NULL,
                    approval_id TEXT NOT NULL UNIQUE,
                    accepted_event_seq INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('accepted', 'dispatching', 'confirmed', 'failed', 'unknown')),
                    outcome TEXT,
                    PRIMARY KEY (actor_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS interrupt_operations (
                    actor_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    request_digest BLOB NOT NULL,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                    accepted_event_seq INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('accepted', 'dispatching', 'confirmed', 'failed', 'unknown')),
                    outcome TEXT,
                    dispatch_epoch INTEGER,
                    PRIMARY KEY (actor_id, request_id)
                );
            """)
            operation_columns = {row["name"] for row in self.connection.execute(
                "PRAGMA table_info(run_operations)")}
            if "dispatch_epoch" not in operation_columns:
                self.connection.execute(
                    "ALTER TABLE run_operations ADD COLUMN dispatch_epoch INTEGER")
            if "outcome" not in operation_columns:
                self.connection.execute(
                    "ALTER TABLE run_operations ADD COLUMN outcome TEXT")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS upstream_turns (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    process_epoch INTEGER NOT NULL CHECK (process_epoch > 0),
                    thread_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    started_event_seq INTEGER NOT NULL,
                    UNIQUE(process_epoch, thread_id, turn_id)
                );
                CREATE TABLE IF NOT EXISTS session_threads (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id)
                        ON DELETE CASCADE,
                    thread_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS thread_starts (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id)
                        ON DELETE CASCADE,
                    actor_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    request_digest BLOB NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('accepted', 'dispatching', 'confirmed', 'unknown')),
                    dispatch_epoch INTEGER,
                    thread_id TEXT,
                    UNIQUE(actor_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    root TEXT NOT NULL UNIQUE,
                    sandbox TEXT NOT NULL CHECK (sandbox IN
                        ('read-only', 'workspace-write'))
                );
                CREATE TABLE IF NOT EXISTS session_workspaces (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id)
                        ON DELETE CASCADE,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id)
                );
            """)
            if version < 10:
                self.connection.execute("PRAGMA user_version=10")
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def _require_write_ready(self) -> None:
        if self._write_gate is not None and not self._write_gate():
            raise EventJournalError("host_not_recovered")

    def __enter__(self) -> EventJournal:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def create_session(self, session_id: str, snapshot: dict[str, Any]) -> None:
        self._require_write_ready()
        if not session_id or not isinstance(session_id, str):
            raise EventJournalError("invalid_session_id")
        data = _json_bytes(snapshot)
        if len(data) > MAX_EVENT_BYTES:
            raise EventJournalError("snapshot_too_large")
        self.connection.execute(
            "INSERT INTO sessions(session_id, snapshot_json) VALUES (?, ?)",
            (session_id, data.decode("utf-8")))

    def bind_session_thread(self, session_id: str, thread_id: str) -> str:
        """Keep one immutable upstream thread identity per Host session."""
        self._require_write_ready()
        if (not isinstance(session_id, str) or not session_id
                or not isinstance(thread_id, str) or not thread_id):
            raise EventJournalError("invalid_session_thread")
        try:
            if len(thread_id.encode("utf-8")) > MAX_THREAD_ID_BYTES:
                raise EventJournalError("invalid_session_thread")
        except UnicodeError as error:
            raise EventJournalError("invalid_session_thread") from error
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id=?",
                    (session_id,)).fetchone() is None:
                raise EventJournalError("session_not_found")
            creation = self.connection.execute(
                "SELECT status, thread_id FROM thread_starts WHERE session_id=?",
                (session_id,)).fetchone()
            if creation is not None and (creation["status"] != "confirmed"
                                         or creation["thread_id"] != thread_id):
                raise EventJournalError("session_thread_conflict")
            prior = self.connection.execute(
                "SELECT thread_id FROM session_threads WHERE session_id=?",
                (session_id,)).fetchone()
            if prior is not None:
                if prior["thread_id"] != thread_id:
                    raise EventJournalError("session_thread_conflict")
            elif self.connection.execute(
                    "SELECT 1 FROM session_threads WHERE thread_id=?",
                    (thread_id,)).fetchone():
                raise EventJournalError("session_thread_conflict")
            else:
                historic = self.connection.execute(
                    "SELECT DISTINCT u.thread_id FROM upstream_turns AS u "
                    "JOIN runs AS r ON r.run_id=u.run_id WHERE r.session_id=?",
                    (session_id,)).fetchall()
                if any(row["thread_id"] != thread_id for row in historic):
                    raise EventJournalError("session_thread_conflict")
                self.connection.execute(
                    "INSERT INTO session_threads(session_id, thread_id) VALUES (?, ?)",
                    (session_id, thread_id))
            self.connection.execute("COMMIT")
            return thread_id
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def get_session_thread(self, session_id: str) -> str:
        if not isinstance(session_id, str) or not session_id:
            raise EventJournalError("invalid_session_id")
        row = self.connection.execute(
            "SELECT thread_id FROM session_threads WHERE session_id=?",
            (session_id,)).fetchone()
        if row is None:
            raise EventJournalError("session_thread_not_bound")
        return row["thread_id"]

    def append(self, session_id: str, *, run_id: str | None, kind: str,
               payload: dict[str, Any], recorded_at: str,
               snapshot: dict[str, Any]) -> dict[str, Any]:
        self._require_write_ready()
        if not session_id or not kind or not recorded_at:
            raise EventJournalError("invalid_event")
        if kind in {"run.created", "run.started", "run.completed", "run.failed",
                    "run.interrupted"}:
            raise EventJournalError("reserved_event_type")
        snapshot_bytes = _json_bytes(snapshot)
        if len(snapshot_bytes) > MAX_EVENT_BYTES:
            raise EventJournalError("snapshot_too_large")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            event = self._append_in_tx(
                session_id, run_id, kind, payload, recorded_at, snapshot_bytes)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return event

    def _append_in_tx(self, session_id: str, run_id: str | None, kind: str,
                      payload: dict[str, Any], recorded_at: str,
                      snapshot_bytes: bytes) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT last_seq FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise EventJournalError("session_not_found")
        if row["last_seq"] >= MAX_SEQ:
            raise EventJournalError("sequence_exhausted")
        seq = row["last_seq"] + 1
        event = {"schema_version": 1, "session_id": session_id,
                 "seq": seq, "run_id": run_id, "type": kind,
                 "recorded_at": recorded_at, "payload": payload}
        event_bytes = _json_bytes(event)
        if len(event_bytes) > MAX_EVENT_BYTES:
            raise EventJournalError("event_too_large")
        self.connection.execute(
            "INSERT INTO events(session_id, seq, event_json, event_bytes) "
            "VALUES (?, ?, ?, ?)",
            (session_id, seq, event_bytes.decode("utf-8"), len(event_bytes)))
        self.connection.execute(
            "UPDATE sessions SET last_seq=?, snapshot_json=? WHERE session_id=?",
            (seq, snapshot_bytes.decode("utf-8"), session_id))
        return event

    def reserve_run(self, session_id: str, run_id: str, *,
                    expected_session_version: int, recorded_at: str) -> dict[str, Any]:
        """Trusted local probe; product callers must use reserve_run_request."""
        self._require_write_ready()
        if (not isinstance(run_id, str) or not run_id
                or type(expected_session_version) is not int
                or not 0 <= expected_session_version < MAX_SEQ or not recorded_at):
            raise EventJournalError("invalid_run")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            event = self._reserve_run_in_tx(
                session_id, run_id, expected_session_version, recorded_at)
            self.connection.execute("COMMIT")
            return event
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _reserve_run_in_tx(self, session_id: str, run_id: str,
                           expected_session_version: int,
                           recorded_at: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT session_version, snapshot_json FROM sessions WHERE session_id=?",
            (session_id,)).fetchone()
        if row is None:
            raise EventJournalError("session_not_found")
        if row["session_version"] != expected_session_version:
            raise EventJournalError("resource_changed")
        if self.connection.execute("SELECT 1 FROM active_slot").fetchone():
            raise EventJournalError("active_run_exists")
        if self.connection.execute(
                "SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
            raise EventJournalError("run_id_exists")
        version = expected_session_version + 1
        snapshot = json.loads(row["snapshot_json"])
        snapshot["active_run"] = {"run_id": run_id, "status": "starting", "version": 1}
        snapshot["session_version"] = version
        snapshot_bytes = _json_bytes(snapshot)
        if len(snapshot_bytes) > MAX_EVENT_BYTES:
            raise EventJournalError("snapshot_too_large")
        self.connection.execute(
            "INSERT INTO runs(run_id, session_id, status, version) VALUES (?, ?, 'starting', 1)",
            (run_id, session_id))
        self.connection.execute(
            "INSERT INTO active_slot(singleton, run_id) VALUES (1, ?)", (run_id,))
        event = self._append_in_tx(session_id, run_id, "run.created",
                                   {"run_id": run_id, "status": "starting"},
                                   recorded_at, snapshot_bytes)
        self.connection.execute(
            "UPDATE sessions SET session_version=? WHERE session_id=?", (version, session_id))
        return event

    def reserve_run_request(self, session_id: str, run_id: str, *, actor_id: str,
                            request_id: str, expected_session_version: int,
                            input_text: str, recorded_at: str) -> dict[str, Any]:
        """Accept a run request once; a lost response replays its original record."""
        self._require_write_ready()
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or not isinstance(session_id, str) or not session_id
                or not isinstance(run_id, str) or not run_id
                or type(expected_session_version) is not int
                or not 0 <= expected_session_version < MAX_SEQ
                or not isinstance(input_text, str) or not input_text
                or not recorded_at):
            raise EventJournalError("invalid_run_request")
        input_bytes = input_text.encode("utf-8")
        if len(input_bytes) > MAX_INPUT_BYTES:
            raise EventJournalError("input_too_large")
        request_body = {"request_id": request_id,
                        "expected_session_version": expected_session_version,
                        "input": input_text}
        digest = hashlib.sha256(_json_bytes({
            "actor_id": actor_id, "method": "POST",
            "path_template": "/v1/sessions/{session_id}/runs",
            "session_id": session_id,
            "protocol": 1, "body": request_body,
        })).digest()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self.connection.execute(
                    "SELECT 1 FROM approval_operations WHERE actor_id=? AND request_id=?",
                    (actor_id, request_id)).fetchone():
                raise EventJournalError("idempotency_conflict")
            if self.connection.execute(
                    "SELECT 1 FROM interrupt_operations WHERE actor_id=? AND request_id=?",
                    (actor_id, request_id)).fetchone():
                raise EventJournalError("idempotency_conflict")
            prior = self.connection.execute(
                "SELECT request_digest, run_id, accepted_event_seq, status, outcome "
                "FROM run_operations WHERE actor_id=? AND request_id=?",
                (actor_id, request_id)).fetchone()
            if prior is not None:
                if prior["request_digest"] != digest:
                    raise EventJournalError("idempotency_conflict")
                result = {"request_id": request_id, "status": prior["status"],
                          "resource_id": prior["run_id"],
                          "accepted_event_seq": prior["accepted_event_seq"]}
                if prior["outcome"] is not None:
                    result["outcome"] = prior["outcome"]
            else:
                event = self._reserve_run_in_tx(
                    session_id, run_id, expected_session_version, recorded_at)
                self.connection.execute(
                    "INSERT INTO run_operations(actor_id, request_id, request_digest, "
                    "run_id, accepted_event_seq, status) VALUES (?, ?, ?, ?, ?, 'accepted')",
                    (actor_id, request_id, digest, run_id, event["seq"]))
                result = {"request_id": request_id, "status": "accepted",
                          "resource_id": run_id, "accepted_event_seq": event["seq"]}
            self.connection.execute("COMMIT")
            return result
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def get_run_operation(self, actor_id: str, request_id: str) -> dict[str, Any]:
        if not actor_id or not request_id:
            raise EventJournalError("invalid_operation_key")
        row = self.connection.execute(
            "SELECT run_id, accepted_event_seq, status, outcome FROM run_operations "
            "WHERE actor_id=? AND request_id=?", (actor_id, request_id)).fetchone()
        if row is None:
            raise EventJournalError("operation_not_found")
        result = {"request_id": request_id, "status": row["status"],
                  "resource_id": row["run_id"],
                  "accepted_event_seq": row["accepted_event_seq"]}
        if row["outcome"] is not None:
            result["outcome"] = row["outcome"]
        return result

    def reserve_interrupt_request(
            self, run_id: str, *, actor_id: str, request_id: str,
            expected_version: int, recorded_at: str) -> dict[str, Any]:
        """Persist an interrupt intent and interrupting snapshot atomically."""
        self._require_write_ready()
        if (not isinstance(run_id, str) or not run_id
                or not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or type(expected_version) is not int
                or not 1 <= expected_version < MAX_SEQ
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_interrupt_request")
        digest = hashlib.sha256(_json_bytes({
            "protocol": 1, "actor_id": actor_id, "method": "POST",
            "path_template": "/v1/runs/{run_id}/interrupt", "run_id": run_id,
            "body": {"request_id": request_id,
                     "expected_version": expected_version},
        })).digest()
        db = self.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if any(db.execute(
                    f"SELECT 1 FROM {table} WHERE actor_id=? AND request_id=?",
                    (actor_id, request_id)).fetchone()
                   for table in ("run_operations", "approval_operations")):
                raise EventJournalError("idempotency_conflict")
            prior = db.execute(
                "SELECT * FROM interrupt_operations WHERE actor_id=? AND request_id=?",
                (actor_id, request_id)).fetchone()
            if prior is not None:
                if prior["request_digest"] != digest:
                    raise EventJournalError("idempotency_conflict")
                result = self._interrupt_operation_result(prior)
            else:
                row = db.execute(
                    "SELECT r.session_id, r.status, r.version, "
                    "s.session_version, s.snapshot_json FROM runs r "
                    "JOIN sessions s ON s.session_id=r.session_id "
                    "WHERE r.run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise EventJournalError("run_not_found")
                existing = db.execute(
                    "SELECT * FROM interrupt_operations WHERE run_id=?",
                    (run_id,)).fetchone()
                if existing is not None:
                    if existing["actor_id"] != actor_id:
                        raise EventJournalError("interrupt_conflict")
                    result = self._interrupt_operation_result(existing)
                elif row["status"] in {"completed", "failed", "interrupted"}:
                    result = {"status": "terminal", "resource_id": run_id,
                              "outcome": row["status"]}
                else:
                    if row["version"] != expected_version:
                        raise EventJournalError("resource_changed")
                    starting = row["status"] == "starting"
                    if row["status"] not in {
                            "starting", "running", "waiting_approval"}:
                        raise EventJournalError("turn_not_bound")
                    slot = db.execute(
                        "SELECT run_id FROM active_slot WHERE singleton=1"
                    ).fetchone()
                    binding = db.execute(
                        "SELECT 1 FROM upstream_turns WHERE run_id=?",
                        (run_id,)).fetchone()
                    if slot is None or slot["run_id"] != run_id:
                        raise EventJournalError("active_slot_mismatch")
                    if starting:
                        start_operation = db.execute(
                            "SELECT status, dispatch_epoch FROM run_operations "
                            "WHERE run_id=?", (run_id,)).fetchone()
                        if binding is not None or start_operation is None:
                            raise EventJournalError("turn_not_bound")
                        if (start_operation["status"] == "accepted"
                                and start_operation["dispatch_epoch"] is None):
                            result = self._cancel_unstarted_run_in_tx(
                                run_id, row, actor_id=actor_id,
                                request_id=request_id, digest=digest,
                                recorded_at=recorded_at)
                            db.execute("COMMIT")
                            return result
                        if (start_operation["status"] not in
                                {"dispatching", "unknown"}
                                or start_operation["dispatch_epoch"] is None):
                            raise EventJournalError("turn_not_bound")
                    elif binding is None:
                        raise EventJournalError("active_slot_mismatch")
                    if row["session_version"] >= MAX_SEQ or row["version"] >= MAX_SEQ:
                        raise EventJournalError("version_exhausted")
                    snapshot = json.loads(row["snapshot_json"])
                    active = snapshot.get("active_run")
                    if (not isinstance(active, dict)
                            or active.get("run_id") != run_id
                            or active.get("status") != row["status"]):
                        raise EventJournalError("invalid_snapshot_run")
                    snapshot["active_run"] = {
                        "run_id": run_id, "status": "interrupting",
                        "version": row["version"] + 1}
                    snapshot["session_version"] = row["session_version"] + 1
                    encoded = _json_bytes(snapshot)
                    if len(encoded) > MAX_EVENT_BYTES:
                        raise EventJournalError("snapshot_too_large")
                    event = self._append_in_tx(
                        row["session_id"], run_id, "operation.updated",
                        {"request_id": request_id, "run_id": run_id,
                         "status": "accepted", "action": "interrupt"},
                        recorded_at, encoded)
                    db.execute(
                        "INSERT INTO interrupt_operations(actor_id, request_id, "
                        "request_digest, run_id, accepted_event_seq, status) "
                        "VALUES (?, ?, ?, ?, ?, 'accepted')",
                        (actor_id, request_id, digest, run_id, event["seq"]))
                    db.execute(
                        "UPDATE runs SET status='interrupting', version=version+1 "
                        "WHERE run_id=?", (run_id,))
                    db.execute(
                        "UPDATE sessions SET session_version=session_version+1 "
                        "WHERE session_id=?", (row["session_id"],))
                    result = {"request_id": request_id, "status": "accepted",
                              "resource_id": run_id,
                              "accepted_event_seq": event["seq"]}
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def _cancel_unstarted_run_in_tx(
            self, run_id: str, row, *, actor_id: str, request_id: str,
            digest: bytes, recorded_at: str) -> dict[str, Any]:
        """Cancel a run that has never claimed a turn/start send attempt."""
        db = self.connection
        operation = db.execute(
            "SELECT actor_id, request_id, status, dispatch_epoch "
            "FROM run_operations WHERE run_id=?", (run_id,)).fetchone()
        binding = db.execute(
            "SELECT 1 FROM upstream_turns WHERE run_id=?", (run_id,)).fetchone()
        slot = db.execute(
            "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
        if slot is None or slot["run_id"] != run_id:
            raise EventJournalError("active_slot_mismatch")
        if (operation is None or operation["status"] != "accepted"
                or operation["dispatch_epoch"] is not None or binding is not None):
            raise EventJournalError("turn_not_bound")
        if row["session_version"] > MAX_SEQ - 4 or row["version"] >= MAX_SEQ:
            raise EventJournalError("version_exhausted")
        snapshot = json.loads(row["snapshot_json"])
        active = snapshot.get("active_run")
        if (not isinstance(active, dict) or active.get("run_id") != run_id
                or active.get("status") != "starting"):
            raise EventJournalError("invalid_snapshot_run")

        def append(kind: str, payload: dict, offset: int, *, terminal=False):
            projected = dict(snapshot)
            projected["session_version"] = row["session_version"] + offset
            if terminal:
                projected["active_run"] = None
            encoded = _json_bytes(projected)
            if len(encoded) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            return self._append_in_tx(
                row["session_id"], run_id, kind, payload,
                recorded_at, encoded)

        accepted = append("operation.updated", {
            "request_id": request_id, "run_id": run_id,
            "status": "accepted", "action": "interrupt"}, 1)
        db.execute(
            "INSERT INTO interrupt_operations(actor_id, request_id, "
            "request_digest, run_id, accepted_event_seq, status, outcome) "
            "VALUES (?, ?, ?, ?, ?, 'confirmed', 'run_interrupted')",
            (actor_id, request_id, digest, run_id, accepted["seq"]))
        db.execute(
            "UPDATE run_operations SET status='failed', "
            "outcome='cancelled_before_dispatch' WHERE run_id=?", (run_id,))
        append("operation.updated", {
            "request_id": operation["request_id"], "run_id": run_id,
            "status": "failed", "outcome": "cancelled_before_dispatch"}, 2)
        append("operation.updated", {
            "request_id": request_id, "run_id": run_id,
            "status": "confirmed", "outcome": "run_interrupted",
            "action": "interrupt"}, 3)
        terminal = append("run.interrupted", {
            "run_id": run_id, "status": "interrupted",
            "reason": "cancelled_before_dispatch"}, 4, terminal=True)
        db.execute(
            "UPDATE runs SET status='interrupted', version=version+1, "
            "terminal_event_seq=? WHERE run_id=?", (terminal["seq"], run_id))
        db.execute("DELETE FROM active_slot WHERE singleton=1 AND run_id=?", (run_id,))
        db.execute(
            "UPDATE sessions SET session_version=session_version+4 "
            "WHERE session_id=?", (row["session_id"],))
        return {"request_id": request_id, "status": "confirmed",
                "resource_id": run_id, "accepted_event_seq": accepted["seq"],
                "outcome": "run_interrupted"}

    @staticmethod
    def _interrupt_operation_result(row) -> dict[str, Any]:
        result = {"request_id": row["request_id"], "status": row["status"],
                  "resource_id": row["run_id"],
                  "accepted_event_seq": row["accepted_event_seq"]}
        if row["outcome"] is not None:
            result["outcome"] = row["outcome"]
        return result

    def get_interrupt_operation(self, actor_id: str, request_id: str) -> dict[str, Any]:
        if not actor_id or not request_id:
            raise EventJournalError("invalid_operation_key")
        row = self.connection.execute(
            "SELECT * FROM interrupt_operations WHERE actor_id=? AND request_id=?",
            (actor_id, request_id)).fetchone()
        if row is None:
            raise EventJournalError("operation_not_found")
        return self._interrupt_operation_result(row)

    def claim_interrupt_dispatch(self, actor_id: str, request_id: str, *,
                                 process_epoch: int, recorded_at: str) -> dict[str, Any]:
        return self._advance_interrupt_operation(
            actor_id, request_id, from_status="accepted",
            to_status="dispatching", process_epoch=process_epoch,
            recorded_at=recorded_at)

    def mark_interrupt_dispatch_unknown(self, actor_id: str, request_id: str, *,
                                        process_epoch: int,
                                        recorded_at: str) -> dict[str, Any]:
        return self._advance_interrupt_operation(
            actor_id, request_id, from_status="dispatching",
            to_status="unknown", process_epoch=process_epoch,
            recorded_at=recorded_at)

    def _advance_interrupt_operation(
            self, actor_id: str, request_id: str, *, from_status: str,
            to_status: str, process_epoch: int | None,
            recorded_at: str) -> dict[str, Any]:
        self._require_write_ready()
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or not isinstance(recorded_at, str) or not recorded_at
                or type(process_epoch) is not int or process_epoch < 1):
            raise EventJournalError("invalid_interrupt_dispatch")
        db = self.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT i.*, r.session_id, r.status AS run_status, "
                "s.session_version, s.snapshot_json, u.process_epoch AS turn_epoch "
                "FROM interrupt_operations i JOIN runs r ON r.run_id=i.run_id "
                "JOIN sessions s ON s.session_id=r.session_id "
                "JOIN upstream_turns u ON u.run_id=i.run_id "
                "WHERE i.actor_id=? AND i.request_id=?",
                (actor_id, request_id)).fetchone()
            if row is None:
                raise EventJournalError("operation_not_found")
            if row["status"] != from_status or row["run_status"] != "interrupting":
                raise EventJournalError("dispatch_unavailable")
            if (to_status == "dispatching" and
                    row["turn_epoch"] != process_epoch):
                raise EventJournalError("stale_epoch")
            if (to_status == "unknown" and
                    row["dispatch_epoch"] != process_epoch):
                raise EventJournalError("stale_epoch")
            slot = db.execute(
                "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
            if slot is None or slot["run_id"] != row["run_id"]:
                raise EventJournalError("active_slot_mismatch")
            if row["session_version"] >= MAX_SEQ:
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(row["snapshot_json"])
            snapshot["session_version"] = row["session_version"] + 1
            encoded = _json_bytes(snapshot)
            if len(encoded) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            if to_status == "dispatching":
                db.execute(
                    "UPDATE interrupt_operations SET status=?, dispatch_epoch=? "
                    "WHERE actor_id=? AND request_id=?",
                    (to_status, process_epoch, actor_id, request_id))
            else:
                db.execute(
                    "UPDATE interrupt_operations SET status=?, outcome=? "
                    "WHERE actor_id=? AND request_id=?",
                    (to_status, "dispatch_unconfirmed", actor_id, request_id))
            self._append_in_tx(
                row["session_id"], row["run_id"], "operation.updated",
                {"request_id": request_id, "run_id": row["run_id"],
                 "status": to_status, "action": "interrupt"},
                recorded_at, encoded)
            db.execute(
                "UPDATE sessions SET session_version=session_version+1 "
                "WHERE session_id=?", (row["session_id"],))
            result = {"request_id": request_id, "status": to_status,
                      "resource_id": row["run_id"],
                      "accepted_event_seq": row["accepted_event_seq"]}
            if to_status == "unknown":
                result["outcome"] = "dispatch_unconfirmed"
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def claim_run_dispatch(self, actor_id: str, request_id: str, *,
                           process_epoch: int, recorded_at: str) -> dict[str, Any]:
        """Grant one send attempt after committing dispatching to storage."""
        return self._advance_run_operation(
            actor_id, request_id, from_status="accepted",
            to_status="dispatching", recorded_at=recorded_at,
            process_epoch=process_epoch)

    def mark_run_dispatch_unknown(self, actor_id: str, request_id: str, *,
                                  recorded_at: str) -> dict[str, Any]:
        """Record ambiguous transport outcome without freeing the active run."""
        return self._advance_run_operation(
            actor_id, request_id, from_status="dispatching",
            to_status="unknown", recorded_at=recorded_at)

    def _advance_run_operation(self, actor_id: str, request_id: str, *,
                               from_status: str, to_status: str,
                               recorded_at: str,
                               process_epoch: int | None = None) -> dict[str, Any]:
        self._require_write_ready()
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or not isinstance(recorded_at, str) or not recorded_at
                or (to_status == "dispatching" and
                    (type(process_epoch) is not int or process_epoch < 1))):
            raise EventJournalError("invalid_operation_key")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT o.status, o.run_id, o.accepted_event_seq, r.session_id, "
                "r.status AS run_status, s.session_version, s.snapshot_json "
                "FROM run_operations AS o JOIN runs AS r ON r.run_id=o.run_id "
                "JOIN sessions AS s ON s.session_id=r.session_id "
                "WHERE o.actor_id=? AND o.request_id=?",
                (actor_id, request_id)).fetchone()
            if row is None:
                raise EventJournalError("operation_not_found")
            if row["status"] != from_status:
                raise EventJournalError("dispatch_unavailable")
            if row["run_status"] in {"completed", "failed", "interrupted"}:
                raise EventJournalError("run_terminal")
            slot = self.connection.execute(
                "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
            if slot is None or slot["run_id"] != row["run_id"]:
                raise EventJournalError("active_slot_mismatch")
            if row["session_version"] >= MAX_SEQ:
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(row["snapshot_json"])
            snapshot["session_version"] = row["session_version"] + 1
            snapshot_bytes = _json_bytes(snapshot)
            if len(snapshot_bytes) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            if to_status == "dispatching":
                self.connection.execute(
                    "UPDATE run_operations SET status=?, dispatch_epoch=? "
                    "WHERE actor_id=? AND request_id=?",
                    (to_status, process_epoch, actor_id, request_id))
            else:
                self.connection.execute(
                    "UPDATE run_operations SET status=? WHERE actor_id=? AND request_id=?",
                    (to_status, actor_id, request_id))
            self._append_in_tx(
                row["session_id"], row["run_id"], "operation.updated",
                {"request_id": request_id, "run_id": row["run_id"], "status": to_status},
                recorded_at, snapshot_bytes)
            self.connection.execute(
                "UPDATE sessions SET session_version=session_version+1 WHERE session_id=?",
                (row["session_id"],))
            self.connection.execute("COMMIT")
            return {"request_id": request_id, "status": to_status,
                    "resource_id": row["run_id"],
                    "accepted_event_seq": row["accepted_event_seq"]}
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def confirm_run_started(self, actor_id: str, request_id: str, *,
                            process_epoch: int, thread_id: str, turn_id: str,
                            recorded_at: str) -> dict[str, Any]:
        """Bind a recognized upstream turn to its claimed run exactly once."""
        self._require_write_ready()
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or type(process_epoch) is not int or process_epoch < 1
                or not isinstance(thread_id, str) or not thread_id
                or not isinstance(turn_id, str) or not turn_id
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_turn_evidence")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT o.status, o.dispatch_epoch, o.run_id, o.accepted_event_seq, "
                "r.session_id, r.status AS run_status, r.version AS run_version, "
                "s.session_version, s.snapshot_json "
                "FROM run_operations AS o JOIN runs AS r ON r.run_id=o.run_id "
                "JOIN sessions AS s ON s.session_id=r.session_id "
                "WHERE o.actor_id=? AND o.request_id=?",
                (actor_id, request_id)).fetchone()
            if row is None:
                raise EventJournalError("operation_not_found")
            if row["dispatch_epoch"] != process_epoch:
                raise EventJournalError("stale_epoch")
            session_thread = self.connection.execute(
                "SELECT thread_id FROM session_threads WHERE session_id=?",
                (row["session_id"],)).fetchone()
            if session_thread is not None and session_thread["thread_id"] != thread_id:
                raise EventJournalError("session_thread_conflict")
            binding = self.connection.execute(
                "SELECT process_epoch, thread_id, turn_id FROM upstream_turns "
                "WHERE run_id=?", (row["run_id"],)).fetchone()
            if row["status"] == "confirmed":
                if (binding is None or binding["process_epoch"] != process_epoch
                        or binding["thread_id"] != thread_id
                        or binding["turn_id"] != turn_id):
                    raise EventJournalError("upstream_turn_conflict")
                self.connection.execute("COMMIT")
                return {"request_id": request_id, "status": "confirmed",
                        "resource_id": row["run_id"],
                        "accepted_event_seq": row["accepted_event_seq"],
                        "outcome": "turn_started"}
            if row["status"] not in {"dispatching", "unknown"}:
                raise EventJournalError("confirmation_unavailable")
            if (row["run_status"] not in {"starting", "interrupting"}
                    or binding is not None):
                raise EventJournalError("run_not_starting")
            if row["run_status"] == "interrupting":
                interrupt = self.connection.execute(
                    "SELECT status FROM interrupt_operations WHERE run_id=?",
                    (row["run_id"],)).fetchone()
                if interrupt is None or interrupt["status"] != "accepted":
                    raise EventJournalError("interrupt_state_conflict")
            slot = self.connection.execute(
                "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
            if slot is None or slot["run_id"] != row["run_id"]:
                raise EventJournalError("active_slot_mismatch")
            if self.connection.execute(
                    "SELECT 1 FROM upstream_turns WHERE process_epoch=? "
                    "AND thread_id=? AND turn_id=?",
                    (process_epoch, thread_id, turn_id)).fetchone():
                raise EventJournalError("upstream_turn_conflict")
            if row["run_version"] >= MAX_SEQ or row["session_version"] >= MAX_SEQ:
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(row["snapshot_json"])
            next_status = ("interrupting" if row["run_status"] == "interrupting"
                           else "running")
            snapshot["active_run"] = {"run_id": row["run_id"],
                                      "status": next_status,
                                      "version": row["run_version"] + 1}
            snapshot["session_version"] = row["session_version"] + 1
            snapshot_bytes = _json_bytes(snapshot)
            if len(snapshot_bytes) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            started = self._append_in_tx(
                row["session_id"], row["run_id"], "run.started",
                {"run_id": row["run_id"]}, recorded_at, snapshot_bytes)
            self._append_in_tx(
                row["session_id"], row["run_id"], "operation.updated",
                {"request_id": request_id, "run_id": row["run_id"],
                 "status": "confirmed", "outcome": "turn_started"},
                recorded_at, snapshot_bytes)
            self.connection.execute(
                "INSERT INTO upstream_turns(run_id, process_epoch, thread_id, turn_id, "
                "started_event_seq) VALUES (?, ?, ?, ?, ?)",
                (row["run_id"], process_epoch, thread_id, turn_id, started["seq"]))
            self.connection.execute(
                "UPDATE runs SET status=?, version=version+1 WHERE run_id=?",
                (next_status, row["run_id"]))
            self.connection.execute(
                "UPDATE run_operations SET status='confirmed', outcome='turn_started' "
                "WHERE actor_id=? AND request_id=?", (actor_id, request_id))
            self.connection.execute(
                "UPDATE sessions SET session_version=session_version+1 WHERE session_id=?",
                (row["session_id"],))
            self.connection.execute("COMMIT")
            return {"request_id": request_id, "status": "confirmed",
                    "resource_id": row["run_id"],
                    "accepted_event_seq": row["accepted_event_seq"],
                    "outcome": "turn_started"}
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def finish_run(self, run_id: str, *, expected_version: int,
                   terminal: str, recorded_at: str) -> dict[str, Any]:
        """Record a terminal state, releasing only this run's slot."""
        self._require_write_ready()
        if (terminal not in {"completed", "failed", "interrupted"}
                or type(expected_version) is not int or expected_version < 1
                or not recorded_at):
            raise EventJournalError("invalid_terminal")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            event = self._finish_run_in_tx(
                run_id, expected_version=expected_version,
                terminal=terminal, recorded_at=recorded_at)
            self.connection.execute("COMMIT")
            return event
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _finish_run_in_tx(self, run_id: str, *, expected_version: int,
                          terminal: str, recorded_at: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT run_id, session_id, status, version, terminal_event_seq "
            "FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise EventJournalError("run_not_found")
        if row["status"] in {"completed", "failed", "interrupted"}:
            if row["status"] != terminal:
                raise EventJournalError("terminal_conflict")
            event = self.connection.execute(
                "SELECT event_json FROM events WHERE session_id=? AND seq=?",
                (row["session_id"], row["terminal_event_seq"])).fetchone()
            return json.loads(event["event_json"])
        if row["version"] != expected_version:
            raise EventJournalError("resource_changed")
        slot = self.connection.execute(
            "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
        if slot is None or slot["run_id"] != run_id:
            raise EventJournalError("active_slot_mismatch")
        session = self.connection.execute(
            "SELECT session_version, snapshot_json FROM sessions WHERE session_id=?",
            (row["session_id"],)).fetchone()
        interrupt = self.connection.execute(
            "SELECT actor_id, request_id, status FROM interrupt_operations "
            "WHERE run_id=?", (run_id,)).fetchone()
        settle_interrupt = (interrupt is not None and interrupt["status"] in
                            {"accepted", "dispatching", "unknown"})
        needed_versions = 2 if settle_interrupt else 1
        if (session["session_version"] > MAX_SEQ - needed_versions
                or row["version"] >= MAX_SEQ):
            raise EventJournalError("version_exhausted")
        snapshot = json.loads(session["snapshot_json"])
        if settle_interrupt:
            operation_status = ("failed" if interrupt["status"] == "accepted"
                                else "confirmed")
            outcome = ("terminal_before_dispatch" if operation_status == "failed"
                       else f"run_{terminal}")
            interim = dict(snapshot)
            interim["session_version"] = session["session_version"] + 1
            interim_bytes = _json_bytes(interim)
            if len(interim_bytes) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            self.connection.execute(
                "UPDATE interrupt_operations SET status=?, outcome=? "
                "WHERE actor_id=? AND request_id=?",
                (operation_status, outcome, interrupt["actor_id"],
                 interrupt["request_id"]))
            self._append_in_tx(
                row["session_id"], run_id, "operation.updated",
                {"request_id": interrupt["request_id"], "run_id": run_id,
                 "status": operation_status, "outcome": outcome,
                 "action": "interrupt"}, recorded_at, interim_bytes)
        snapshot["active_run"] = None
        snapshot["session_version"] = (
            session["session_version"] + needed_versions)
        snapshot_bytes = _json_bytes(snapshot)
        if len(snapshot_bytes) > MAX_EVENT_BYTES:
            raise EventJournalError("snapshot_too_large")
        event = self._append_in_tx(row["session_id"], run_id, f"run.{terminal}",
                                   {"run_id": run_id, "status": terminal},
                                   recorded_at, snapshot_bytes)
        self.connection.execute(
            "UPDATE runs SET status=?, version=version+1, terminal_event_seq=? "
            "WHERE run_id=?", (terminal, event["seq"], run_id))
        self.connection.execute(
            "DELETE FROM active_slot WHERE singleton=1 AND run_id=?", (run_id,))
        self.connection.execute(
            "UPDATE sessions SET session_version=session_version+? WHERE session_id=?",
            (needed_versions, row["session_id"]))
        return event

    def recover_run_after_isolation(
            self, run_id: str, *, expected_version: int,
            old_process_epoch: int, old_process_isolated: bool,
            reason: str, recorded_at: str) -> dict[str, Any]:
        """Interrupt one old run only after the caller isolated its process."""
        self._require_write_ready()
        if not old_process_isolated:
            raise EventJournalError("old_process_not_isolated")
        if (not isinstance(run_id, str) or not run_id
                or type(expected_version) is not int or expected_version < 1
                or type(old_process_epoch) is not int or old_process_epoch < 1
                or reason not in {"host_restart", "upstream_exit"}
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_recovery")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            event = self._recover_run_after_isolation_in_tx(
                run_id, expected_version=expected_version,
                old_process_epoch=old_process_epoch, reason=reason,
                recorded_at=recorded_at)
            self.connection.execute("COMMIT")
            return event
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _recover_run_after_isolation_in_tx(
            self, run_id: str, *, expected_version: int,
            old_process_epoch: int, reason: str,
            recorded_at: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT r.session_id, r.status, r.version, r.terminal_event_seq, "
            "s.session_version, s.snapshot_json FROM runs AS r "
            "JOIN sessions AS s ON s.session_id=r.session_id WHERE r.run_id=?",
            (run_id,)).fetchone()
        if row is None:
            raise EventJournalError("run_not_found")
        if row["status"] in {"completed", "failed", "interrupted"}:
            if row["status"] != "interrupted":
                raise EventJournalError("recovery_unavailable")
            existing = self.connection.execute(
                "SELECT event_json FROM events WHERE session_id=? AND seq=?",
                (row["session_id"], row["terminal_event_seq"])).fetchone()
            event = json.loads(existing["event_json"])
            if event["payload"].get("reason") != reason:
                raise EventJournalError("recovery_conflict")
            return event
        if row["version"] != expected_version:
            raise EventJournalError("resource_changed")
        slot = self.connection.execute(
            "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
        if slot is None or slot["run_id"] != run_id:
            raise EventJournalError("active_slot_mismatch")
        operation = self.connection.execute(
            "SELECT actor_id, request_id, status, dispatch_epoch "
            "FROM run_operations WHERE run_id=?", (run_id,)).fetchone()
        if (operation is not None and operation["dispatch_epoch"] is not None
                and operation["dispatch_epoch"] != old_process_epoch):
            raise EventJournalError("stale_epoch")
        interrupt = self.connection.execute(
            "SELECT actor_id, request_id, status, dispatch_epoch "
            "FROM interrupt_operations WHERE run_id=?", (run_id,)).fetchone()
        if (interrupt is not None and interrupt["dispatch_epoch"] is not None
                and interrupt["dispatch_epoch"] != old_process_epoch):
            raise EventJournalError("stale_epoch")
        operation_status = None
        operation_outcome = None
        if operation is not None:
            if operation["status"] == "accepted":
                operation_status = "failed"
                operation_outcome = "not_dispatched"
            elif operation["status"] == "dispatching":
                operation_status = "unknown"
                operation_outcome = "dispatch_unconfirmed"
        interrupt_status = None
        interrupt_outcome = None
        if interrupt is not None:
            if interrupt["status"] == "accepted":
                interrupt_status = "failed"
                interrupt_outcome = "not_dispatched"
            elif interrupt["status"] == "dispatching":
                interrupt_status = "unknown"
                interrupt_outcome = "dispatch_unconfirmed"
        needed_versions = (1 + int(operation_status is not None)
                           + int(interrupt_status is not None))
        if (row["session_version"] > MAX_SEQ - needed_versions
                or row["version"] >= MAX_SEQ):
            raise EventJournalError("version_exhausted")
        snapshot = json.loads(row["snapshot_json"])
        offset = 0
        if operation_status is not None:
            interim = dict(snapshot)
            offset += 1
            interim["session_version"] = row["session_version"] + offset
            interim_bytes = _json_bytes(interim)
            if len(interim_bytes) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            self.connection.execute(
                "UPDATE run_operations SET status=?, outcome=? "
                "WHERE actor_id=? AND request_id=?",
                (operation_status, operation_outcome,
                 operation["actor_id"], operation["request_id"]))
            self._append_in_tx(
                row["session_id"], run_id, "operation.updated",
                {"request_id": operation["request_id"], "run_id": run_id,
                 "status": operation_status, "outcome": operation_outcome},
                recorded_at, interim_bytes)
        if interrupt_status is not None:
            interim = dict(snapshot)
            offset += 1
            interim["session_version"] = row["session_version"] + offset
            interim_bytes = _json_bytes(interim)
            if len(interim_bytes) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            self.connection.execute(
                "UPDATE interrupt_operations SET status=?, outcome=? "
                "WHERE actor_id=? AND request_id=?",
                (interrupt_status, interrupt_outcome,
                 interrupt["actor_id"], interrupt["request_id"]))
            self._append_in_tx(
                row["session_id"], run_id, "operation.updated",
                {"request_id": interrupt["request_id"], "run_id": run_id,
                 "status": interrupt_status, "outcome": interrupt_outcome,
                 "action": "interrupt"}, recorded_at, interim_bytes)
        snapshot["active_run"] = None
        snapshot["session_version"] = row["session_version"] + needed_versions
        snapshot_bytes = _json_bytes(snapshot)
        if len(snapshot_bytes) > MAX_EVENT_BYTES:
            raise EventJournalError("snapshot_too_large")
        event = self._append_in_tx(
            row["session_id"], run_id, "run.interrupted",
            {"run_id": run_id, "status": "interrupted", "reason": reason},
            recorded_at, snapshot_bytes)
        self.connection.execute(
            "UPDATE runs SET status='interrupted', version=version+1, "
            "terminal_event_seq=? WHERE run_id=?", (event["seq"], run_id))
        self.connection.execute(
            "DELETE FROM active_slot WHERE singleton=1 AND run_id=?", (run_id,))
        self.connection.execute(
            "UPDATE sessions SET session_version=session_version+? "
            "WHERE session_id=?", (needed_versions, row["session_id"]))
        return event

    def snapshot(self, session_id: str) -> dict[str, Any]:
        self.connection.execute("BEGIN")
        try:
            row = self.connection.execute(
                "SELECT last_seq, snapshot_json FROM sessions WHERE session_id=?",
                (session_id,)).fetchone()
            if row is None:
                raise EventJournalError("session_not_found")
            result = json.loads(row["snapshot_json"])
            result["snapshot_seq"] = row["last_seq"]
            self.connection.execute("COMMIT")
            return result
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def snapshot_tail(self, session_id: str, *, limit: int = 50) -> dict[str, Any]:
        """Read a snapshot and a bounded newest-event tail in one read view."""
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_ITEMS:
            raise EventJournalError("invalid_cursor")
        db = self.connection
        db.execute("BEGIN")
        try:
            row = db.execute(
                "SELECT last_seq, snapshot_json FROM sessions WHERE session_id=?",
                (session_id,)).fetchone()
            if row is None:
                raise EventJournalError("session_not_found")
            result = json.loads(row["snapshot_json"])
            result["session_id"] = session_id
            result["snapshot_seq"] = row["last_seq"]
            result["recent_events"] = []
            result["history_before_seq"] = row["last_seq"] + 1
            budget = MAX_PAGE_BYTES - len(_json_bytes(result)) - 2048
            if budget < 0:
                raise EventJournalError("snapshot_too_large")
            rows = db.execute(
                "SELECT seq, event_json, event_bytes FROM events WHERE session_id=? "
                "AND seq<=? ORDER BY seq DESC LIMIT ?",
                (session_id, row["last_seq"], limit)).fetchall()
            selected: list[dict[str, Any]] = []
            used = 0
            for item in rows:
                if used + item["event_bytes"] > budget:
                    break
                selected.append(json.loads(item["event_json"]))
                used += item["event_bytes"]
            while True:
                result["recent_events"] = list(reversed(selected))
                result["history_before_seq"] = (
                    selected[-1]["seq"] if selected else row["last_seq"] + 1)
                if len(_json_bytes(result)) <= MAX_PAGE_BYTES:
                    break
                if not selected:
                    raise EventJournalError("snapshot_too_large")
                selected.pop()
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def page(self, session_id: str, *, after_seq: int | None = None,
             before_seq: int | None = None, limit: int = 50) -> dict[str, Any]:
        if after_seq is not None and before_seq is not None:
            raise EventJournalError("invalid_cursor")
        if (type(limit) is not int or not 1 <= limit <= MAX_PAGE_ITEMS
                or (after_seq is not None and (type(after_seq) is not int or after_seq < 0))
                or (before_seq is not None and (type(before_seq) is not int or before_seq < 1))):
            raise EventJournalError("invalid_cursor")
        self.connection.execute("BEGIN")
        try:
            row = self.connection.execute(
                "SELECT last_seq FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise EventJournalError("session_not_found")
            latest = row["last_seq"]
            if after_seq is not None and after_seq > latest:
                raise EventJournalError("cursor_ahead")
            if before_seq is not None and before_seq > latest + 1:
                raise EventJournalError("cursor_ahead")
            if before_seq is None:
                boundary = after_seq or 0
                rows = self.connection.execute(
                    "SELECT seq, event_json, event_bytes FROM events "
                    "WHERE session_id=? AND seq>? ORDER BY seq ASC LIMIT ?",
                    (session_id, boundary, limit + 1)).fetchall()
            else:
                boundary = before_seq
                rows = self.connection.execute(
                    "SELECT seq, event_json, event_bytes FROM events "
                    "WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                    (session_id, boundary, limit + 1)).fetchall()
            selected: list[sqlite3.Row] = []
            total = 0
            for item in rows[:limit]:
                if selected and total + item["event_bytes"] > PAGE_EVENT_BUDGET:
                    break
                selected.append(item)
                total += item["event_bytes"]
            if before_seq is not None:
                selected.reverse()
            events = [json.loads(item["event_json"]) for item in selected]
            has_more = len(rows) > len(selected)
            result = {"events": events, "latest_seq": latest, "has_more": has_more,
                      "next_after_seq": (selected[-1]["seq"] if before_seq is None and selected else boundary)
                      if before_seq is None else None,
                      "next_before_seq": (selected[0]["seq"] if before_seq is not None and selected else boundary)
                      if before_seq is not None else None}
            self.connection.execute("COMMIT")
            return result
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
