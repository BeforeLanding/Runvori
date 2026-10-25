"""M0 startup slice: acquire Host ownership before opening the approval journal.

The journal epoch is intentionally not advanced here. A future Host must first
isolate any old app-server process and then call start_epoch_after_isolation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runvori.infrastructure.approval_journal import ApprovalJournal
from runvori.infrastructure.event_journal import (
    MAX_EVENT_BYTES, MAX_SEQ, EventJournal, EventJournalError, _json_bytes,
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
            events = EventJournal(self.data_dir / "host.db")
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
        return self

    def close(self) -> None:
        events, approvals, lock = self.events, self.approvals, self.lock
        self.events = None
        self.approvals = None
        self.lock = None
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

    def record_approval_request(
            self, session_id: str, run_id: str, *, approval_id: str,
            epoch: int, upstream_request_id: str, kind: str,
            recorded_at: str) -> dict:
        """Persist one approval and its normalized event in one transaction."""
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
        if (not session_id or not run_id or not approval_id
                or type(epoch) is not int or epoch < 1
                or not upstream_request_id or kind not in {"command", "file_change"}
                or not recorded_at):
            raise EventJournalError("invalid_approval_request")
        db = self.events.connection
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
                "kind, status, version, run_id) VALUES (?, ?, ?, ?, 'pending', 1, ?)",
                (approval_id, epoch, upstream_request_id, kind, run_id))
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
            db.execute("COMMIT")
            return event
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def record_approval_decision(
            self, approval_id: str, *, actor_id: str, request_id: str,
            expected_version: int, decision: str, recorded_at: str) -> dict:
        """Accept one immutable decision with its operation and events atomically."""
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
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
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
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

    def recover_approvals_after_isolation(
            self, *, old_process_isolated: bool, recorded_at: str) -> int:
        """Advance the epoch with all outstanding approval projections in one commit."""
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
        if not old_process_isolated:
            raise EventJournalError("old_process_not_isolated")
        if not isinstance(recorded_at, str) or not recorded_at:
            raise EventJournalError("invalid_recovery")
        db = self.events.connection
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
            db.execute("COMMIT")
            return old_epoch + 1
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def record_approval_request_closed(
            self, approval_id: str, *, epoch: int, upstream_request_id: str,
            recorded_at: str) -> dict:
        """Persist matched request closure without inferring tool effects."""
        if self.events is None or self.approvals is None:
            raise InstanceLockUnavailable("locked_journals_not_open")
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

    def __enter__(self) -> LockedHostJournals:
        return self.open()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
