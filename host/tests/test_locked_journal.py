"""Real process startup ordering for the M0 locked approval journal slice."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runvori.cli import ROOT
from runvori.infrastructure.instance_lock import InstanceLockUnavailable, WindowsInstanceLock
from runvori.infrastructure.locked_journal import LockedApprovalJournal


PEER = Path(__file__).with_name("locked_journal_peer.py")


@unittest.skipUnless(os.name == "nt", "Windows startup lock")
class LockedJournalTests(unittest.TestCase):
    def test_contended_startup_never_opens_sqlite(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with WindowsInstanceLock(data_dir / "host.lock"):
                with patch("runvori.infrastructure.locked_journal.ApprovalJournal",
                           side_effect=AssertionError("sqlite_opened_before_lock")):
                    with self.assertRaisesRegex(InstanceLockUnavailable,
                                                "instance_lock_unavailable"):
                        LockedApprovalJournal(data_dir).open()
            self.assertFalse((data_dir / "approvals.db").exists())

    def test_second_process_cannot_start_and_crash_does_not_advance_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            marker = data_dir / "owner.ready"
            env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
            owner = subprocess.Popen([sys.executable, str(PEER), str(data_dir), str(marker)],
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
                    LockedApprovalJournal(data_dir).open()
                owner.kill()
                owner.wait(timeout=5)
                with LockedApprovalJournal(data_dir) as restarted:
                    self.assertEqual(restarted.journal.current_epoch(), 0)
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)

    def test_database_open_failure_releases_startup_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            bad_database = data_dir / "approvals.db"
            bad_database.mkdir()
            with self.assertRaises(sqlite3.OperationalError):
                LockedApprovalJournal(data_dir).open()
            bad_database.rmdir()
            with LockedApprovalJournal(data_dir) as startup:
                self.assertEqual(startup.journal.current_epoch(), 0)


if __name__ == "__main__":
    unittest.main()
