"""Offline revocation uses the same lock as the future Host."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.instance_lock import WindowsInstanceLock
from runvori.infrastructure.pairing_journal import PairingJournal

HOST_ID = "host_" + "1" * 32


@unittest.skipUnless(os.name == "nt", "Windows Host instance lock")
class LocalDeviceAdminTests(unittest.TestCase):
    @staticmethod
    def _invoke(data_dir: Path):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/runvori.py"),
             "revoke-device", "--data-dir", str(data_dir)],
            cwd=ROOT, text=True, capture_output=True, timeout=10)
        return result.returncode, json.loads(result.stdout)

    def test_cli_revokes_existing_device_without_codex(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            directory = Path(temp)
            with PairingJournal(directory / "pairing.db") as journal:
                secret, _ = journal.issue_local_secret(host_id=HOST_ID)
                _, token = journal.exchange(secret, host_id=HOST_ID)
            code, output = self._invoke(directory)
            self.assertEqual(code, 0)
            self.assertEqual(output, {"status": "passed", "device": "revoked",
                                      "mode": "offline"})
            # Without a running Host the CLI must say the revocation was
            # offline: in-flight polls are not cancelled in that mode.
            self.assertIn("offline", output["mode"])
            self.assertNotIn(token.hex(), json.dumps(output))
            with PairingJournal(directory / "pairing.db") as journal:
                self.assertFalse(journal.authenticates(token, host_id=HOST_ID))

    def test_live_host_lock_refuses_without_changing_pairing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            directory = Path(temp)
            with PairingJournal(directory / "pairing.db") as journal:
                secret, _ = journal.issue_local_secret(host_id=HOST_ID)
                _, token = journal.exchange(secret, host_id=HOST_ID)
            with WindowsInstanceLock(directory / "host.lock"):
                code, output = self._invoke(directory)
                self.assertEqual(code, 1)
                self.assertEqual(output, {
                    "status": "failed", "code": "instance_lock_unavailable"})
                with PairingJournal(directory / "pairing.db") as journal:
                    self.assertTrue(journal.authenticates(token, host_id=HOST_ID))

    def test_missing_pairing_store_is_not_created(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            directory = Path(temp)
            code, output = self._invoke(directory)
            self.assertEqual(code, 1)
            self.assertEqual(output, {
                "status": "failed", "code": "pairing_store_not_found"})
            self.assertFalse((directory / "pairing.db").exists())


if __name__ == "__main__":
    unittest.main()
