"""Behavioral proof of one-time pairing under replay and DB contention."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.pairing_journal import PairingError, PairingJournal


class PairingJournalTests(unittest.TestCase):
    def test_committed_exchange_cannot_be_replayed_after_response_loss(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "pairing.db"
            now = [1000]
            with PairingJournal(database, clock=lambda: now[0]) as journal:
                secret, expiry = journal.issue_local_secret()
                self.assertEqual(len(secret), 32)
                self.assertEqual(expiry, 1300)
                stored = journal.connection.execute(
                    "SELECT secret_digest FROM pairing_challenge").fetchone()[0]
                self.assertNotEqual(stored, secret)
                device_id, token = journal.exchange(secret)
                self.assertEqual(len(device_id), 32)
                self.assertEqual(len(token), 32)
                self.assertTrue(journal.authenticates(token))
                stored_token = journal.connection.execute(
                    "SELECT token_digest FROM paired_devices WHERE active = 1").fetchone()[0]
                self.assertNotEqual(stored_token, token)
            with PairingJournal(database, clock=lambda: now[0]) as reopened:
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    reopened.exchange(secret)
                self.assertTrue(reopened.authenticates(token))

    def test_expiry_and_new_local_issue_invalidate_old_secret(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            now = [1000]
            with PairingJournal(Path(temp) / "pairing.db", clock=lambda: now[0]) as journal:
                first, _ = journal.issue_local_secret()
                second, _ = journal.issue_local_secret()
                self.assertNotEqual(first, second)
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    journal.exchange(first)
                now[0] = 1300
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    journal.exchange(second)

    def test_two_connections_can_issue_only_one_device(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "pairing.db"
            with PairingJournal(database, clock=lambda: 1000) as journal:
                secret, _ = journal.issue_local_secret()

            def exchange():
                with PairingJournal(database, clock=lambda: 1000) as journal:
                    try:
                        return journal.exchange(secret)
                    except PairingError as error:
                        return str(error)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: exchange(), range(2)))
            self.assertEqual(sum(isinstance(item, tuple) for item in results), 1)
            self.assertEqual(results.count("pairing_unavailable"), 1)
            with PairingJournal(database, clock=lambda: 1000) as journal:
                self.assertEqual(journal.connection.execute(
                    "SELECT COUNT(*) FROM paired_devices WHERE active = 1").fetchone()[0], 1)

    def test_local_revoke_invalidates_token_before_repairing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with PairingJournal(Path(temp) / "pairing.db", clock=lambda: 1000) as journal:
                secret, _ = journal.issue_local_secret()
                _device_id, token = journal.exchange(secret)
                with self.assertRaisesRegex(PairingError, "device_already_paired"):
                    journal.issue_local_secret()
                journal.revoke_local_device()
                self.assertFalse(journal.authenticates(token))
                replacement, _ = journal.issue_local_secret()
                _new_device, new_token = journal.exchange(replacement)
                self.assertTrue(journal.authenticates(new_token))
                self.assertFalse(journal.authenticates(token))


if __name__ == "__main__":
    unittest.main()
