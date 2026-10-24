"""Standalone SQLite proof of one-shot approval dispatch and crash recovery.

This is not the Host service schema or an app-server dispatcher. Callers must
isolate the old process before advancing an epoch and must never retry a claim.
"""

import sqlite3
from pathlib import Path


class JournalError(Exception):
    """A stable local journal rejection code."""


class ApprovalJournal:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS journal_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                epoch INTEGER NOT NULL CHECK (epoch >= 0)
            );
            INSERT OR IGNORE INTO journal_meta (singleton, epoch) VALUES (1, 0);
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                epoch INTEGER NOT NULL,
                upstream_request_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('command', 'file_change')),
                status TEXT NOT NULL CHECK (status IN
                    ('pending', 'decision_recorded', 'dispatching', 'resolved',
                     'expired', 'unknown')),
                decision TEXT CHECK (decision IN ('accept', 'decline')),
                decision_request_id TEXT UNIQUE,
                version INTEGER NOT NULL CHECK (version >= 1),
                resolution_kind TEXT,
                file_snapshot TEXT CHECK (file_snapshot IN
                    ('absent', 'matches', 'different', 'unsafe_path')),
                UNIQUE (epoch, upstream_request_id)
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def _epoch(self) -> int:
        return self.connection.execute(
            "SELECT epoch FROM journal_meta WHERE singleton = 1").fetchone()[0]

    def current_epoch(self) -> int:
        return self._epoch()

    def _row(self, approval_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            raise JournalError("approval_not_found")
        return row

    def get(self, approval_id: str) -> dict:
        return dict(self._row(approval_id))

    def start_epoch_after_isolation(self, *, old_process_isolated: bool) -> int:
        if not old_process_isolated:
            raise JournalError("old_process_not_isolated")
        self._begin()
        try:
            epoch = self._epoch() + 1
            self.connection.execute(
                "UPDATE approvals SET status = 'unknown', version = version + 1 "
                "WHERE status = 'dispatching'")
            self.connection.execute(
                "UPDATE approvals SET status = 'expired', version = version + 1 "
                "WHERE status IN ('pending', 'decision_recorded')")
            self.connection.execute(
                "UPDATE journal_meta SET epoch = ? WHERE singleton = 1", (epoch,))
            self.connection.execute("COMMIT")
            return epoch
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def record_request(self, *, approval_id: str, epoch: int,
                       upstream_request_id: str, kind: str) -> dict:
        if not approval_id or not upstream_request_id or kind not in ("command", "file_change"):
            raise JournalError("invalid_approval_request")
        self._begin()
        try:
            if epoch != self._epoch() or epoch == 0:
                raise JournalError("stale_epoch")
            self.connection.execute(
                "INSERT INTO approvals (approval_id, epoch, upstream_request_id, "
                "kind, status, version) VALUES (?, ?, ?, ?, 'pending', 1)",
                (approval_id, epoch, upstream_request_id, kind))
            self.connection.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self.connection.execute("ROLLBACK")
            raise JournalError("duplicate_approval_request") from error
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)

    def record_decision(self, *, approval_id: str, request_id: str,
                        decision: str, expected_version: int) -> dict:
        if not request_id or decision not in ("accept", "decline"):
            raise JournalError("invalid_decision")
        self._begin()
        try:
            owner = self.connection.execute(
                "SELECT approval_id FROM approvals WHERE decision_request_id = ?",
                (request_id,)).fetchone()
            if owner is not None and owner[0] != approval_id:
                raise JournalError("idempotency_conflict")
            row = self._row(approval_id)
            if row["decision"] is not None:
                if row["decision"] != decision:
                    raise JournalError("decision_conflict")
                self.connection.execute("COMMIT")
                return self.get(approval_id)
            if row["epoch"] != self._epoch() or row["status"] != "pending":
                raise JournalError("approval_expired")
            if row["version"] != expected_version:
                raise JournalError("resource_changed")
            self.connection.execute(
                "UPDATE approvals SET status = 'decision_recorded', decision = ?, "
                "decision_request_id = ?, version = version + 1 WHERE approval_id = ?",
                (decision, request_id, approval_id))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)

    def claim_dispatch(self, *, approval_id: str, epoch: int) -> dict:
        self._begin()
        try:
            row = self._row(approval_id)
            if (epoch != self._epoch() or row["epoch"] != epoch
                    or row["status"] != "decision_recorded"):
                raise JournalError("dispatch_unavailable")
            self.connection.execute(
                "UPDATE approvals SET status = 'dispatching', version = version + 1 "
                "WHERE approval_id = ?", (approval_id,))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)

    def record_request_closed(self, *, approval_id: str, epoch: int) -> dict:
        self._begin()
        try:
            row = self._row(approval_id)
            if row["epoch"] != epoch or epoch != self._epoch():
                raise JournalError("stale_epoch")
            if row["status"] == "resolved" and row["resolution_kind"] == "request_closed":
                self.connection.execute("COMMIT")
                return self.get(approval_id)
            if row["status"] not in ("pending", "decision_recorded", "dispatching"):
                raise JournalError("resolution_unavailable")
            status = "resolved" if row["status"] == "dispatching" else "expired"
            self.connection.execute(
                "UPDATE approvals SET status = ?, resolution_kind = 'request_closed', "
                "version = version + 1 WHERE approval_id = ?", (status, approval_id))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)

    def inspect_unknown_file(self, *, approval_id: str, path: Path,
                             expected_bytes: bytes) -> dict:
        if not isinstance(expected_bytes, bytes) or not expected_bytes:
            raise JournalError("invalid_expected_bytes")
        if path.is_symlink():
            snapshot = "unsafe_path"
        elif not path.exists():
            snapshot = "absent"
        elif not path.is_file():
            snapshot = "unsafe_path"
        elif path.stat().st_size != len(expected_bytes):
            snapshot = "different"
        else:
            snapshot = ("matches" if path.read_bytes() == expected_bytes else "different")
        self._begin()
        try:
            row = self._row(approval_id)
            if row["status"] != "unknown":
                raise JournalError("reconciliation_unavailable")
            self.connection.execute(
                "UPDATE approvals SET file_snapshot = ?, version = version + 1 "
                "WHERE approval_id = ?", (snapshot, approval_id))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return self.get(approval_id)
