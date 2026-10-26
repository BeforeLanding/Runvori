"""M0 startup slice: acquire Host ownership before opening the approval journal.

The journal epoch is intentionally not advanced here. A future Host must first
isolate any old app-server process and then call start_epoch_after_isolation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from runvori.infrastructure.approval_journal import ApprovalJournal
from runvori.infrastructure.event_journal import (
    MAX_EVENT_BYTES, MAX_SEQ, MAX_THREAD_ID_BYTES,
    EventJournal, EventJournalError, _json_bytes,
)
from runvori.infrastructure.instance_lock import InstanceLockUnavailable, WindowsInstanceLock


class LockedApprovalJournal:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.lock: WindowsInstanceLock | None = None
        self.journal: ApprovalJournal | None = None

    def open(self) -> LockedApprovalJournal:
        if self.lock is not None:
            raise InstanceLockUnavailable("locked_journal_already_open")
        if (not self.data_dir.is_absolute() or not self.data_dir.is_dir()
                or self.data_dir.is_symlink()
                or (hasattr(self.data_dir, "is_junction") and self.data_dir.is_junction())):
            raise InstanceLockUnavailable("unsafe_host_data_dir")

        lock = WindowsInstanceLock(self.data_dir / "host.lock").acquire()
        try:
            journal = ApprovalJournal(self.data_dir / "approvals.db")
        except BaseException:
            lock.close()
            raise
        self.lock = lock
        self.journal = journal
        return self

    def close(self) -> None:
        journal, lock = self.journal, self.lock
        self.journal = None
        self.lock = None
        try:
            if journal is not None:
                journal.close()
        finally:
            if lock is not None:
                lock.close()

    def __enter__(self) -> LockedApprovalJournal:
        return self.open()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


class LockedHostJournals:
    """Own approval and event state on one SQLite connection under one lock.

    This does not advance an approval epoch or recover an active run. Legacy
    split probe databases are refused rather than silently ignored.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.lock: WindowsInstanceLock | None = None
        self.approvals: ApprovalJournal | None = None
        self.events: EventJournal | None = None
        self._recovered = False
        self._upstream_owner = None
        self._upstream_owner_lock = threading.Lock()
        self._closing = False

    def open(self) -> LockedHostJournals:
        if self.lock is not None:
            raise InstanceLockUnavailable("locked_journals_already_open")
        if (not self.data_dir.is_absolute() or not self.data_dir.is_dir()
                or self.data_dir.is_symlink()
                or (hasattr(self.data_dir, "is_junction") and self.data_dir.is_junction())):
            raise InstanceLockUnavailable("unsafe_host_data_dir")

        lock = WindowsInstanceLock(self.data_dir / "host.lock").acquire()
        events: EventJournal | None = None
        try:
            if any((self.data_dir / name).exists() or
                   (self.data_dir / name).is_symlink()
                   for name in ("approvals.db", "events.db")):
                raise InstanceLockUnavailable("legacy_split_store_present")
            events = EventJournal(
                self.data_dir / "host.db", write_gate=lambda: self._recovered)
            approvals = ApprovalJournal(connection=events.connection)
        except BaseException:
            try:
                if events is not None:
                    events.close()
            finally:
                lock.close()
            raise
        self.lock = lock
        self.approvals = approvals
        self.events = events
        self._recovered = False
        self._closing = False
        return self

    def close(self) -> None:
        with self._upstream_owner_lock:
            self._closing = True
            owner = self._upstream_owner
        if owner is not None:
            self._recovered = False
            client = owner.client
            if client is None:
                raise InstanceLockUnavailable("upstream_launch_incomplete")
            client.close()
            if (client._job is None or client._job.handle is not None
                    or client.process.poll() is None):
                raise InstanceLockUnavailable("upstream_isolation_unconfirmed")
            with self._upstream_owner_lock:
                self._upstream_owner = None
        events, approvals, lock = self.events, self.approvals, self.lock
        self.events = None
        self.approvals = None
        self.lock = None
        self._recovered = False
        try:
            if approvals is not None:
                approvals.close()
        finally:
            try:
                if events is not None:
                    events.close()
            finally:
                if lock is not None:
                    lock.close()

    def _require_recovered(self) -> None:
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
        if not self._recovered:
            raise EventJournalError("host_not_recovered")

    def current_host_id(self) -> str:
        """Expose the authoritative identity only after startup recovery."""
        self._require_recovered()
        return self.events.get_host_id()

    def reserve_upstream_owner(self, owner, *, epoch: int) -> None:
        """Reserve the sole connection slot before starting a child process."""
        if type(epoch) is not int or epoch < 1:
            raise EventJournalError("invalid_epoch")
        with self._upstream_owner_lock:
            if self._closing:
                raise EventJournalError("host_closing")
            self._require_recovered()
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            if self._upstream_owner is not None:
                raise EventJournalError("upstream_already_owned")
            self._upstream_owner = owner

    def release_upstream_owner(self, owner) -> None:
        with self._upstream_owner_lock:
            if self._upstream_owner is not owner:
                raise EventJournalError("upstream_owner_mismatch")
            self._upstream_owner = None

    def assert_owned_upstream(self, owner, *, epoch: int) -> None:
        with self._upstream_owner_lock:
            self._require_recovered()
            if type(epoch) is not int or epoch < 1:
                raise EventJournalError("invalid_epoch")
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            if self._upstream_owner is not owner:
                raise EventJournalError("upstream_owner_mismatch")

    def freeze_writes_for_upstream_exit(self, owner, *, epoch: int) -> None:
        """Close the shared write gate before isolating this Host's upstream."""
        self._require_recovered()
        if type(epoch) is not int or epoch < 1:
            raise EventJournalError("invalid_epoch")
        if epoch != self.approvals.current_epoch():
            raise EventJournalError("stale_epoch")
        with self._upstream_owner_lock:
            if self._upstream_owner is not owner:
                raise EventJournalError("upstream_owner_mismatch")
        self._recovered = False

    def get_bound_session_thread(self, session_id: str, *, epoch: int) -> str:
        """Expose a thread identity only after this Host owns the current epoch."""
        self._require_recovered()
        if type(epoch) is not int or epoch < 1:
            raise EventJournalError("invalid_epoch")
        if epoch != self.approvals.current_epoch():
            raise EventJournalError("stale_epoch")
        return self.events.get_session_thread(session_id)

    def get_bound_interrupt_turn(self, run_id: str, *, epoch: int) -> dict:
        """Read only the active, bound turn on this Host's current epoch."""
        self._require_recovered()
        if not isinstance(run_id, str) or not run_id:
            raise EventJournalError("invalid_run_id")
        if type(epoch) is not int or epoch < 1:
            raise EventJournalError("invalid_epoch")
        if epoch != self.approvals.current_epoch():
            raise EventJournalError("stale_epoch")
        row = self.events.connection.execute(
            "SELECT u.thread_id, u.turn_id, u.process_epoch "
            "FROM upstream_turns u JOIN runs r ON r.run_id=u.run_id "
            "JOIN active_slot a ON a.run_id=r.run_id AND a.singleton=1 "
            "JOIN session_threads st ON st.session_id=r.session_id "
            "AND st.thread_id=u.thread_id "
            "WHERE u.run_id=? AND r.status='interrupting'",
            (run_id,)).fetchone()
        if row is None or row["process_epoch"] != epoch:
            raise EventJournalError("interrupt_turn_unavailable")
        return {"thread_id": row["thread_id"], "turn_id": row["turn_id"]}

    def register_workspace(self, workspace_id: str, workspace: Path, *,
                           sandbox: str, epoch: int) -> dict:
        """Register an immutable local root and policy under Host ownership."""
        self._require_recovered()
        if (not isinstance(workspace_id, str) or not workspace_id
                or not isinstance(sandbox, str)
                or sandbox not in {"read-only", "workspace-write"}
                or type(epoch) is not int or epoch < 1):
            raise EventJournalError("invalid_workspace_registration")
        try:
            path = Path(workspace)
            if not path.is_absolute() or not path.is_dir():
                raise EventJournalError("invalid_local_workspace")
            for segment in (path, *path.parents):
                if (segment.is_symlink() or (hasattr(segment, "is_junction")
                                             and segment.is_junction())):
                    raise EventJournalError("invalid_local_workspace")
            root = str(path.resolve(strict=True))
        except (TypeError, ValueError, OSError, RuntimeError) as error:
            raise EventJournalError("invalid_local_workspace") from error
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            prior = db.execute("SELECT * FROM workspaces WHERE workspace_id=?",
                               (workspace_id,)).fetchone()
            if prior is None:
                if db.execute("SELECT 1 FROM workspaces WHERE root=?",
                              (root,)).fetchone():
                    raise EventJournalError("workspace_conflict")
                db.execute("INSERT INTO workspaces VALUES (?, ?, ?)",
                           (workspace_id, root, sandbox))
            elif prior["root"] != root or prior["sandbox"] != sandbox:
                raise EventJournalError("workspace_conflict")
            result = {"workspace_id": workspace_id, "root": root,
                      "sandbox": sandbox}
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def create_session_in_workspace(self, session_id: str,
                                    workspace_id: str, snapshot: dict,
                                    *, epoch: int) -> None:
        """Persist local session and its registered workspace together."""
        self._require_recovered()
        if (not isinstance(session_id, str) or not session_id
                or not isinstance(workspace_id, str) or not workspace_id
                or type(epoch) is not int or epoch < 1):
            raise EventJournalError("invalid_session_workspace")
        data = _json_bytes(snapshot)
        if len(data) > MAX_EVENT_BYTES:
            raise EventJournalError("snapshot_too_large")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            if db.execute("SELECT 1 FROM workspaces WHERE workspace_id=?",
                          (workspace_id,)).fetchone() is None:
                raise EventJournalError("workspace_not_registered")
            db.execute("INSERT INTO sessions(session_id, snapshot_json) "
                       "VALUES (?, ?)", (session_id, data.decode("utf-8")))
            db.execute("INSERT INTO session_workspaces VALUES (?, ?)",
                       (session_id, workspace_id))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def get_session_workspace(self, session_id: str, *, epoch: int) -> dict:
        self._require_recovered()
        if type(epoch) is not int or epoch < 1:
            raise EventJournalError("invalid_epoch")
        if epoch != self.approvals.current_epoch():
            raise EventJournalError("stale_epoch")
        row = self.events.connection.execute(
            "SELECT w.workspace_id, w.root, w.sandbox FROM session_workspaces sw "
            "JOIN workspaces w ON w.workspace_id=sw.workspace_id "
            "WHERE sw.session_id=?", (session_id,)).fetchone()
        if row is None:
            raise EventJournalError("session_workspace_not_registered")
        return dict(row)

    def reserve_thread_start(self, *, session_id: str, actor_id: str,
                             request_id: str, request_digest: bytes,
                             epoch: int) -> dict:
        """Persist one immutable create intent before any upstream send."""
        self._require_recovered()
        if (not isinstance(session_id, str) or not session_id
                or not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or type(request_digest) is not bytes or len(request_digest) != 32
                or type(epoch) is not int or epoch < 1):
            raise EventJournalError("invalid_thread_start")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            if db.execute("SELECT 1 FROM sessions WHERE session_id=?",
                          (session_id,)).fetchone() is None:
                raise EventJournalError("session_not_found")
            prior = db.execute(
                "SELECT * FROM thread_starts WHERE session_id=?",
                (session_id,)).fetchone()
            if prior is not None:
                if (prior["actor_id"] != actor_id
                        or prior["request_id"] != request_id
                        or prior["request_digest"] != request_digest):
                    raise EventJournalError("thread_start_conflict")
                bound = db.execute(
                    "SELECT thread_id FROM session_threads WHERE session_id=?",
                    (session_id,)).fetchone()
                if ((prior["status"] == "confirmed"
                     and (bound is None or bound["thread_id"] != prior["thread_id"]))
                        or (prior["status"] != "confirmed" and bound is not None)):
                    raise EventJournalError("invalid_thread_start_state")
                result = dict(prior)
            else:
                if db.execute(
                        "SELECT 1 FROM session_threads WHERE session_id=?",
                        (session_id,)).fetchone():
                    raise EventJournalError("session_thread_already_bound")
                if db.execute(
                        "SELECT 1 FROM runs WHERE session_id=? LIMIT 1",
                        (session_id,)).fetchone():
                    raise EventJournalError("session_has_runs")
                if db.execute(
                        "SELECT 1 FROM thread_starts WHERE actor_id=? "
                        "AND request_id=?", (actor_id, request_id)).fetchone():
                    raise EventJournalError("thread_start_conflict")
                db.execute(
                    "INSERT INTO thread_starts(session_id, actor_id, request_id, "
                    "request_digest, status) VALUES (?, ?, ?, ?, 'accepted')",
                    (session_id, actor_id, request_id, request_digest))
                result = dict(db.execute(
                    "SELECT * FROM thread_starts WHERE session_id=?",
                    (session_id,)).fetchone())
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def claim_thread_start(self, *, session_id: str, actor_id: str,
                           request_id: str, epoch: int) -> dict:
        """Grant exactly one thread/start send opportunity."""
        return self._advance_thread_start(
            session_id, actor_id=actor_id, request_id=request_id,
            epoch=epoch, from_status="accepted", to_status="dispatching")

    def mark_thread_start_unknown(self, *, session_id: str, actor_id: str,
                                  request_id: str, epoch: int) -> dict:
        return self._advance_thread_start(
            session_id, actor_id=actor_id, request_id=request_id,
            epoch=epoch, from_status="dispatching", to_status="unknown")

    def _advance_thread_start(self, session_id: str, *, actor_id: str,
                              request_id: str, epoch: int,
                              from_status: str, to_status: str) -> dict:
        self._require_recovered()
        if (not isinstance(session_id, str) or not session_id
                or not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or type(epoch) is not int or epoch < 1):
            raise EventJournalError("invalid_thread_start")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            row = db.execute(
                "SELECT * FROM thread_starts WHERE session_id=? AND actor_id=? "
                "AND request_id=?", (session_id, actor_id, request_id)).fetchone()
            if row is None:
                raise EventJournalError("thread_start_not_found")
            if (row["status"] != from_status
                    or (to_status == "unknown" and row["dispatch_epoch"] != epoch)):
                raise EventJournalError("thread_start_unavailable")
            if to_status == "dispatching":
                if db.execute(
                        "SELECT 1 FROM session_threads WHERE session_id=?",
                        (session_id,)).fetchone():
                    raise EventJournalError("session_thread_already_bound")
                db.execute(
                    "UPDATE thread_starts SET status='dispatching', dispatch_epoch=? "
                    "WHERE session_id=?", (epoch, session_id))
            else:
                db.execute(
                    "UPDATE thread_starts SET status='unknown' WHERE session_id=?",
                    (session_id,))
            result = dict(db.execute(
                "SELECT * FROM thread_starts WHERE session_id=?",
                (session_id,)).fetchone())
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def confirm_thread_start(self, *, session_id: str, actor_id: str,
                             request_id: str, epoch: int,
                             thread_id: str) -> dict:
        """Bind the returned upstream identity with the create operation."""
        self._require_recovered()
        if (not isinstance(thread_id, str) or not thread_id
                or type(epoch) is not int or epoch < 1):
            raise EventJournalError("invalid_thread_start_confirmation")
        try:
            if len(thread_id.encode("utf-8")) > MAX_THREAD_ID_BYTES:
                raise EventJournalError("invalid_thread_start_confirmation")
        except UnicodeError as error:
            raise EventJournalError("invalid_thread_start_confirmation") from error
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            row = db.execute(
                "SELECT * FROM thread_starts WHERE session_id=? AND actor_id=? "
                "AND request_id=?", (session_id, actor_id, request_id)).fetchone()
            if row is None:
                raise EventJournalError("thread_start_not_found")
            if row["status"] == "confirmed":
                if row["thread_id"] != thread_id:
                    raise EventJournalError("thread_start_conflict")
                bound = db.execute(
                    "SELECT thread_id FROM session_threads WHERE session_id=?",
                    (session_id,)).fetchone()
                if bound is None or bound["thread_id"] != thread_id:
                    raise EventJournalError("invalid_thread_start_state")
                result = dict(row)
            else:
                if (row["status"] not in {"dispatching", "unknown"}
                        or row["dispatch_epoch"] != epoch):
                    raise EventJournalError("thread_start_unavailable")
                if db.execute(
                        "SELECT 1 FROM session_threads WHERE session_id=? "
                        "OR thread_id=?", (session_id, thread_id)).fetchone():
                    raise EventJournalError("session_thread_conflict")
                db.execute(
                    "INSERT INTO session_threads(session_id, thread_id) VALUES (?, ?)",
                    (session_id, thread_id))
                db.execute(
                    "UPDATE thread_starts SET status='confirmed', thread_id=? "
                    "WHERE session_id=?", (thread_id, session_id))
                result = dict(db.execute(
                    "SELECT * FROM thread_starts WHERE session_id=?",
                    (session_id,)).fetchone())
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def record_approval_request(
            self, session_id: str, run_id: str, *, approval_id: str,
            epoch: int, upstream_request_id: str, kind: str,
            recorded_at: str) -> dict:
        """Persist one approval and its normalized event in one transaction."""
        return self._record_approval_request(
            session_id, run_id, approval_id=approval_id, epoch=epoch,
            upstream_request_id=upstream_request_id, kind=kind,
            recorded_at=recorded_at, request_digest=None,
            in_transaction=False)

    def _record_approval_request(
            self, session_id: str, run_id: str, *, approval_id: str,
            epoch: int, upstream_request_id: str, kind: str,
            recorded_at: str, request_digest: bytes | None,
            in_transaction: bool) -> dict:
        self._require_recovered()
        if (not session_id or not run_id or not approval_id
                or type(epoch) is not int or epoch < 1
                or not upstream_request_id or kind not in {"command", "file_change"}
                or (request_digest is not None and
                    (type(request_digest) is not bytes or len(request_digest) != 32))
                or not recorded_at):
            raise EventJournalError("invalid_approval_request")
        db = self.events.connection
        if not in_transaction:
            db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            row = db.execute(
                "SELECT r.session_id, r.status, r.version, s.session_version, "
                "s.snapshot_json FROM runs AS r JOIN sessions AS s "
                "ON s.session_id=r.session_id WHERE r.run_id=?", (run_id,)).fetchone()
            if row is None or row["session_id"] != session_id:
                raise EventJournalError("run_not_found")
            if row["status"] not in {"running", "waiting_approval"}:
                raise EventJournalError("run_not_approvable")
            binding = db.execute(
                "SELECT process_epoch FROM upstream_turns WHERE run_id=?",
                (run_id,)).fetchone()
            if binding is None or binding["process_epoch"] != epoch:
                raise EventJournalError("stale_epoch")
            slot = db.execute("SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
            if slot is None or slot["run_id"] != run_id:
                raise EventJournalError("active_slot_mismatch")
            if row["version"] >= MAX_SEQ or row["session_version"] >= MAX_SEQ:
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(row["snapshot_json"])
            open_ids = snapshot.get("open_approval_ids", [])
            if not isinstance(open_ids, list) or approval_id in open_ids:
                raise EventJournalError("invalid_snapshot_approvals")
            snapshot["open_approval_ids"] = [*open_ids, approval_id]
            snapshot["active_run"] = {"run_id": run_id,
                                      "status": "waiting_approval",
                                      "version": row["version"] + 1}
            snapshot["session_version"] = row["session_version"] + 1
            encoded = _json_bytes(snapshot)
            if len(encoded) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            db.execute(
                "INSERT INTO approvals(approval_id, epoch, upstream_request_id, "
                "kind, status, version, run_id, request_digest) "
                "VALUES (?, ?, ?, ?, 'pending', 1, ?, ?)",
                (approval_id, epoch, upstream_request_id, kind, run_id,
                 request_digest))
            event = self.events._append_in_tx(
                session_id, run_id, "approval.requested",
                {"approval_id": approval_id, "approval_version": 1},
                recorded_at, encoded)
            db.execute(
                "UPDATE runs SET status='waiting_approval', version=version+1 "
                "WHERE run_id=?", (run_id,))
            db.execute(
                "UPDATE sessions SET session_version=session_version+1 "
                "WHERE session_id=?", (session_id,))
            if not in_transaction:
                db.execute("COMMIT")
            return event
        except BaseException:
            if not in_transaction:
                db.execute("ROLLBACK")
            raise

    def record_upstream_approval_request(
            self, *, thread_id: str, turn_id: str, epoch: int,
            upstream_request_id: str, kind: str, approval_id: str,
            request_digest: bytes,
            recorded_at: str) -> dict:
        """Bind one raw upstream request to its persisted run, once."""
        self._require_recovered()
        if (not isinstance(thread_id, str) or not thread_id
                or not isinstance(turn_id, str) or not turn_id
                or type(epoch) is not int or epoch < 1
                or not isinstance(upstream_request_id, str)
                or not upstream_request_id
                or not isinstance(approval_id, str) or not approval_id
                or kind not in {"command", "file_change"}
                or type(request_digest) is not bytes or len(request_digest) != 32
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_upstream_approval_request")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            binding = db.execute(
                "SELECT u.run_id, r.session_id FROM upstream_turns AS u "
                "JOIN runs AS r ON r.run_id=u.run_id "
                "WHERE u.process_epoch=? AND u.thread_id=? AND u.turn_id=?",
                (epoch, thread_id, turn_id)).fetchone()
            if binding is None:
                raise EventJournalError("upstream_turn_not_found")
            prior = db.execute(
                "SELECT * FROM approvals WHERE epoch=? AND upstream_request_id=?",
                (epoch, upstream_request_id)).fetchone()
            if prior is not None:
                if (prior["run_id"] != binding["run_id"] or prior["kind"] != kind
                        or prior["request_digest"] != request_digest):
                    raise EventJournalError("upstream_request_conflict")
                result = dict(prior)
            else:
                self._record_approval_request(
                    binding["session_id"], binding["run_id"],
                    approval_id=approval_id, epoch=epoch,
                    upstream_request_id=upstream_request_id, kind=kind,
                    recorded_at=recorded_at, request_digest=request_digest,
                    in_transaction=True)
                result = self.approvals.get(approval_id)
            db.execute("COMMIT")
            return result
        except sqlite3.IntegrityError as error:
            db.execute("ROLLBACK")
            if getattr(error, "sqlite_errorcode", None) in {
                    sqlite3.SQLITE_CONSTRAINT_UNIQUE,
                    sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY}:
                raise EventJournalError("duplicate_approval_request") from error
            raise
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def record_upstream_turn_terminal(
            self, *, thread_id: str, turn_id: str, epoch: int,
            terminal: str, recorded_at: str) -> dict:
        """Commit an identified upstream terminal and release its active slot."""
        self._require_recovered()
        if (not isinstance(thread_id, str) or not thread_id
                or not isinstance(turn_id, str) or not turn_id
                or type(epoch) is not int or epoch < 1
                or not isinstance(terminal, str)
                or terminal not in {"completed", "failed", "interrupted"}
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_upstream_turn_terminal")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if epoch != self.approvals.current_epoch():
                raise EventJournalError("stale_epoch")
            binding = db.execute(
                "SELECT u.run_id, r.version FROM upstream_turns AS u "
                "JOIN runs AS r ON r.run_id=u.run_id "
                "WHERE u.process_epoch=? AND u.thread_id=? AND u.turn_id=?",
                (epoch, thread_id, turn_id)).fetchone()
            if binding is None:
                raise EventJournalError("upstream_turn_not_found")
            run = db.execute(
                "SELECT status FROM runs WHERE run_id=?",
                (binding["run_id"],)).fetchone()
            if run["status"] not in {"completed", "failed", "interrupted"}:
                self._settle_approvals_at_turn_terminal_in_tx(
                    binding["run_id"], epoch=epoch, recorded_at=recorded_at)
            event = self.events._finish_run_in_tx(
                binding["run_id"], expected_version=binding["version"],
                terminal=terminal, recorded_at=recorded_at)
            db.execute("COMMIT")
            return event
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def _settle_approvals_at_turn_terminal_in_tx(
            self, run_id: str, *, epoch: int, recorded_at: str) -> None:
        """Invalidate unsent decisions and keep sent decisions uncertain."""
        db = self.events.connection
        rows = db.execute(
            "SELECT a.approval_id, a.status, a.version, r.session_id, "
            "o.actor_id, o.request_id, o.status AS operation_status "
            "FROM approvals AS a JOIN runs AS r ON r.run_id=a.run_id "
            "LEFT JOIN approval_operations AS o ON o.approval_id=a.approval_id "
            "WHERE a.run_id=? AND a.epoch=? AND "
            "a.status IN ('pending', 'decision_recorded', 'dispatching') "
            "ORDER BY a.approval_id", (run_id, epoch)).fetchall()
        for row in rows:
            status = row["status"]
            expected_operation = {
                "pending": None, "decision_recorded": "accepted",
                "dispatching": "dispatching",
            }[status]
            if row["operation_status"] != expected_operation:
                raise EventJournalError("invalid_approval_terminal_state")
            new_status = "unknown" if status == "dispatching" else "expired"
            new_operation_status = ("unknown" if status == "dispatching"
                                    else "failed" if status == "decision_recorded"
                                    else None)
            outcome = ("turn_terminal_dispatch_unconfirmed" if status == "dispatching"
                       else "turn_terminal_before_dispatch"
                       if status == "decision_recorded" else None)
            session = db.execute(
                "SELECT session_version, snapshot_json FROM sessions "
                "WHERE session_id=?", (row["session_id"],)).fetchone()
            needed = 2 if new_operation_status is not None else 1
            if (session["session_version"] > MAX_SEQ - needed
                    or row["version"] >= MAX_SEQ):
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(session["snapshot_json"])
            open_ids = snapshot.get("open_approval_ids")
            if not isinstance(open_ids, list) or row["approval_id"] not in open_ids:
                raise EventJournalError("invalid_snapshot_approvals")
            if new_status == "expired":
                snapshot["open_approval_ids"] = [
                    item for item in open_ids if item != row["approval_id"]]
            snapshot["session_version"] = session["session_version"] + 1
            encoded = _json_bytes(snapshot)
            if len(encoded) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            db.execute(
                "UPDATE approvals SET status=?, version=version+1, "
                "resolution_kind=? WHERE approval_id=?",
                (new_status, "turn_terminal" if new_status == "expired" else None,
                 row["approval_id"]))
            self.events._append_in_tx(
                row["session_id"], run_id, "approval.updated",
                {"approval_id": row["approval_id"],
                 "approval_version": row["version"] + 1,
                 "status": new_status, "reason": "turn_terminal"},
                recorded_at, encoded)
            if new_operation_status is not None:
                snapshot["session_version"] += 1
                encoded = _json_bytes(snapshot)
                if len(encoded) > MAX_EVENT_BYTES:
                    raise EventJournalError("snapshot_too_large")
                db.execute(
                    "UPDATE approval_operations SET status=?, outcome=? "
                    "WHERE actor_id=? AND request_id=?",
                    (new_operation_status, outcome,
                     row["actor_id"], row["request_id"]))
                self.events._append_in_tx(
                    row["session_id"], run_id, "operation.updated",
                    {"request_id": row["request_id"],
                     "approval_id": row["approval_id"],
                     "status": new_operation_status, "outcome": outcome},
                    recorded_at, encoded)
            db.execute(
                "UPDATE sessions SET session_version=session_version+? "
                "WHERE session_id=?", (needed, row["session_id"]))

    def record_approval_decision(
            self, approval_id: str, *, actor_id: str, request_id: str,
            expected_version: int, decision: str, recorded_at: str) -> dict:
        """Accept one immutable decision with its operation and events atomically."""
        self._require_recovered()
        if (not isinstance(approval_id, str) or not approval_id
                or not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or type(expected_version) is not int
                or not 1 <= expected_version < MAX_SEQ
                or not isinstance(decision, str)
                or decision not in {"allow_once", "deny"}
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_decision")
        digest = hashlib.sha256(_json_bytes({
            "protocol": 1, "actor_id": actor_id, "method": "POST",
            "path_template": "/v1/approvals/{approval_id}/decision",
            "approval_id": approval_id,
            "body": {"request_id": request_id,
                     "expected_version": expected_version,
                     "decision": decision},
        })).digest()
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute(
                    "SELECT 1 FROM run_operations WHERE actor_id=? AND request_id=?",
                    (actor_id, request_id)).fetchone():
                raise EventJournalError("idempotency_conflict")
            if db.execute(
                "SELECT 1 FROM interrupt_operations WHERE actor_id=? AND request_id=?",
                (actor_id, request_id)).fetchone():
                raise EventJournalError("idempotency_conflict")
            prior = db.execute(
                "SELECT actor_id, request_id, request_digest, approval_id, "
                "accepted_event_seq, status, outcome FROM approval_operations "
                "WHERE actor_id=? AND request_id=?", (actor_id, request_id)).fetchone()
            if prior is not None:
                if prior["request_digest"] != digest:
                    raise EventJournalError("idempotency_conflict")
                result = self._approval_operation_result(prior)
            else:
                row = db.execute(
                    "SELECT a.run_id, a.epoch, a.status, a.version, a.decision, "
                    "r.session_id, r.status AS run_status, s.session_version, "
                    "s.snapshot_json FROM approvals AS a "
                    "JOIN runs AS r ON r.run_id=a.run_id "
                    "JOIN sessions AS s ON s.session_id=r.session_id "
                    "WHERE a.approval_id=?", (approval_id,)).fetchone()
                if row is None:
                    raise EventJournalError("approval_not_found")
                saved_decision = {"accept": "allow_once", "decline": "deny"}.get(
                    row["decision"])
                if saved_decision is not None:
                    if saved_decision != decision:
                        raise EventJournalError("decision_conflict")
                    original = db.execute(
                        "SELECT actor_id, request_id, request_digest, approval_id, "
                        "accepted_event_seq, status, outcome FROM approval_operations "
                        "WHERE approval_id=?", (approval_id,)).fetchone()
                    if original is None:
                        raise EventJournalError("decision_state_untracked")
                    result = self._approval_operation_result(original)
                else:
                    if (row["epoch"] != self.approvals.current_epoch()
                            or row["status"] != "pending"
                            or row["run_status"] != "waiting_approval"):
                        raise EventJournalError("approval_expired")
                    if row["version"] != expected_version:
                        raise EventJournalError("resource_changed")
                    slot = db.execute(
                        "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
                    binding = db.execute(
                        "SELECT process_epoch FROM upstream_turns WHERE run_id=?",
                        (row["run_id"],)).fetchone()
                    if (slot is None or slot["run_id"] != row["run_id"]
                            or binding is None or binding["process_epoch"] != row["epoch"]):
                        raise EventJournalError("approval_expired")
                    if row["session_version"] >= MAX_SEQ:
                        raise EventJournalError("version_exhausted")
                    snapshot = json.loads(row["snapshot_json"])
                    if approval_id not in snapshot.get("open_approval_ids", []):
                        raise EventJournalError("invalid_snapshot_approvals")
                    snapshot["session_version"] = row["session_version"] + 1
                    encoded = _json_bytes(snapshot)
                    if len(encoded) > MAX_EVENT_BYTES:
                        raise EventJournalError("snapshot_too_large")
                    stored_decision = ("accept" if decision == "allow_once"
                                       else "decline")
                    db.execute(
                        "UPDATE approvals SET status='decision_recorded', decision=?, "
                        "version=version+1 WHERE approval_id=?",
                        (stored_decision, approval_id))
                    event = self.events._append_in_tx(
                        row["session_id"], row["run_id"], "approval.updated",
                        {"approval_id": approval_id, "approval_version": row["version"] + 1,
                         "status": "decision_recorded", "decision": decision},
                        recorded_at, encoded)
                    db.execute(
                        "INSERT INTO approval_operations(actor_id, request_id, "
                        "request_digest, approval_id, accepted_event_seq, status) "
                        "VALUES (?, ?, ?, ?, ?, 'accepted')",
                        (actor_id, request_id, digest, approval_id, event["seq"]))
                    self.events._append_in_tx(
                        row["session_id"], row["run_id"], "operation.updated",
                        {"request_id": request_id, "approval_id": approval_id,
                         "status": "accepted"}, recorded_at, encoded)
                    db.execute(
                        "UPDATE sessions SET session_version=session_version+1 "
                        "WHERE session_id=?", (row["session_id"],))
                    result = {"request_id": request_id, "status": "accepted",
                              "resource_id": approval_id,
                              "accepted_event_seq": event["seq"]}
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    @staticmethod
    def _approval_operation_result(row) -> dict:
        result = {"request_id": row["request_id"], "status": row["status"],
                  "resource_id": row["approval_id"],
                  "accepted_event_seq": row["accepted_event_seq"]}
        if row["outcome"] is not None:
            result["outcome"] = row["outcome"]
        return result

    def get_approval_operation(self, actor_id: str, request_id: str) -> dict:
        if self.events is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
        if not actor_id or not request_id:
            raise EventJournalError("invalid_operation_key")
        row = self.events.connection.execute(
            "SELECT request_id, approval_id, accepted_event_seq, status, outcome "
            "FROM approval_operations WHERE actor_id=? AND request_id=?",
            (actor_id, request_id)).fetchone()
        if row is None:
            raise EventJournalError("operation_not_found")
        return self._approval_operation_result(row)

    def claim_approval_dispatch(
            self, actor_id: str, request_id: str, *, epoch: int,
            recorded_at: str) -> dict:
        """Persist a one-shot send claim before replying to the upstream request."""
        return self._advance_approval_dispatch(
            actor_id, request_id, epoch=epoch, recorded_at=recorded_at,
            from_status="accepted", to_status="dispatching")

    def mark_approval_dispatch_unknown(
            self, actor_id: str, request_id: str, *, epoch: int,
            recorded_at: str) -> dict:
        """Keep an ambiguous send from being claimed a second time."""
        return self._advance_approval_dispatch(
            actor_id, request_id, epoch=epoch, recorded_at=recorded_at,
            from_status="dispatching", to_status="unknown")

    def _advance_approval_dispatch(
            self, actor_id: str, request_id: str, *, epoch: int,
            recorded_at: str, from_status: str, to_status: str) -> dict:
        self._require_recovered()
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id
                or type(epoch) is not int or epoch < 1
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_dispatch_claim")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT o.status AS operation_status, o.approval_id, "
                "o.accepted_event_seq, a.status AS approval_status, a.version, "
                "a.epoch, a.run_id, r.session_id, r.status AS run_status, "
                "s.session_version, s.snapshot_json "
                "FROM approval_operations AS o "
                "JOIN approvals AS a ON a.approval_id=o.approval_id "
                "JOIN runs AS r ON r.run_id=a.run_id "
                "JOIN sessions AS s ON s.session_id=r.session_id "
                "WHERE o.actor_id=? AND o.request_id=?",
                (actor_id, request_id)).fetchone()
            if row is None:
                raise EventJournalError("operation_not_found")
            expected_approval = ("decision_recorded" if to_status == "dispatching"
                                 else "dispatching")
            if (row["operation_status"] != from_status
                    or row["approval_status"] != expected_approval):
                raise EventJournalError("dispatch_unavailable")
            if (epoch != self.approvals.current_epoch() or row["epoch"] != epoch
                    or row["run_status"] != "waiting_approval"):
                raise EventJournalError("dispatch_unavailable")
            slot = db.execute(
                "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
            binding = db.execute(
                "SELECT process_epoch FROM upstream_turns WHERE run_id=?",
                (row["run_id"],)).fetchone()
            if (slot is None or slot["run_id"] != row["run_id"]
                    or binding is None or binding["process_epoch"] != epoch):
                raise EventJournalError("dispatch_unavailable")
            if row["session_version"] >= MAX_SEQ or row["version"] >= MAX_SEQ:
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(row["snapshot_json"])
            open_ids = snapshot.get("open_approval_ids")
            if not isinstance(open_ids, list) or row["approval_id"] not in open_ids:
                raise EventJournalError("invalid_snapshot_approvals")
            snapshot["session_version"] = row["session_version"] + 1
            encoded = _json_bytes(snapshot)
            if len(encoded) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            approval_status = ("dispatching" if to_status == "dispatching"
                               else "unknown")
            outcome = (None if to_status == "dispatching"
                       else "dispatch_unconfirmed")
            db.execute(
                "UPDATE approvals SET status=?, version=version+1 WHERE approval_id=?",
                (approval_status, row["approval_id"]))
            db.execute(
                "UPDATE approval_operations SET status=?, outcome=? "
                "WHERE actor_id=? AND request_id=?",
                (to_status, outcome, actor_id, request_id))
            self.events._append_in_tx(
                row["session_id"], row["run_id"], "approval.updated",
                {"approval_id": row["approval_id"],
                 "approval_version": row["version"] + 1,
                 "status": approval_status}, recorded_at, encoded)
            self.events._append_in_tx(
                row["session_id"], row["run_id"], "operation.updated",
                {"request_id": request_id, "approval_id": row["approval_id"],
                 "status": to_status, "outcome": outcome}, recorded_at, encoded)
            db.execute(
                "UPDATE sessions SET session_version=session_version+1 "
                "WHERE session_id=?", (row["session_id"],))
            db.execute("COMMIT")
            result = {"request_id": request_id, "status": to_status,
                      "resource_id": row["approval_id"],
                      "accepted_event_seq": row["accepted_event_seq"]}
            if outcome is not None:
                result["outcome"] = outcome
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def _recover_approvals_after_isolation(
            self, *, old_process_isolated: bool, recorded_at: str,
            in_transaction: bool) -> int:
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
        if not old_process_isolated:
            raise EventJournalError("old_process_not_isolated")
        if not isinstance(recorded_at, str) or not recorded_at:
            raise EventJournalError("invalid_recovery")
        db = self.events.connection
        if not in_transaction:
            db.execute("BEGIN IMMEDIATE")
        try:
            old_epoch = self.approvals.current_epoch()
            if old_epoch >= MAX_SEQ:
                raise EventJournalError("epoch_exhausted")
            rows = db.execute(
                "SELECT a.approval_id, a.epoch, a.status, a.version, a.run_id, "
                "r.session_id, o.actor_id, o.request_id, "
                "o.status AS operation_status "
                "FROM approvals AS a LEFT JOIN runs AS r ON r.run_id=a.run_id "
                "LEFT JOIN approval_operations AS o ON o.approval_id=a.approval_id "
                "WHERE a.status IN ('pending', 'decision_recorded', 'dispatching') "
                "ORDER BY r.session_id, a.approval_id").fetchall()
            for row in rows:
                if row["epoch"] > old_epoch or row["session_id"] is None:
                    raise EventJournalError("invalid_approval_recovery_state")
                status = row["status"]
                operation_status = row["operation_status"]
                if ((status == "pending" and operation_status is not None)
                        or (status == "decision_recorded"
                            and operation_status != "accepted")
                        or (status == "dispatching"
                            and operation_status != "dispatching")):
                    raise EventJournalError("invalid_approval_recovery_state")
                new_status = "unknown" if status == "dispatching" else "expired"
                new_operation_status = ("unknown" if status == "dispatching"
                                        else "failed" if status == "decision_recorded"
                                        else None)
                outcome = ("dispatch_unconfirmed" if status == "dispatching"
                           else "not_dispatched" if status == "decision_recorded"
                           else None)
                session = db.execute(
                    "SELECT session_version, snapshot_json FROM sessions "
                    "WHERE session_id=?", (row["session_id"],)).fetchone()
                needed = 2 if new_operation_status is not None else 1
                if (session["session_version"] > MAX_SEQ - needed
                        or row["version"] >= MAX_SEQ):
                    raise EventJournalError("version_exhausted")
                snapshot = json.loads(session["snapshot_json"])
                open_ids = snapshot.get("open_approval_ids")
                if not isinstance(open_ids, list) or row["approval_id"] not in open_ids:
                    raise EventJournalError("invalid_snapshot_approvals")
                if new_status == "expired":
                    snapshot["open_approval_ids"] = [
                        item for item in open_ids if item != row["approval_id"]]
                snapshot["session_version"] = session["session_version"] + 1
                encoded = _json_bytes(snapshot)
                if len(encoded) > MAX_EVENT_BYTES:
                    raise EventJournalError("snapshot_too_large")
                db.execute(
                    "UPDATE approvals SET status=?, version=version+1 "
                    "WHERE approval_id=?", (new_status, row["approval_id"]))
                self.events._append_in_tx(
                    row["session_id"], row["run_id"], "approval.updated",
                    {"approval_id": row["approval_id"],
                     "approval_version": row["version"] + 1,
                     "status": new_status, "reason": "process_isolated"},
                    recorded_at, encoded)
                if new_operation_status is not None:
                    snapshot["session_version"] += 1
                    encoded = _json_bytes(snapshot)
                    if len(encoded) > MAX_EVENT_BYTES:
                        raise EventJournalError("snapshot_too_large")
                    db.execute(
                        "UPDATE approval_operations SET status=?, outcome=? "
                        "WHERE actor_id=? AND request_id=?",
                        (new_operation_status, outcome,
                         row["actor_id"], row["request_id"]))
                    self.events._append_in_tx(
                        row["session_id"], row["run_id"], "operation.updated",
                        {"request_id": row["request_id"],
                         "approval_id": row["approval_id"],
                         "status": new_operation_status, "outcome": outcome},
                        recorded_at, encoded)
                db.execute(
                    "UPDATE sessions SET session_version=session_version+? "
                    "WHERE session_id=?", (needed, row["session_id"]))
            db.execute(
                "UPDATE journal_meta SET epoch=? WHERE singleton=1",
                (old_epoch + 1,))
            if not in_transaction:
                db.execute("COMMIT")
            return old_epoch + 1
        except BaseException:
            if not in_transaction:
                db.execute("ROLLBACK")
            raise

    def recover_host_after_isolation(
            self, *, old_process_isolated: bool, old_process_epoch: int,
            expected_run_id: str | None, expected_run_version: int | None,
            reason: str, recorded_at: str) -> dict:
        """Recover approvals and the old active run as one startup transaction."""
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
        self._recovered = False
        with self._upstream_owner_lock:
            owner = self._upstream_owner
        if owner is not None:
            client = owner.client
            if (client is None or client._job is None
                    or client._job.handle is not None
                    or client.process.poll() is None):
                raise EventJournalError("upstream_isolation_unconfirmed")
        if old_process_isolated is not True:
            raise EventJournalError("old_process_not_isolated")
        if (type(old_process_epoch) is not int or old_process_epoch < 0
                or (expected_run_id is None) != (expected_run_version is None)
                or (expected_run_id is not None and
                    (not isinstance(expected_run_id, str) or not expected_run_id
                     or type(expected_run_version) is not int
                     or expected_run_version < 1 or old_process_epoch < 1))
                or reason not in {"host_restart", "upstream_exit"}
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_recovery")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            if self.approvals.current_epoch() != old_process_epoch:
                raise EventJournalError("stale_epoch")
            slot = db.execute(
                "SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
            active_run_id = None if slot is None else slot["run_id"]
            if active_run_id != expected_run_id:
                raise EventJournalError("active_slot_mismatch")
            if expected_run_id is not None:
                run = db.execute(
                    "SELECT status, version FROM runs WHERE run_id=?",
                    (expected_run_id,)).fetchone()
                if run is None or run["status"] in {
                        "completed", "failed", "interrupted"}:
                    raise EventJournalError("recovery_unavailable")
                if run["version"] != expected_run_version:
                    raise EventJournalError("resource_changed")
                binding = db.execute(
                    "SELECT process_epoch FROM upstream_turns WHERE run_id=?",
                    (expected_run_id,)).fetchone()
                if binding is not None and binding["process_epoch"] != old_process_epoch:
                    raise EventJournalError("stale_epoch")
            elif db.execute(
                    "SELECT 1 FROM runs WHERE status NOT IN "
                    "('completed', 'failed', 'interrupted') LIMIT 1").fetchone():
                raise EventJournalError("active_slot_mismatch")
            if db.execute(
                    "SELECT 1 FROM thread_starts WHERE status='dispatching' "
                    "AND (dispatch_epoch IS NULL OR dispatch_epoch != ?) LIMIT 1",
                    (old_process_epoch,)).fetchone():
                raise EventJournalError("invalid_thread_start_recovery_state")
            db.execute(
                "UPDATE thread_starts SET status='unknown' "
                "WHERE status='dispatching' AND dispatch_epoch=?",
                (old_process_epoch,))
            next_epoch = self._recover_approvals_after_isolation(
                old_process_isolated=True, recorded_at=recorded_at,
                in_transaction=True)
            run_event = None
            if expected_run_id is not None:
                run_event = self.events._recover_run_after_isolation_in_tx(
                    expected_run_id, expected_version=expected_run_version,
                    old_process_epoch=old_process_epoch, reason=reason,
                    recorded_at=recorded_at)
            db.execute("COMMIT")
            self._recovered = True
            return {"epoch": next_epoch, "run_event": run_event}
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def record_approval_request_closed(
            self, approval_id: str, *, epoch: int, upstream_request_id: str,
            recorded_at: str) -> dict:
        """Persist matched request closure without inferring tool effects."""
        self._require_recovered()
        if (not isinstance(approval_id, str) or not approval_id
                or type(epoch) is not int or epoch < 1
                or not isinstance(upstream_request_id, str)
                or not upstream_request_id
                or not isinstance(recorded_at, str) or not recorded_at):
            raise EventJournalError("invalid_request_closure")
        db = self.events.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            approval = db.execute(
                "SELECT * FROM approvals WHERE approval_id=?",
                (approval_id,)).fetchone()
            if approval is None or approval["run_id"] is None:
                raise EventJournalError("approval_not_found")
            if (approval["epoch"] != epoch
                    or approval["upstream_request_id"] != upstream_request_id):
                raise EventJournalError("request_identity_mismatch")
            if (approval["status"] in {"expired", "resolved"}
                    and approval["resolution_kind"] == "request_closed"):
                result = dict(approval)
                db.execute("COMMIT")
                return result
            if (approval["status"] == "expired"
                    and approval["resolution_kind"] == "turn_terminal"):
                if epoch != self.approvals.current_epoch():
                    raise EventJournalError("resolution_unavailable")
                binding = db.execute(
                    "SELECT process_epoch FROM upstream_turns WHERE run_id=?",
                    (approval["run_id"],)).fetchone()
                if binding is None or binding["process_epoch"] != epoch:
                    raise EventJournalError("request_identity_mismatch")
                run = db.execute(
                    "SELECT session_id FROM runs WHERE run_id=?",
                    (approval["run_id"],)).fetchone()
                session = db.execute(
                    "SELECT session_version, snapshot_json FROM sessions "
                    "WHERE session_id=?", (run["session_id"],)).fetchone()
                if (session["session_version"] >= MAX_SEQ
                        or approval["version"] >= MAX_SEQ):
                    raise EventJournalError("version_exhausted")
                snapshot = json.loads(session["snapshot_json"])
                open_ids = snapshot.get("open_approval_ids")
                if not isinstance(open_ids, list) or approval_id in open_ids:
                    raise EventJournalError("invalid_snapshot_approvals")
                snapshot["session_version"] = session["session_version"] + 1
                encoded = _json_bytes(snapshot)
                if len(encoded) > MAX_EVENT_BYTES:
                    raise EventJournalError("snapshot_too_large")
                db.execute(
                    "UPDATE approvals SET resolution_kind='request_closed', "
                    "version=version+1 WHERE approval_id=?", (approval_id,))
                self.events._append_in_tx(
                    run["session_id"], approval["run_id"], "approval.updated",
                    {"approval_id": approval_id,
                     "approval_version": approval["version"] + 1,
                     "status": "expired", "resolution_kind": "request_closed"},
                    recorded_at, encoded)
                db.execute(
                    "UPDATE sessions SET session_version=session_version+1 "
                    "WHERE session_id=?", (run["session_id"],))
                result = self.approvals.get(approval_id)
                db.execute("COMMIT")
                return result
            if (epoch != self.approvals.current_epoch()
                    or approval["status"] not in {
                        "pending", "decision_recorded", "dispatching", "unknown"}):
                raise EventJournalError("resolution_unavailable")
            binding = db.execute(
                "SELECT process_epoch FROM upstream_turns WHERE run_id=?",
                (approval["run_id"],)).fetchone()
            if binding is None or binding["process_epoch"] != epoch:
                raise EventJournalError("request_identity_mismatch")
            operation = db.execute(
                "SELECT actor_id, request_id, status FROM approval_operations "
                "WHERE approval_id=?", (approval_id,)).fetchone()
            status = approval["status"]
            expected_operation = {
                "pending": None, "decision_recorded": "accepted",
                "dispatching": "dispatching", "unknown": "unknown",
            }[status]
            if ((operation is None and expected_operation is not None)
                    or (operation is not None
                        and operation["status"] != expected_operation)):
                raise EventJournalError("invalid_approval_resolution_state")
            run = db.execute(
                "SELECT session_id FROM runs WHERE run_id=?",
                (approval["run_id"],)).fetchone()
            if run is None:
                raise EventJournalError("run_not_found")
            session = db.execute(
                "SELECT session_version, snapshot_json FROM sessions "
                "WHERE session_id=?", (run["session_id"],)).fetchone()
            needed = 2 if operation is not None else 1
            if (session["session_version"] > MAX_SEQ - needed
                    or approval["version"] >= MAX_SEQ):
                raise EventJournalError("version_exhausted")
            snapshot = json.loads(session["snapshot_json"])
            open_ids = snapshot.get("open_approval_ids")
            if not isinstance(open_ids, list) or approval_id not in open_ids:
                raise EventJournalError("invalid_snapshot_approvals")
            snapshot["open_approval_ids"] = [
                item for item in open_ids if item != approval_id]
            snapshot["session_version"] = session["session_version"] + 1
            encoded = _json_bytes(snapshot)
            if len(encoded) > MAX_EVENT_BYTES:
                raise EventJournalError("snapshot_too_large")
            new_status = "expired" if status in {"pending", "decision_recorded"} else "resolved"
            db.execute(
                "UPDATE approvals SET status=?, resolution_kind='request_closed', "
                "version=version+1 WHERE approval_id=?",
                (new_status, approval_id))
            self.events._append_in_tx(
                run["session_id"], approval["run_id"], "approval.updated",
                {"approval_id": approval_id,
                 "approval_version": approval["version"] + 1,
                 "status": new_status, "resolution_kind": "request_closed"},
                recorded_at, encoded)
            if operation is not None:
                new_operation_status = ("failed" if status == "decision_recorded"
                                        else "confirmed")
                outcome = ("request_closed_before_dispatch"
                           if status == "decision_recorded" else "request_closed")
                snapshot["session_version"] += 1
                encoded = _json_bytes(snapshot)
                if len(encoded) > MAX_EVENT_BYTES:
                    raise EventJournalError("snapshot_too_large")
                db.execute(
                    "UPDATE approval_operations SET status=?, outcome=? "
                    "WHERE actor_id=? AND request_id=?",
                    (new_operation_status, outcome,
                     operation["actor_id"], operation["request_id"]))
                self.events._append_in_tx(
                    run["session_id"], approval["run_id"], "operation.updated",
                    {"request_id": operation["request_id"],
                     "approval_id": approval_id,
                     "status": new_operation_status, "outcome": outcome},
                    recorded_at, encoded)
            db.execute(
                "UPDATE sessions SET session_version=session_version+? "
                "WHERE session_id=?", (needed, run["session_id"]))
            result = dict(db.execute(
                "SELECT * FROM approvals WHERE approval_id=?",
                (approval_id,)).fetchone())
            db.execute("COMMIT")
            return result
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def record_upstream_request_closed(
            self, *, thread_id: str, epoch: int,
            upstream_request_id: str, recorded_at: str) -> dict:
        """Resolve an approval by persisted upstream identity, not caller ID."""
        self._require_recovered()
        if (not isinstance(thread_id, str) or not thread_id
                or type(epoch) is not int or epoch < 1
                or not isinstance(upstream_request_id, str)
                or not upstream_request_id):
            raise EventJournalError("invalid_request_identity")
        row = self.events.connection.execute(
            "SELECT a.approval_id FROM approvals AS a "
            "JOIN upstream_turns AS u ON u.run_id=a.run_id "
            "WHERE a.epoch=? AND a.upstream_request_id=? "
            "AND u.process_epoch=? AND u.thread_id=?",
            (epoch, upstream_request_id, epoch, thread_id)).fetchone()
        if row is None:
            raise EventJournalError("approval_not_found")
        return self.record_approval_request_closed(
            row["approval_id"], epoch=epoch,
            upstream_request_id=upstream_request_id,
            recorded_at=recorded_at)

    def __enter__(self) -> LockedHostJournals:
        return self.open()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
