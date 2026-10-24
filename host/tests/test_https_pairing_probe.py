"""Real loopback HTTPS exchange from pin check through one-time commit."""

import json
import shutil
import subprocess
import sys
import unittest

from runvori.cli import ROOT


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL CLI unavailable")
class HttpsPairingProbeTests(unittest.TestCase):
    def test_pinned_exchange_and_lost_response_replay(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/probe_https_pairing.py")],
                                cwd=ROOT, capture_output=True, text=True, timeout=60,
                                check=False)
        report = json.loads(result.stdout)
        if report.get("code") == "loopback_unavailable":
            self.skipTest("loopback socket connection unavailable in this environment")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(report["wrong_pin"], "no_http_request")
        self.assertEqual(report["replay"], "rejected")


if __name__ == "__main__":
    unittest.main()
