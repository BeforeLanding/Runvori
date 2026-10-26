"""Behavioral proof of one-time pairing under replay and DB contention."""

import tempfile
import unittest
import hashlib
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.pairing_journal import PairingError, PairingJournal

HOST_ID = "host_" + "1" * 32
OTHER_HOST_ID = "host_" + "2" * 32


class PairingJournalTests(unittest.TestCase):
    def test_secret_and_token_are_bound_to_host_identity(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with PairingJournal(Path(temp) / "pairing.db", clock=lambda: 1000) as journal:
                with self.assertRaisesRegex(PairingError, "invalid_host_id"):
                    journal.issue_local_secret(host_id="wrong")
                secret, _ = journal.issue_local_secret(host_id=HOST_ID)
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    journal.exchange(secret, host_id=OTHER_HOST_ID)
                _, token = journal.exchange(secret, host_id=HOST_ID)
                self.assertTrue(journal.authenticates(token, host_id=HOST_ID))
                self.assertFalse(journal.authenticates(token, host_id=OTHER_HOST_ID))

    def test_legacy_unbound_credentials_are_invalidated_on_upgrade(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "pairing.db"
            legacy_secret = b"s" * 32
            legacy_token = b"t" * 32
            with closing(sqlite3.connect(database)) as db:
                db.executescript("""
                    CREATE TABLE pairing_challenge (
                        singleton INTEGER PRIMARY KEY, secret_digest BLOB,
                        expires_at INTEGER, consumed INTEGER);
                    CREATE TABLE paired_devices (
                        device_id TEXT PRIMARY KEY, token_digest BLOB,
                        active INTEGER);
                """)
                db.execute("INSERT INTO pairing_challenge VALUES (1, ?, 1300, 0)",
                           (hashlib.sha256(legacy_secret).digest(),))
                db.execute("INSERT INTO paired_devices VALUES ('legacy', ?, 1)",
                           (hashlib.sha256(legacy_token).digest(),))
                db.commit()
            with PairingJournal(database, clock=lambda: 1000) as upgraded:
                self.assertFalse(upgraded.authenticates(
                    legacy_token, host_id=HOST_ID))
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    upgraded.exchange(legacy_secret, host_id=HOST_ID)
                secret, _ = upgraded.issue_local_secret(host_id=HOST_ID)
                _, token = upgraded.exchange(secret, host_id=HOST_ID)
                self.assertTrue(upgraded.authenticates(token, host_id=HOST_ID))

    def test_failed_legacy_invalidation_rolls_back_and_can_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "pairing.db"
            with closing(sqlite3.connect(database)) as db:
                db.executescript("""
                    CREATE TABLE pairing_challenge (
                        singleton INTEGER PRIMARY KEY, secret_digest BLOB,
                        expires_at INTEGER, consumed INTEGER);
                    CREATE TABLE paired_devices (
                        device_id TEXT PRIMARY KEY, token_digest BLOB,
                        active INTEGER);
                    INSERT INTO pairing_challenge VALUES (1, X'00', 1300, 0);
                    INSERT INTO paired_devices VALUES ('legacy', X'00', 1);
                    CREATE TRIGGER block_revoke BEFORE UPDATE ON paired_devices
                    BEGIN SELECT RAISE(ABORT, 'blocked'); END;
                """)
            with self.assertRaises(sqlite3.IntegrityError):
                PairingJournal(database)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute(
                    "SELECT active FROM paired_devices").fetchone()[0], 1)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM pairing_challenge").fetchone()[0], 1)
                db.execute("DROP TRIGGER block_revoke")
                db.commit()
            with PairingJournal(database) as journal:
                self.assertEqual(journal.connection.execute(
                    "SELECT active FROM paired_devices").fetchone()[0], 0)
                self.assertIsNone(journal.connection.execute(
                    "SELECT 1 FROM pairing_challenge").fetchone())

    def test_committed_exchange_cannot_be_replayed_after_response_loss(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "pairing.db"
            now = [1000]
            with PairingJournal(database, clock=lambda: now[0]) as journal:
                secret, expiry = journal.issue_local_secret(host_id=HOST_ID)
                self.assertEqual(len(secret), 32)
                self.assertEqual(expiry, 1300)
                stored = journal.connection.execute(
                    "SELECT secret_digest FROM pairing_challenge").fetchone()[0]
                self.assertNotEqual(stored, secret)
                device_id, token = journal.exchange(secret, host_id=HOST_ID)
                self.assertEqual(len(device_id), 32)
                self.assertEqual(len(token), 32)
                self.assertTrue(journal.authenticates(token, host_id=HOST_ID))
                self.assertFalse(journal.authenticates(token, host_id=OTHER_HOST_ID))
                stored_token = journal.connection.execute(
                    "SELECT token_digest FROM paired_devices WHERE active = 1").fetchone()[0]
                self.assertNotEqual(stored_token, token)
            with PairingJournal(database, clock=lambda: now[0]) as reopened:
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    reopened.exchange(secret, host_id=HOST_ID)
                self.assertTrue(reopened.authenticates(token, host_id=HOST_ID))

    def test_expiry_and_new_local_issue_invalidate_old_secret(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            now = [1000]
            with PairingJournal(Path(temp) / "pairing.db", clock=lambda: now[0]) as journal:
                first, _ = journal.issue_local_secret(host_id=HOST_ID)
                second, _ = journal.issue_local_secret(host_id=HOST_ID)
                self.assertNotEqual(first, second)
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    journal.exchange(first, host_id=HOST_ID)
                now[0] = 1300
                with self.assertRaisesRegex(PairingError, "pairing_unavailable"):
                    journal.exchange(second, host_id=HOST_ID)

    def test_two_connections_can_issue_only_one_device(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "pairing.db"
            with PairingJournal(database, clock=lambda: 1000) as journal:
                secret, _ = journal.issue_local_secret(host_id=HOST_ID)

            def exchange():
                with PairingJournal(database, clock=lambda: 1000) as journal:
                    try:
                        return journal.exchange(secret, host_id=HOST_ID)
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
                secret, _ = journal.issue_local_secret(host_id=HOST_ID)
                _device_id, token = journal.exchange(secret, host_id=HOST_ID)
                with self.assertRaisesRegex(PairingError, "device_already_paired"):
                    journal.issue_local_secret(host_id=HOST_ID)
                journal.revoke_local_device()
                self.assertFalse(journal.authenticates(token, host_id=HOST_ID))
                replacement, _ = journal.issue_local_secret(host_id=HOST_ID)
                _new_device, new_token = journal.exchange(replacement, host_id=HOST_ID)
                self.assertTrue(journal.authenticates(new_token, host_id=HOST_ID))
                self.assertFalse(journal.authenticates(token, host_id=HOST_ID))


if __name__ == "__main__":
    unittest.main()
