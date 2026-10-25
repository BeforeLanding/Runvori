"""Resume a persisted thread only after Host epoch recovery."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.event_handoff import process_next_event
from runvori.codex.stdio import StdioClient, TransportError
from runvori.codex.thread_resume import (
    ThreadResumeError, resume_session_thread,
)
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class ThreadResumeTests(unittest.TestCase):
    @staticmethod
    def _create_bound_session(folder):
        with LockedHostJournals(folder) as startup:
            epoch = startup.recover_host_after_isolation(
                old_process_isolated=True, old_process_epoch=0,
                expected_run_id=None, expected_run_version=None,
                reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
            startup.events.create_session("session", {})
            startup.events.bind_session_thread("session", "thread_demo")
            return epoch

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_reopen_requires_recovery_then_verifies_exact_thread(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            old_epoch = self._create_bound_session(folder)
            with LockedHostJournals(folder) as startup:
                class RejectClient:
                    def request(self, *_args):
                        raise AssertionError("resume_before_recovery")

                with self.assertRaisesRegex(EventJournalError,
                                            "host_not_recovered"):
                    resume_session_thread(
                        RejectClient(), startup, session_id="session",
                        epoch=old_epoch)
                epoch = startup.recover_host_after_isolation(
                    old_process_isolated=True, old_process_epoch=old_epoch,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:01Z")["epoch"]
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    resume_session_thread(
                        RejectClient(), startup, session_id="session",
                        epoch=old_epoch)
                with StdioClient(
                        [sys.executable, str(PEER), "thread_resume_good"],
                        cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    self.assertEqual(resume_session_thread(
                        client, startup, session_id="session", epoch=epoch),
                        {"session_id": "session", "thread_id": "thread_demo",
                         "epoch": epoch})
                    self.assertEqual(process_next_event(
                        client, startup, epoch=epoch,
                        recorded_at="2026-10-25T00:00:02Z", timeout=1)["kind"],
                        "ignored_notification")
                self.assertEqual(startup.events.get_session_thread("session"),
                                 "thread_demo")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_wrong_identity_or_missing_reply_closes_connection(self):
        for mode in ("thread_resume_wrong", "thread_resume_no_response"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    folder = Path(temp)
                    epoch = self._create_bound_session(folder)
                    with LockedHostJournals(folder) as startup:
                        next_epoch = startup.recover_host_after_isolation(
                            old_process_isolated=True, old_process_epoch=epoch,
                            expected_run_id=None, expected_run_version=None,
                            reason="host_restart",
                            recorded_at="2026-10-25T00:00:01Z")["epoch"]
                        with StdioClient([sys.executable, str(PEER), mode],
                                         cwd=ROOT, env=dict(os.environ)) as client:
                            client.initialize()
                            with self.assertRaisesRegex(ThreadResumeError,
                                                        "thread_resume_unconfirmed"):
                                resume_session_thread(
                                    client, startup, session_id="session",
                                    epoch=next_epoch)
                            with self.assertRaisesRegex(TransportError,
                                                        "connection_unusable"):
                                client.next_event(0.1)
                        self.assertEqual(startup.events.get_session_thread(
                            "session"), "thread_demo")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_unbound_session_never_calls_upstream(self):
        class RejectClient:
            def request(self, *_args):
                raise AssertionError("resume_without_binding")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = startup.recover_host_after_isolation(
                    old_process_isolated=True, old_process_epoch=0,
                    expected_run_id=None, expected_run_version=None,
                    reason="host_restart",
                    recorded_at="2026-10-25T00:00:00Z")["epoch"]
                startup.events.create_session("session", {})
                with self.assertRaisesRegex(EventJournalError,
                                            "session_thread_not_bound"):
                    resume_session_thread(
                        RejectClient(), startup, session_id="session",
                        epoch=epoch)


if __name__ == "__main__":
    unittest.main()
