"""Close a managed upstream before advancing the Host recovery epoch."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runvori.cli import ROOT
from runvori.codex.stdio import TransportError
from runvori.codex.upstream_lifecycle import (
    OwnedUpstream, UpstreamLifecycleError, isolate_and_recover_upstream,
    launch_owned_upstream,
)
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


@unittest.skipUnless(os.name == "nt", "Windows Job ownership")
class UpstreamLifecycleTests(unittest.TestCase):
    @staticmethod
    def _ready(startup):
        return startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart",
            recorded_at="2026-10-25T00:00:00Z")["epoch"]

    @staticmethod
    def _owner(startup, epoch):
        return launch_owned_upstream(
            startup, epoch=epoch,
            command=[sys.executable, str(PEER), "thread_bootstrap_good"],
            cwd=ROOT, env=dict(os.environ))

    def test_closes_job_then_recovers_active_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                startup.events.create_session("session", {})
                startup.events.reserve_run_request(
                    "session", "run", actor_id="local", request_id="start",
                    expected_session_version=0, input_text="hello",
                    recorded_at="2026-10-25T00:00:01Z")
                owner = self._owner(startup, epoch)
                result = isolate_and_recover_upstream(
                    owner, recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertIsNotNone(owner.client.process.poll())
                self.assertIsNone(owner.client._job.handle)
                self.assertEqual(startup.events.connection.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "interrupted")
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT run_id FROM active_slot").fetchone())
                replacement = self._owner(startup, epoch + 1)
                self.assertIsNone(replacement.client.process.poll())

    def test_close_failure_keeps_write_gate_closed_and_epoch_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                owner = self._owner(startup, epoch)
                with patch.object(owner.client, "close",
                                  side_effect=TransportError("close_failed")):
                    with self.assertRaisesRegex(
                            UpstreamLifecycleError,
                            "upstream_isolation_unconfirmed"):
                        isolate_and_recover_upstream(
                            owner, recorded_at="2026-10-25T00:00:01Z")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "host_not_recovered"):
                    startup.events.create_session("blocked", {})

    def test_stale_epoch_does_not_launch_connection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    self._owner(startup, epoch + 1)
                startup.events.create_session("still-open", {})

    def test_duplicate_launch_rejected_and_journal_close_isolates_owner(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                owner = self._owner(startup, epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_already_owned"):
                    self._owner(startup, epoch)
                forged = OwnedUpstream(startup, epoch)
                forged.client = owner.client
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_owner_mismatch"):
                    isolate_and_recover_upstream(
                        forged, recorded_at="2026-10-25T00:00:01Z")
                self.assertIsNone(owner.client.process.poll())
            self.assertIsNotNone(owner.client.process.poll())
            self.assertIsNone(owner.client._job.handle)

    def test_initialize_failure_releases_slot_after_process_isolation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                with patch("runvori.codex.upstream_lifecycle.StdioClient.initialize",
                           side_effect=TransportError("init_failed")):
                    with self.assertRaisesRegex(TransportError, "init_failed"):
                        self._owner(startup, epoch)
                owner = self._owner(startup, epoch)
                self.assertIsNone(owner.client.process.poll())

    def test_direct_recovery_cannot_advance_while_owned_process_is_live(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                owner = self._owner(startup, epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_isolation_unconfirmed"):
                    startup.recover_host_after_isolation(
                        old_process_isolated=True, old_process_epoch=epoch,
                        expected_run_id=None, expected_run_version=None,
                        reason="upstream_exit",
                        recorded_at="2026-10-25T00:00:01Z")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "host_not_recovered"):
                    startup.events.create_session("blocked", {})
                self.assertIsNone(owner.client.process.poll())


if __name__ == "__main__":
    unittest.main()
