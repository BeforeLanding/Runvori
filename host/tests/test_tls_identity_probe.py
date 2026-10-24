"""Local TLS handshake and SPKI rotation probe; requires the OpenSSL CLI."""

import json
import shutil
import subprocess
import sys
import unittest

from runvori.cli import ROOT


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI unavailable")
class TlsIdentityProbeTests(unittest.TestCase):
    def test_real_loopback_handshakes_reject_wrong_identity(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/probe_tls_identity.py")],
                                cwd=ROOT, capture_output=True, text=True, timeout=60,
                                check=False)
        report = json.loads(result.stdout)
        if report.get("code") == "loopback_unavailable":
            self.skipTest("loopback socket connection unavailable in this environment")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["rotated_pin"], "rejected_before_data")
        self.assertEqual(report["untrusted_cert"], "rejected")


if __name__ == "__main__":
    unittest.main()
