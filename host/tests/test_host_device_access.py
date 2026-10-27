"""Recovered Host identity is the sole source for device pairing and reads."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from runvori.application.command_mailbox import HostCommandMailbox
from runvori.application.device_reads import DeviceReadError
from runvori.application.host_device_access import (
    HostDeviceAccess, HostDeviceAccessError,
)
from runvori.cli import ROOT
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals
from runvori.infrastructure.pairing_journal import PairingError, PairingJournal


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class HostDeviceAccessTests(unittest.TestCase):
    @staticmethod
    def _recover(journals):
        journals.recover_host_after_isolation(
            old_process_epoch=journals.approvals.current_epoch(),
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-26T00:00:00Z")

    def test_unrecovered_host_cannot_create_pairing_or_read_gateway(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            directory = Path(temp)
            with LockedHostJournals(directory) as journals:
                with self.assertRaisesRegex(EventJournalError, "host_not_recovered"):
                    HostDeviceAccess(journals, HostCommandMailbox())
                self.assertFalse((directory / "pairing.db").exists())

    def test_recovered_identity_pairs_and_reads_through_owner_mailbox(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as journals:
                self._recover(journals)
                journals.events.create_session("session", {})
                mailbox = HostCommandMailbox()
                access = HostDeviceAccess(journals, mailbox)
                self.assertEqual(access.host_id, journals.events.get_host_id())
                secret, _ = access.issue_local_secret()
                _, token = access.exchange(secret)
                outcome = {}

                def read():
                    outcome["snapshot"] = access.reads.snapshot(
                        token=token, session_id="session")

                worker = threading.Thread(target=read)
                worker.start()
                deadline = time.monotonic() + 2
                while not mailbox.serve_once(
                        journals, recorded_at="2026-10-26T00:00:01Z",
                        run_starts=None, interrupts=None):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.005)
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(outcome["snapshot"]["session_id"], "session")
                access.revoke_local_device()
                with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                    access.reads.snapshot(token=token, session_id="session")

    def test_rebuilt_host_rejects_copied_old_pairing_store(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            source = Path(temp) / "source"
            rebuilt = Path(temp) / "rebuilt"
            source.mkdir()
            rebuilt.mkdir()
            with LockedHostJournals(source) as journals:
                self._recover(journals)
                original = HostDeviceAccess(journals, HostCommandMailbox())
                secret, _ = original.issue_local_secret()
                _, old_token = original.exchange(secret)
                old_id = original.host_id
            shutil.copy2(source / "pairing.db", rebuilt / "pairing.db")
            with LockedHostJournals(rebuilt) as journals:
                self._recover(journals)
                mailbox = HostCommandMailbox()
                current = HostDeviceAccess(journals, mailbox)
                self.assertNotEqual(current.host_id, old_id)
                with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                    current.reads.snapshot(token=old_token, session_id="session")
                self.assertTrue(mailbox._items.empty())
                with self.assertRaisesRegex(PairingError, "device_already_paired"):
                    current.issue_local_secret()
                current.revoke_local_device()
                replacement, _ = current.issue_local_secret()
                _, fresh_token = current.exchange(replacement)
                with PairingJournal(rebuilt / "pairing.db") as pairing:
                    self.assertTrue(pairing.authenticates(
                        fresh_token, host_id=current.host_id))

    def test_closed_host_access_cannot_issue_or_use_old_credentials(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as journals:
                self._recover(journals)
                access = HostDeviceAccess(journals, HostCommandMailbox())
                secret, _ = access.issue_local_secret()
                _, token = access.exchange(secret)
            with self.assertRaisesRegex(HostDeviceAccessError, "host_unavailable"):
                access.issue_local_secret()
            with self.assertRaisesRegex(HostDeviceAccessError, "host_unavailable"):
                access.revoke_local_device()
            with self.assertRaisesRegex(DeviceReadError, "host_unavailable"):
                access.reads.snapshot(token=token, session_id="session")
            with journals:
                self._recover(journals)
                with self.assertRaisesRegex(HostDeviceAccessError, "host_unavailable"):
                    access.issue_local_secret()


if __name__ == "__main__":
    unittest.main()
