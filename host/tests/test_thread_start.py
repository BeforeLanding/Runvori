"""Durable one-shot thread creation and crash-safe unknown outcomes."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.stdio import StdioClient, TransportError
from runvori.codex.thread_start import ThreadStartError, create_session_thread_once
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class ThreadStartTests(unittest.TestCase):
    @staticmethod
    def _ready(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.register_workspace("workspace", ROOT, sandbox="read-only",
                                   epoch=epoch)
        startup.create_session_in_workspace("session", "workspace", {},
                                            epoch=epoch)
        return epoch

    @staticmethod
    def _create(client, startup, epoch, *, request_id="create-request"):
        return create_session_thread_once(
            client, startup, session_id="session", actor_id="local",
            request_id=request_id, epoch=epoch)

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_confirmed_thread_is_bound_once_across_reopen(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            with LockedHostJournals(folder) as startup:
                epoch = self._ready(startup)
                with StdioClient(
                        [sys.executable, str(PEER), "thread_bootstrap_good"],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    created = self._create(client, startup, epoch)
                self.assertEqual(created["status"], "confirmed")
                self.assertEqual(created["thread_id"], "thread_demo")
                self.assertEqual(startup.events.get_session_thread("session"),
                                 "thread_demo")
                class RejectClient:
                    def request(self, *_args):
                        raise AssertionError("duplicate_thread_start")

                self.assertEqual(self._create(
                    RejectClient(), startup, epoch), created)
                with self.assertRaisesRegex(EventJournalError,
                                            "thread_start_conflict"):
                    self._create(RejectClient(), startup, epoch,
                                 request_id="different-request")
            with LockedHostJournals(folder) as reopened:
                next_epoch = reopened.recover_host_after_isolation(
                    old_process_isolated=True, old_process_epoch=epoch,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:01Z")["epoch"]
                self.assertEqual(self._create(
                    RejectClient(), reopened, next_epoch), created)
                self.assertEqual(reopened.events.get_session_thread("session"),
                                 "thread_demo")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_created_upstream_but_lost_reply_never_retries(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            marker = folder / "created.txt"
            with LockedHostJournals(folder) as startup:
                epoch = self._ready(startup)
                with StdioClient(
                        [sys.executable, str(PEER),
                         "thread_bootstrap_lost", str(marker)],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    unknown = self._create(client, startup, epoch)
                    self.assertEqual(unknown["status"], "unknown")
                    with self.assertRaisesRegex(TransportError,
                                                "connection_unusable"):
                        client.next_event(0.1)
                self.assertEqual(marker.read_text(encoding="utf-8"),
                                 "created\n")
                with self.assertRaisesRegex(EventJournalError,
                                            "session_thread_not_bound"):
                    startup.events.get_session_thread("session")
                with self.assertRaisesRegex(EventJournalError,
                                            "session_thread_conflict"):
                    startup.events.bind_session_thread("session", "thread_demo")
                class RejectClient:
                    def request(self, *_args):
                        raise AssertionError("duplicate_thread_start")

                self.assertEqual(self._create(
                    RejectClient(), startup, epoch), unknown)
            with LockedHostJournals(folder) as reopened:
                next_epoch = reopened.recover_host_after_isolation(
                    old_process_isolated=True, old_process_epoch=epoch,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:01Z")["epoch"]
                self.assertEqual(self._create(
                    RejectClient(), reopened, next_epoch)["status"], "unknown")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_claim_or_binding_failure_never_retries_send(self):
        class RejectClient:
            def request(self, *_args):
                raise AssertionError("thread_start_sent")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_thread_claim BEFORE UPDATE ON thread_starts
                            WHEN NEW.status='dispatching'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(sqlite3.IntegrityError):
                    self._create(RejectClient(), startup, epoch)
                db.execute("DROP TRIGGER block_thread_claim")
                self.assertEqual(db.execute(
                    "SELECT status FROM thread_starts").fetchone()[0], "accepted")
                db.execute("""CREATE TRIGGER block_thread_binding BEFORE INSERT ON session_threads
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                class RespondingClient:
                    def __init__(self):
                        self.calls = 0
                        self.closed = False

                    def request(self, *_args):
                        self.calls += 1
                        return {"thread": {"id": "thread_demo"}}

                    def close(self):
                        self.closed = True

                client = RespondingClient()
                result = self._create(client, startup, epoch)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(client.calls, 1)
                self.assertTrue(client.closed)
                self.assertIsNone(db.execute(
                    "SELECT thread_id FROM session_threads").fetchone())

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_recovery_marks_claimed_unknown_but_keeps_unclaimed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            with LockedHostJournals(folder) as startup:
                epoch = self._ready(startup)
                startup.events.create_session("second", {})
                digest = b"a" * 32
                startup.reserve_thread_start(
                    session_id="session", actor_id="local", request_id="a",
                    request_digest=digest, epoch=epoch)
                startup.claim_thread_start(
                    session_id="session", actor_id="local", request_id="a",
                    epoch=epoch)
                startup.reserve_thread_start(
                    session_id="second", actor_id="local", request_id="b",
                    request_digest=digest, epoch=epoch)
            with LockedHostJournals(folder) as reopened:
                next_epoch = reopened.recover_host_after_isolation(
                    old_process_isolated=True, old_process_epoch=epoch,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:01Z")["epoch"]
                rows = {row["session_id"]: row["status"] for row in
                        reopened.events.connection.execute(
                            "SELECT session_id, status FROM thread_starts")}
                self.assertEqual(rows, {"session": "unknown", "second": "accepted"})
                with self.assertRaisesRegex(EventJournalError,
                                            "thread_start_unavailable"):
                    reopened.claim_thread_start(
                        session_id="session", actor_id="local", request_id="a",
                        epoch=next_epoch)
                self.assertEqual(reopened.claim_thread_start(
                    session_id="second", actor_id="local", request_id="b",
                    epoch=next_epoch)["status"], "dispatching")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_bad_response_and_local_path_rejections(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                with self.assertRaisesRegex(EventJournalError,
                                            "invalid_local_workspace"):
                    startup.register_workspace("relative", Path("relative"),
                                               sandbox="read-only", epoch=epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "invalid_workspace_registration"):
                    startup.register_workspace("bad", ROOT,
                                               sandbox="danger", epoch=epoch)
                with StdioClient(
                        [sys.executable, str(PEER), "thread_bootstrap_bad_shape"],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    result = self._create(client, startup, epoch)
                    self.assertEqual(result["status"], "unknown")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_registered_workspace_is_immutable_and_required(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                self.assertEqual(startup.register_workspace(
                    "workspace", ROOT, sandbox="read-only", epoch=epoch),
                    startup.get_session_workspace("session", epoch=epoch))
                with self.assertRaisesRegex(EventJournalError,
                                            "workspace_conflict"):
                    startup.register_workspace("workspace", ROOT,
                                               sandbox="workspace-write",
                                               epoch=epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "workspace_conflict"):
                    startup.register_workspace("other-id", ROOT,
                                               sandbox="read-only", epoch=epoch)
                startup.events.create_session("legacy", {})
                with self.assertRaisesRegex(EventJournalError,
                                            "session_workspace_not_registered"):
                    create_session_thread_once(
                        None, startup, session_id="legacy", actor_id="local",
                        request_id="legacy-create", epoch=epoch)
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT 1 FROM thread_starts WHERE session_id='legacy'").fetchone())

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_local_session_creation_rolls_back_on_workspace_link_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_workspace_link
                            BEFORE INSERT ON session_workspaces
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.create_session_in_workspace(
                        "new", "workspace", {}, epoch=epoch)
                self.assertIsNone(db.execute(
                    "SELECT 1 FROM sessions WHERE session_id='new'").fetchone())
                db.execute("DROP TRIGGER block_workspace_link")
                with self.assertRaisesRegex(EventJournalError,
                                            "workspace_not_registered"):
                    startup.create_session_in_workspace(
                        "new", "missing", {}, epoch=epoch)

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_thread_start_reads_policy_from_registration_and_rechecks_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            workspace = folder / "local-workspace"
            workspace.mkdir()
            with LockedHostJournals(folder) as startup:
                epoch = self._ready(startup)
                startup.register_workspace("writable", workspace,
                                           sandbox="workspace-write", epoch=epoch)
                startup.create_session_in_workspace("new", "writable", {},
                                                    epoch=epoch)
                class RecordingClient:
                    def __init__(self):
                        self.params = None

                    def request(self, method, params):
                        self.params = (method, params)
                        return {"thread": {"id": "new-thread"}}

                client = RecordingClient()
                result = create_session_thread_once(
                    client, startup, session_id="new", actor_id="local",
                    request_id="new-create", epoch=epoch)
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(client.params[0], "thread/start")
                self.assertEqual(client.params[1]["cwd"], str(workspace))
                self.assertEqual(client.params[1]["sandbox"], "workspace-write")
                startup.create_session_in_workspace("later", "writable", {},
                                                    epoch=epoch)
                workspace.rmdir()
                with self.assertRaisesRegex(ThreadStartError,
                                            "invalid_local_workspace"):
                    create_session_thread_once(
                        client, startup, session_id="later", actor_id="local",
                        request_id="later-create", epoch=epoch)
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT 1 FROM thread_starts WHERE session_id='later'").fetchone())

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_recovery_failure_rolls_back_thread_start_unknown(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                startup.reserve_thread_start(
                    session_id="session", actor_id="local", request_id="a",
                    request_digest=b"a" * 32, epoch=epoch)
                startup.claim_thread_start(
                    session_id="session", actor_id="local", request_id="a",
                    epoch=epoch)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_epoch BEFORE UPDATE ON journal_meta
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                args = dict(old_process_isolated=True, old_process_epoch=epoch,
                            expected_run_id=None, expected_run_version=None,
                            reason="host_restart",
                            recorded_at="2026-10-25T00:00:01Z")
                with self.assertRaises(sqlite3.IntegrityError):
                    startup.recover_host_after_isolation(**args)
                self.assertEqual(db.execute(
                    "SELECT status FROM thread_starts").fetchone()[0],
                    "dispatching")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                db.execute("DROP TRIGGER block_epoch")
                self.assertEqual(startup.recover_host_after_isolation(
                    **args)["epoch"], epoch + 1)
                self.assertEqual(db.execute(
                    "SELECT status FROM thread_starts").fetchone()[0],
                    "unknown")


if __name__ == "__main__":
    unittest.main()
