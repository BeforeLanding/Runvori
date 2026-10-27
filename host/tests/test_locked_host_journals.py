"""Windows startup ownership across the approval and event stores."""

import ctypes
import os
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from runvori.cli import ROOT
from runvori.codex.upstream_lifecycle import OwnedUpstream
from runvori.infrastructure.approval_journal import JournalError
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.instance_lock import InstanceLockUnavailable, WindowsInstanceLock
from runvori.infrastructure.locked_journal import LockedHostJournals
from runvori.infrastructure.process_identity import capture_process_identity

from approval_fixtures import reviewable_command, seed_workspace_session


PEER = Path(__file__).with_name("locked_host_peer.py")
UPSTREAM_PEER = Path(__file__).with_name("upstream_owner_peer.py")


def _kill_pid(pid: int) -> None:
    """Clean up a leftover the Job does not own; never used by the Host gate."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x0001, False, pid)
    if not handle:
        return
    try:
        api.TerminateProcess(handle, 1)
    finally:
        api.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "Windows startup lock")
class LockedHostJournalsTests(unittest.TestCase):
    @staticmethod
    def _start_epoch(startup):
        return startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]

    @staticmethod
    def _pending_approval(startup):
        epoch = LockedHostJournalsTests._start_epoch(startup)
        seed_workspace_session(startup, epoch)
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="run-request",
            expected_session_version=0, input_text="continue",
            recorded_at="2026-10-25T00:00:00Z")
        startup.events.claim_run_dispatch(
            "device", "run-request", process_epoch=epoch,
            recorded_at="2026-10-25T00:00:01Z")
        startup.events.confirm_run_started(
            "device", "run-request", process_epoch=epoch,
            thread_id="thread", turn_id="turn",
            recorded_at="2026-10-25T00:00:02Z")
        startup.record_approval_request(
            "session", "run", approval_id="approval", epoch=epoch,
            upstream_request_id="upstream", kind="command",
            details=reviewable_command(startup.data_dir),
            turn_id="turn",
            recorded_at="2026-10-25T00:00:03Z")
        return epoch

    def test_shared_approval_probe_writes_cannot_bypass_host_events(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    startup.record_approval_request(
                        "session", "run", approval_id="approval", epoch=1,
                        upstream_request_id="upstream", kind="command",
                        recorded_at="2026-10-25T00:00:00Z")
                epoch = self._start_epoch(startup)
                calls = (
                    lambda: startup.approvals.start_epoch_after_isolation(
                        old_process_isolated=True),
                    lambda: startup.approvals.record_request(
                        approval_id="approval", epoch=epoch,
                        upstream_request_id="upstream", kind="command"),
                    lambda: startup.approvals.record_decision(
                        approval_id="approval", request_id="request",
                        decision="accept", expected_version=1),
                    lambda: startup.approvals.claim_dispatch(
                        approval_id="approval", epoch=epoch),
                    lambda: startup.approvals.record_request_closed(
                        approval_id="approval", epoch=epoch),
                    lambda: startup.approvals.inspect_unknown_file(
                        approval_id="approval", workspace=Path(temp),
                        relative_path="file", expected_bytes=b"test"),
                )
                for call in calls:
                    with self.assertRaisesRegex(
                            JournalError, "shared_store_requires_host_transaction"):
                        call()
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertEqual(startup.events.connection.execute(
                    "SELECT COUNT(*) FROM approvals").fetchone()[0], 0)

    def test_epoch_without_recorded_process_recovers_and_opens_gate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                args = {"old_process_epoch": 0, "expected_run_id": None,
                        "expected_run_version": None, "reason": "host_restart",
                        "recorded_at": "2026-10-25T00:00:00Z"}
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    startup.events.create_session("session", {})
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    startup.events.bind_session_thread("session", "thread_demo")
                self.assertEqual(startup.recover_host_after_isolation(**args),
                                 {"epoch": 1, "run_event": None})
                startup.events.create_session("session", {})
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 0)

    def test_recorded_live_process_refuses_recovery_and_keeps_gate_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._start_epoch(startup)
                owner = OwnedUpstream(startup, epoch)
                startup.reserve_upstream_owner(owner, epoch=epoch)
                try:
                    live = subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(60)"])
                    try:
                        identity = capture_process_identity(live)
                        startup.record_upstream_process(
                            owner, epoch=epoch, pid=identity["pid"],
                            created_at=identity["created_at"],
                            recorded_at="2026-10-25T00:00:01Z")
                        startup.release_upstream_owner(owner)
                        owner = None
                        with self.assertRaisesRegex(
                                EventJournalError, "upstream_isolation_unconfirmed"):
                            startup.recover_host_after_isolation(
                                old_process_epoch=epoch, expected_run_id=None,
                                expected_run_version=None, reason="host_restart",
                                recorded_at="2026-10-25T00:00:02Z")
                        self.assertEqual(startup.approvals.current_epoch(), epoch)
                        with self.assertRaisesRegex(EventJournalError,
                                                    "host_not_recovered"):
                            startup.events.create_session("blocked", {})
                        live.kill()
                        live.wait(timeout=5)
                        recovered = startup.recover_host_after_isolation(
                            old_process_epoch=epoch, expected_run_id=None,
                            expected_run_version=None, reason="host_restart",
                            recorded_at="2026-10-25T00:00:03Z")
                        self.assertEqual(recovered["epoch"], epoch + 1)
                        startup.events.create_session("accepted", {})
                    finally:
                        if live.poll() is None:
                            live.kill()
                        live.wait(timeout=5)
                finally:
                    if owner is not None:
                        startup.release_upstream_owner(owner)

    def test_same_epoch_record_replaces_only_an_isolated_process(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._start_epoch(startup)
                owner = OwnedUpstream(startup, epoch)
                startup.reserve_upstream_owner(owner, epoch=epoch)
                first = second = None
                try:
                    first = subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(60)"])
                    second = subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(60)"])
                    original = capture_process_identity(first)
                    startup.record_upstream_process(
                        owner, epoch=epoch, pid=original["pid"],
                        created_at=original["created_at"],
                        recorded_at="2026-10-25T00:00:01Z")
                    replacement = capture_process_identity(second)
                    with self.assertRaisesRegex(
                            EventJournalError, "upstream_isolation_unconfirmed"):
                        startup.record_upstream_process(
                            owner, epoch=epoch, pid=replacement["pid"],
                            created_at=replacement["created_at"],
                            recorded_at="2026-10-25T00:00:02Z")
                    self.assertEqual(startup.events.connection.execute(
                        "SELECT pid FROM upstream_processes").fetchone()[0],
                        original["pid"])
                    first.kill()
                    first.wait(timeout=5)
                    startup.record_upstream_process(
                        owner, epoch=epoch, pid=replacement["pid"],
                        created_at=replacement["created_at"],
                        recorded_at="2026-10-25T00:00:03Z")
                    self.assertEqual(startup.events.connection.execute(
                        "SELECT pid FROM upstream_processes").fetchone()[0],
                        replacement["pid"])
                finally:
                    for process in (first, second):
                        if process is not None:
                            if process.poll() is None:
                                process.kill()
                            process.wait(timeout=5)
                    startup.release_upstream_owner(owner)

    def test_record_requires_the_owned_connection_for_that_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._start_epoch(startup)
                owner = OwnedUpstream(startup, epoch)
                forged = OwnedUpstream(startup, epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_owner_mismatch"):
                    startup.record_upstream_process(
                        forged, epoch=epoch, pid=1, created_at=1,
                        recorded_at="2026-10-25T00:00:01Z")
                startup.reserve_upstream_owner(owner, epoch=epoch)
                try:
                    with self.assertRaisesRegex(EventJournalError,
                                                "invalid_process_identity"):
                        startup.record_upstream_process(
                            owner, epoch=epoch, pid=0, created_at=1,
                            recorded_at="2026-10-25T00:00:01Z")
                    with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                        startup.record_upstream_process(
                            owner, epoch=epoch + 1, pid=1, created_at=1,
                            recorded_at="2026-10-25T00:00:01Z")
                finally:
                    startup.release_upstream_owner(owner)

    def test_managed_process_killed_with_host_passes_the_next_recovery(self):
        # The recorded launcher belongs to this Host's Job, so killing the Host
        # must leave nothing alive and recovery must proceed.
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            marker = data_dir / "owner.ready"
            owner = self._spawn_peer(data_dir, marker, "owned")
            try:
                self._await_marker(marker, owner)
                owner.kill()
                owner.wait(timeout=5)
                with LockedHostJournals(data_dir) as reopened:
                    self.assertEqual(reopened.events.connection.execute(
                        "SELECT COUNT(*) FROM upstream_processes").fetchone()[0], 1)
                    self.assertEqual(reopened.recover_host_after_isolation(
                        old_process_epoch=1, expected_run_id=None,
                        expected_run_version=None, reason="host_restart",
                        recorded_at="2026-10-25T00:00:02Z")["epoch"], 2)
                    reopened.events.create_session("after-crash", {})
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)

    def test_unmanaged_leftover_blocks_the_next_recovery_across_a_crash(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            marker = data_dir / "owner.ready"
            owner = self._spawn_peer(data_dir, marker, "leftover")
            leftover_pid = None
            try:
                leftover_pid = self._await_marker(marker, owner)
                owner.kill()
                owner.wait(timeout=5)
                with LockedHostJournals(data_dir) as reopened:
                    with self.assertRaisesRegex(
                            EventJournalError, "upstream_isolation_unconfirmed"):
                        reopened.recover_host_after_isolation(
                            old_process_epoch=1, expected_run_id=None,
                            expected_run_version=None, reason="host_restart",
                            recorded_at="2026-10-25T00:00:02Z")
                    self.assertEqual(reopened.approvals.current_epoch(), 1)
                    with self.assertRaisesRegex(EventJournalError,
                                                "host_not_recovered"):
                        reopened.events.create_session("blocked", {})
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)
                if leftover_pid is not None:
                    _kill_pid(leftover_pid)

    @staticmethod
    def _spawn_peer(data_dir: Path, marker: Path, mode: str):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
        return subprocess.Popen(
            [sys.executable, str(UPSTREAM_PEER), mode, str(data_dir), str(marker)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def _await_marker(self, marker: Path, owner) -> int:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            self.assertIsNone(owner.poll(), "upstream owner exited before readiness")
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "upstream owner readiness timeout")
        return int(marker.read_text(encoding="ascii").split()[1])

    def test_host_recovery_commits_approval_epoch_and_run_together(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.claim_approval_dispatch(
                    "device", "decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(EventJournalError, "resource_changed"):
                    startup.recover_host_after_isolation(
                        old_process_epoch=epoch,
                        expected_run_id="run", expected_run_version=2,
                        reason="host_restart",
                        recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 9)
                recovered = startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart", recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(recovered["epoch"], epoch + 1)
                self.assertEqual(recovered["run_event"]["type"], "run.interrupted")
                self.assertEqual(startup.approvals.get("approval")["status"], "unknown")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["outcome"],
                    "dispatch_unconfirmed")
                snapshot = startup.events.snapshot("session")
                self.assertIsNone(snapshot["active_run"])
                self.assertEqual(snapshot["open_approval_ids"], ["approval"])
                self.assertEqual(snapshot["snapshot_seq"], 12)
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT run_id FROM active_slot").fetchone())
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.approvals.current_epoch(), epoch + 1)
                self.assertEqual(reopened.events.snapshot("session")["snapshot_seq"], 12)
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    reopened.recover_host_after_isolation(
                        old_process_epoch=epoch,
                        expected_run_id="run", expected_run_version=3,
                        reason="host_restart",
                        recorded_at="2026-10-25T00:00:07Z")

    def test_host_recovery_run_event_failure_rolls_back_approval_and_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.claim_approval_dispatch(
                    "device", "decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_run_recovery BEFORE INSERT ON events
                    WHEN NEW.seq = 12
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                args = {"old_process_epoch": epoch, "expected_run_id": "run",
                        "expected_run_version": 3, "reason": "host_restart",
                        "recorded_at": "2026-10-25T00:00:06Z"}
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.recover_host_after_isolation(**args)
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    startup.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:07Z")
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    startup.events.finish_run(
                        "run", expected_version=3, terminal="interrupted",
                        recorded_at="2026-10-25T00:00:07Z")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertEqual(startup.approvals.get("approval")["status"],
                                 "dispatching")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "dispatching")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 9)
                self.assertEqual(db.execute(
                    "SELECT run_id FROM active_slot").fetchone()[0], "run")
                db.execute("DROP TRIGGER block_run_recovery")
                self.assertEqual(startup.recover_host_after_isolation(
                    **args)["run_event"]["seq"], 12)

    def test_contended_lock_never_opens_either_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with WindowsInstanceLock(data_dir / "host.lock"):
                with (patch("runvori.infrastructure.locked_journal.ApprovalJournal",
                            side_effect=AssertionError("approval_opened")),
                      patch("runvori.infrastructure.locked_journal.EventJournal",
                            side_effect=AssertionError("events_opened"))):
                    with self.assertRaisesRegex(InstanceLockUnavailable,
                                                "instance_lock_unavailable"):
                        LockedHostJournals(data_dir).open()
            self.assertFalse((data_dir / "host.db").exists())

    def test_approval_schema_failure_closes_connection_and_releases_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with patch("runvori.infrastructure.locked_journal.ApprovalJournal",
                       side_effect=sqlite3.OperationalError("schema_failed")):
                with self.assertRaisesRegex(sqlite3.OperationalError, "schema_failed"):
                    LockedHostJournals(data_dir).open()
            with LockedHostJournals(data_dir) as startup:
                self.assertEqual(startup.approvals.current_epoch(), 0)
                self._start_epoch(startup)
                startup.events.create_session("session", {})
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 0)
                self.assertIs(startup.approvals.connection, startup.events.connection)

    def test_split_probe_files_are_refused_without_creating_new_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            legacy = data_dir / "events.db"
            legacy.write_bytes(b"old-probe")
            with self.assertRaisesRegex(InstanceLockUnavailable,
                                        "legacy_split_store_present"):
                LockedHostJournals(data_dir).open()
            self.assertEqual(legacy.read_bytes(), b"old-probe")
            self.assertFalse((data_dir / "host.db").exists())
            legacy.unlink()
            with LockedHostJournals(data_dir) as startup:
                self.assertIs(startup.approvals.connection, startup.events.connection)

    def test_crash_reopen_keeps_epoch_and_active_slot_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            marker = data_dir / "owner.ready"
            env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
            owner = subprocess.Popen(
                [sys.executable, str(PEER), str(data_dir), str(marker)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    self.assertIsNone(owner.poll(), "journal owner exited before readiness")
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "journal owner readiness timeout")
                with self.assertRaisesRegex(InstanceLockUnavailable,
                                            "instance_lock_unavailable"):
                    LockedHostJournals(data_dir).open()
                owner.kill()
                owner.wait(timeout=5)
                with LockedHostJournals(data_dir) as reopened:
                    self.assertEqual(reopened.approvals.current_epoch(), 1)
                    self.assertIs(reopened.approvals.connection,
                                  reopened.events.connection)
                    self.assertEqual(reopened.events.snapshot("session")["active_run"],
                                     {"run_id": "run", "status": "starting", "version": 1})
                    self.assertEqual(reopened.events.connection.execute(
                        "SELECT run_id FROM active_slot").fetchone()[0], "run")
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)

    def test_approval_request_and_event_commit_together(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._start_epoch(startup)
                startup.events.create_session("session", {})
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="request",
                    expected_session_version=0, input_text="continue",
                    recorded_at="2026-10-25T00:00:00Z")
                startup.events.claim_run_dispatch(
                    "device", "request", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:01Z")
                startup.events.confirm_run_started(
                    "device", "request", process_epoch=epoch,
                    thread_id="thread", turn_id="turn",
                    recorded_at="2026-10-25T00:00:02Z")
                event = startup.record_approval_request(
                    "session", "run", approval_id="approval", epoch=epoch,
                    upstream_request_id="upstream", kind="command",
                    recorded_at="2026-10-25T00:00:03Z")
                self.assertEqual(event["type"], "approval.requested")
                self.assertEqual(startup.approvals.get("approval")["run_id"], "run")
                snapshot = startup.events.snapshot("session")
                self.assertEqual(snapshot["snapshot_seq"], event["seq"])
                self.assertEqual(snapshot["open_approval_ids"], ["approval"])
                self.assertEqual(snapshot["active_run"]["status"], "waiting_approval")
                next_epoch = startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:04Z")["epoch"]
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    startup.record_approval_request(
                        "session", "run", approval_id="late", epoch=epoch,
                        upstream_request_id="late", kind="command",
                        recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(next_epoch, epoch + 1)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"],
                                 event["seq"] + 2)

    def test_approval_event_failure_rolls_back_row_and_run_status(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._start_epoch(startup)
                startup.events.create_session("session", {})
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="request",
                    expected_session_version=0, input_text="continue",
                    recorded_at="2026-10-25T00:00:00Z")
                startup.events.claim_run_dispatch(
                    "device", "request", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:01Z")
                startup.events.confirm_run_started(
                    "device", "request", process_epoch=epoch,
                    thread_id="thread", turn_id="turn",
                    recorded_at="2026-10-25T00:00:02Z")
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_approval_event BEFORE INSERT ON events
                    WHEN NEW.seq = 5
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.record_approval_request(
                        "session", "run", approval_id="approval", epoch=epoch,
                        upstream_request_id="upstream", kind="command",
                        recorded_at="2026-10-25T00:00:03Z")
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "running")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 4)
                db.execute("DROP TRIGGER block_approval_event")
                self.assertEqual(startup.record_approval_request(
                    "session", "run", approval_id="approval", epoch=epoch,
                    upstream_request_id="upstream", kind="command",
                    recorded_at="2026-10-25T00:00:04Z")["seq"], 5)

    def test_decision_replays_after_reopen_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                self._pending_approval(startup)
                accepted = startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(accepted, {
                    "request_id": "decision-request", "status": "accepted",
                    "resource_id": "approval", "accepted_event_seq": 6})
                self.assertEqual(startup.approvals.get("approval")["decision"], "accept")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 7)
                self.assertEqual(startup.events.snapshot("session")["session_version"], 5)
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.get_approval_operation(
                    "device", "decision-request"), accepted)
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    reopened.record_approval_decision(
                        "approval", actor_id="device",
                        request_id="decision-request", expected_version=1,
                        decision="allow_once",
                        recorded_at="2026-10-25T00:00:05Z")
                reopened.recover_host_after_isolation(
                    old_process_epoch=1,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart", recorded_at="2026-10-25T00:00:05Z")
                recovered = reopened.get_approval_operation(
                    "device", "decision-request")
                self.assertEqual(recovered["status"], "failed")
                self.assertEqual(recovered["outcome"], "not_dispatched")
                self.assertEqual(reopened.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:05Z"), recovered)
                self.assertEqual(reopened.record_approval_decision(
                    "approval", actor_id="device", request_id="other-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:05Z"), recovered)
                for request_id, version, decision, error in (
                        ("decision-request", 1, "deny", "idempotency_conflict"),
                        ("decision-request", 2, "allow_once", "idempotency_conflict"),
                        ("other-request", 1, "deny", "decision_conflict"),
                        ("run-request", 1, "allow_once", "idempotency_conflict")):
                    with self.assertRaisesRegex(EventJournalError, error):
                        reopened.record_approval_decision(
                            "approval", actor_id="device", request_id=request_id,
                            expected_version=version, decision=decision,
                            recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(EventJournalError, "idempotency_conflict"):
                    reopened.events.reserve_run_request(
                        "session", "other-run", actor_id="device",
                        request_id="decision-request",
                        expected_session_version=8, input_text="continue",
                        recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(EventJournalError, "operation_not_found"):
                    reopened.get_approval_operation("other-device", "decision-request")
                self.assertEqual(reopened.events.snapshot("session")["snapshot_seq"], 10)
                self.assertEqual(reopened.events.connection.execute(
                    "SELECT COUNT(*) FROM approval_operations").fetchone()[0], 1)

    def test_decision_event_failure_rolls_back_approval_and_operation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._pending_approval(startup)
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_decision_event BEFORE INSERT ON events
                    WHEN NEW.seq = 7
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.record_approval_decision(
                        "approval", actor_id="device", request_id="decision-request",
                        expected_version=1, decision="deny",
                        recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(startup.approvals.get("approval")["status"], "pending")
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM approval_operations").fetchone()[0], 0)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 5)
                db.execute("DROP TRIGGER block_decision_event")
                self.assertEqual(startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:05Z")["accepted_event_seq"], 6)

    def test_decision_rejects_stale_version_and_expired_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._pending_approval(startup)
                with self.assertRaisesRegex(EventJournalError, "resource_changed"):
                    startup.record_approval_decision(
                        "approval", actor_id="device", request_id="decision-request",
                        expected_version=2, decision="deny",
                        recorded_at="2026-10-25T00:00:04Z")
                startup.recover_host_after_isolation(
                    old_process_epoch=1,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(EventJournalError, "approval_expired"):
                    startup.record_approval_decision(
                        "approval", actor_id="device", request_id="decision-request",
                        expected_version=1, decision="deny",
                        recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 7)

    def test_decision_request_key_is_scoped_to_actor(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="same-key",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.record_approval_request(
                    "session", "run", approval_id="second", epoch=epoch,
                    upstream_request_id="second-upstream", kind="command",
                    details=reviewable_command(startup.data_dir),
                    turn_id="turn", recorded_at="2026-10-25T00:00:05Z")
                second = startup.record_approval_decision(
                    "second", actor_id="other-device", request_id="same-key",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(second["resource_id"], "second")
                self.assertEqual(startup.get_approval_operation(
                    "device", "same-key")["resource_id"], "approval")
                self.assertEqual(startup.get_approval_operation(
                    "other-device", "same-key")["resource_id"], "second")

    def test_dispatch_claim_once_and_unknown_survive_reopen(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:04Z")
                claim = startup.claim_approval_dispatch(
                    "device", "decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(claim["status"], "dispatching")
                self.assertEqual(startup.approvals.get("approval")["version"], 3)
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 9)
                with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                    startup.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.get_approval_operation(
                    "device", "decision-request"), claim)
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    reopened.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:07Z")
                reopened.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart", recorded_at="2026-10-25T00:00:08Z")
                unknown = reopened.get_approval_operation(
                    "device", "decision-request")
                self.assertEqual(unknown["outcome"], "dispatch_unconfirmed")
                self.assertEqual(reopened.approvals.get("approval")["status"], "unknown")
                self.assertEqual(reopened.events.snapshot("session")["snapshot_seq"], 12)
                with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                    reopened.mark_approval_dispatch_unknown(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:09Z")
                with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                    reopened.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:09Z")

    def test_dispatch_claim_event_failure_rolls_back_both_states(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:04Z")
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_claim_event BEFORE INSERT ON events
                    WHEN NEW.seq = 9
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.approvals.get("approval")["status"],
                                 "decision_recorded")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 7)
                db.execute("DROP TRIGGER block_claim_event")
                self.assertEqual(startup.claim_approval_dispatch(
                    "device", "decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:06Z")["status"], "dispatching")

    def test_dispatch_claim_rejects_expired_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                    startup.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "failed")

    def test_unknown_event_failure_keeps_claimed_state(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.claim_approval_dispatch(
                    "device", "decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_unknown_event BEFORE INSERT ON events
                    WHEN NEW.seq = 11
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.mark_approval_dispatch_unknown(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(startup.approvals.get("approval")["status"],
                                 "dispatching")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "dispatching")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 9)
                with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                    startup.claim_approval_dispatch(
                        "device", "decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:07Z")

    def test_isolated_epoch_recovery_expires_unclaimed_and_preserves_unknown(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="unclaimed",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.record_approval_request(
                    "session", "run", approval_id="second", epoch=epoch,
                    upstream_request_id="second-upstream", kind="command",
                    details=reviewable_command(startup.data_dir),
                    turn_id="turn", recorded_at="2026-10-25T00:00:05Z")
                startup.record_approval_decision(
                    "second", actor_id="device", request_id="claimed",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:06Z")
                startup.claim_approval_dispatch(
                    "device", "claimed", epoch=epoch,
                    recorded_at="2026-10-25T00:00:07Z")
                startup.record_approval_request(
                    "session", "run", approval_id="third", epoch=epoch,
                    upstream_request_id="third-upstream", kind="file_change",
                    recorded_at="2026-10-25T00:00:08Z")
                before = startup.events.snapshot("session")
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    startup.recover_host_after_isolation(
                        old_process_epoch=epoch + 1,
                        expected_run_id="run", expected_run_version=5,
                        reason="host_restart",
                        recorded_at="2026-10-25T00:00:09Z")
                self.assertEqual(startup.events.snapshot("session"), before)
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertEqual(startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=5,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:09Z")["epoch"], epoch + 1)
                self.assertEqual(startup.approvals.get("approval")["status"], "expired")
                self.assertEqual(startup.approvals.get("second")["status"], "unknown")
                self.assertEqual(startup.approvals.get("third")["status"], "expired")
                self.assertEqual(startup.get_approval_operation(
                    "device", "unclaimed")["outcome"], "not_dispatched")
                self.assertEqual(startup.get_approval_operation(
                    "device", "claimed")["outcome"], "dispatch_unconfirmed")
                snapshot = startup.events.snapshot("session")
                self.assertEqual(snapshot["open_approval_ids"], ["second"])
                self.assertEqual(snapshot["snapshot_seq"], 19)
                self.assertEqual(snapshot["session_version"], 15)
                self.assertIsNone(snapshot["active_run"])
                with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                    startup.claim_approval_dispatch(
                        "device", "unclaimed", epoch=epoch,
                        recorded_at="2026-10-25T00:00:10Z")
                self.assertEqual(startup.events.snapshot("session")["open_approval_ids"],
                                 ["second"])
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.approvals.current_epoch(), epoch + 1)
                self.assertEqual(reopened.events.snapshot("session")["snapshot_seq"], 19)

    def test_isolated_epoch_recovery_event_failure_rolls_back_everything(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="unclaimed",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.record_approval_request(
                    "session", "run", approval_id="second", epoch=epoch,
                    upstream_request_id="second-upstream", kind="command",
                    details=reviewable_command(startup.data_dir),
                    turn_id="turn", recorded_at="2026-10-25T00:00:05Z")
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_recovery_event BEFORE INSERT ON events
                    WHEN NEW.seq = 11
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                args = {"old_process_epoch": epoch, "expected_run_id": "run",
                        "expected_run_version": 4, "reason": "host_restart",
                        "recorded_at": "2026-10-25T00:00:05Z"}
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.recover_host_after_isolation(**args)
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertEqual(startup.approvals.get("approval")["status"],
                                 "decision_recorded")
                self.assertEqual(startup.approvals.get("second")["status"], "pending")
                self.assertEqual(startup.get_approval_operation(
                    "device", "unclaimed")["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 8)
                db.execute("DROP TRIGGER block_recovery_event")
                args["recorded_at"] = "2026-10-25T00:00:06Z"
                self.assertEqual(startup.recover_host_after_isolation(
                    **args)["epoch"], epoch + 1)

    def test_request_closure_after_terminal_run_expires_pending(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.events.finish_run(
                    "run", expected_version=3, terminal="interrupted",
                    recorded_at="2026-10-25T00:00:04Z")
                closed = startup.record_approval_request_closed(
                    "approval", epoch=epoch, upstream_request_id="upstream",
                    recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(closed["status"], "expired")
                self.assertEqual(closed["resolution_kind"], "request_closed")
                self.assertEqual(startup.events.snapshot("session")["open_approval_ids"],
                                 [])
                self.assertIsNone(startup.events.snapshot("session")["active_run"])
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 7)

    def test_request_closure_before_dispatch_fails_operation_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="allow_once",
                    recorded_at="2026-10-25T00:00:04Z")
                with self.assertRaisesRegex(EventJournalError,
                                            "request_identity_mismatch"):
                    startup.record_approval_request_closed(
                        "approval", epoch=epoch, upstream_request_id="other",
                        recorded_at="2026-10-25T00:00:05Z")
                closed = startup.record_approval_request_closed(
                    "approval", epoch=epoch, upstream_request_id="upstream",
                    recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(closed["status"], "expired")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["outcome"],
                    "request_closed_before_dispatch")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 9)
            with LockedHostJournals(data_dir) as reopened:
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    reopened.record_approval_request_closed(
                        "approval", epoch=epoch, upstream_request_id="upstream",
                        recorded_at="2026-10-25T00:00:06Z")
                reopened.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="host_restart", recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(reopened.record_approval_request_closed(
                    "approval", epoch=epoch, upstream_request_id="upstream",
                    recorded_at="2026-10-25T00:00:06Z"), closed)
                self.assertEqual(reopened.events.snapshot("session")["snapshot_seq"], 10)

    def test_request_closure_confirms_end_without_claiming_effect(self):
        for mark_unknown in (False, True):
            with self.subTest(mark_unknown=mark_unknown):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    with LockedHostJournals(Path(temp)) as startup:
                        epoch = self._pending_approval(startup)
                        startup.record_approval_decision(
                            "approval", actor_id="device",
                            request_id="decision-request", expected_version=1,
                            decision="allow_once",
                            recorded_at="2026-10-25T00:00:04Z")
                        startup.claim_approval_dispatch(
                            "device", "decision-request", epoch=epoch,
                            recorded_at="2026-10-25T00:00:05Z")
                        if mark_unknown:
                            startup.mark_approval_dispatch_unknown(
                                "device", "decision-request", epoch=epoch,
                                recorded_at="2026-10-25T00:00:06Z")
                        closed = startup.record_approval_request_closed(
                            "approval", epoch=epoch,
                            upstream_request_id="upstream",
                            recorded_at="2026-10-25T00:00:07Z")
                        self.assertEqual(closed["status"], "resolved")
                        self.assertEqual(closed["resolution_kind"], "request_closed")
                        self.assertEqual(startup.get_approval_operation(
                            "device", "decision-request")["outcome"],
                            "request_closed")
                        self.assertEqual(startup.events.snapshot(
                            "session")["open_approval_ids"], [])
                        with self.assertRaisesRegex(EventJournalError,
                                                    "dispatch_unavailable"):
                            startup.claim_approval_dispatch(
                                "device", "decision-request", epoch=epoch,
                                recorded_at="2026-10-25T00:00:08Z")

    def test_request_closure_second_event_failure_rolls_back(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                startup.record_approval_decision(
                    "approval", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.claim_approval_dispatch(
                    "device", "decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                db = startup.events.connection
                db.execute("""
                    CREATE TRIGGER block_close_event BEFORE INSERT ON events
                    WHEN NEW.seq = 11
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """)
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.record_approval_request_closed(
                        "approval", epoch=epoch, upstream_request_id="upstream",
                        recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(startup.approvals.get("approval")["status"],
                                 "dispatching")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "dispatching")
                self.assertEqual(startup.events.snapshot("session")["snapshot_seq"], 9)
                db.execute("DROP TRIGGER block_close_event")
                self.assertEqual(startup.record_approval_request_closed(
                    "approval", epoch=epoch, upstream_request_id="upstream",
                    recorded_at="2026-10-25T00:00:07Z")["status"], "resolved")

    def test_clean_close_marks_next_epoch_unlaunched(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._start_epoch(startup)
                self.assertEqual(startup.events.connection.execute(
                    "SELECT state FROM upstream_epochs WHERE process_epoch=?",
                    (epoch,)).fetchone()[0], "unlaunched")
            with LockedHostJournals(data_dir) as reopened:
                state = reopened.startup_state()
                self.assertEqual(state["epoch"], epoch)
                self.assertEqual(state["epoch_state"], "unlaunched")
                self.assertIsNone(state["active_run"])
                self.assertEqual(reopened.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:02Z")["epoch"], epoch + 1)
                self.assertEqual(reopened.events.connection.execute(
                    "SELECT state FROM upstream_epochs WHERE process_epoch=?",
                    (epoch + 1,)).fetchone()[0], "unlaunched")

    def test_v12_epoch_without_process_record_is_legacy_unknown(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._pending_approval(startup)
                before = startup.events.snapshot("session")
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute("DROP TABLE upstream_epochs")
                db.execute("PRAGMA user_version=12")
                db.commit()
            with LockedHostJournals(data_dir) as reopened:
                state = reopened.startup_state()
                self.assertEqual(state["epoch"], epoch)
                self.assertEqual(state["epoch_state"], "legacy_unknown")
                self.assertEqual(state["active_run"]["run_id"], "run")
                version = state["active_run"]["version"]
                with self.assertRaisesRegex(EventJournalError,
                                            "legacy_upstream_unverified"):
                    reopened.recover_host_after_isolation(
                        old_process_epoch=epoch,
                        expected_run_id="run", expected_run_version=version,
                        reason="host_restart",
                        recorded_at="2026-10-25T00:00:09Z")
                self.assertEqual(reopened.approvals.current_epoch(), epoch)
                self.assertEqual(reopened.approvals.get("approval")["status"],
                                 "pending")
                self.assertEqual(reopened.events.snapshot("session"), before)
                with self.assertRaisesRegex(EventJournalError,
                                            "host_not_recovered"):
                    reopened.events.create_session("blocked", {})
            # The refusal is stable across restarts; no epoch is silently upgraded.
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.startup_state()["epoch_state"],
                                 "legacy_unknown")
                with self.assertRaisesRegex(EventJournalError,
                                            "legacy_upstream_unverified"):
                    reopened.recover_host_after_isolation(
                        old_process_epoch=epoch,
                        expected_run_id="run", expected_run_version=version,
                        reason="host_restart",
                        recorded_at="2026-10-25T00:00:10Z")

    def test_v12_epoch_with_recorded_process_migrates_to_recorded(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._start_epoch(startup)
                owner = OwnedUpstream(startup, epoch)
                startup.reserve_upstream_owner(owner, epoch=epoch)
                live = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"])
                try:
                    identity = capture_process_identity(live)
                    startup.record_upstream_process(
                        owner, epoch=epoch, pid=identity["pid"],
                        created_at=identity["created_at"],
                        recorded_at="2026-10-25T00:00:01Z")
                finally:
                    live.kill()
                    live.wait(timeout=5)
                    startup.release_upstream_owner(owner)
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute("DROP TABLE upstream_epochs")
                db.execute("PRAGMA user_version=12")
                db.commit()
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.startup_state()["epoch_state"],
                                 "recorded")
                result = reopened.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(result["epoch"], epoch + 1)

    def test_inconsistent_epoch_state_is_refused_at_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                epoch = self._start_epoch(startup)
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute(
                    "INSERT INTO upstream_processes VALUES (?, 1, 1, 'now')",
                    (epoch,))
                db.commit()
            with self.assertRaisesRegex(EventJournalError,
                                        "upstream_epoch_state_invalid"):
                LockedHostJournals(data_dir).open()
            # The failed open released the lock.
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute("DELETE FROM upstream_processes")
                db.commit()
            with LockedHostJournals(data_dir) as reopened:
                self.assertEqual(reopened.startup_state()["epoch_state"],
                                 "unlaunched")

    def test_epoch_zero_with_upstream_history_is_refused_at_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as startup:
                self._pending_approval(startup)
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute("UPDATE journal_meta SET epoch=0")
                db.execute("DROP TABLE upstream_epochs")
                db.commit()
            with self.assertRaisesRegex(EventJournalError,
                                        "upstream_epoch_state_invalid"):
                LockedHostJournals(data_dir).open()

    def test_host_shutdown_reason_is_recorded_for_active_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._pending_approval(startup)
                version = startup.events.connection.execute(
                    "SELECT version FROM runs WHERE run_id='run'").fetchone()[0]
                result = startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=version,
                    reason="host_shutdown",
                    recorded_at="2026-10-25T00:00:09Z")
                reason = startup.events.connection.execute(
                    "SELECT event_json FROM events WHERE session_id='session' "
                    "AND seq=?", (result["run_event"]["seq"],)).fetchone()[0]
                self.assertIn('"reason":"host_shutdown"', reason)


if __name__ == "__main__":
    unittest.main()
