"""End-to-end HTTPS client loop against a real HostRuntime (synthetic upstream).

The client is independent of the product packages: it speaks only the /v1
protocol over a pinned TLS connection. Local administration uses the pipe.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from runvori.cli import ROOT


PROBE = Path(__file__).resolve().parents[2] / "scripts/probe_https_host.py"


class HttpsHostFlowTests(unittest.TestCase):
    def test_end_to_end_https_loop(self):
        if os.name != "nt":
            self.skipTest("Windows named pipe and Host lock required")
        result = subprocess.run(
            [sys.executable, str(PROBE)], cwd=ROOT, capture_output=True,
            text=True, timeout=300, check=False)
        try:
            report = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            self.fail("unreadable probe report: "
                      f"{result.stdout[-500:]!r} {result.stderr[-500:]!r}")
        if report.get("status") == "skipped":
            self.skipTest(report.get("code", "probe_skipped"))
        self.assertEqual(report.get("status"), "passed", report)
        steps = {step["step"] for step in report["steps"]}
        for expected in ("tls_pin_mismatch", "local_admin", "auth_gate",
                         "pairing", "pairing_replay", "rate_limit",
                         "host_view", "snapshot", "run_accepted",
                         "approval_detail", "decision_accepted",
                         "run_completed", "resync", "insufficient_review",
                         "revocation"):
            self.assertIn(expected, steps)
        detail = next(step for step in report["steps"]
                      if step["step"] == "approval_detail")
        self.assertEqual(detail["details_status"], "complete")
        self.assertTrue(detail["command_present"])
        self.assertTrue(detail["cwd_inside_workspace"])
        resync = next(step for step in report["steps"]
                      if step["step"] == "resync")
        self.assertTrue(resync["continuous"])
        self.assertEqual(resync["duplicate_count"], 0)
        insufficient = next(step for step in report["steps"]
                            if step["step"] == "insufficient_review")
        self.assertTrue(insufficient["allow_blocked"])
        self.assertEqual(insufficient["allow_status"], 409)
        self.assertEqual(insufficient["deny_status"], 202)
        # The failure text must not include a token, secret or absolute path.
        text = json.dumps(report)
        self.assertNotIn("\\\\Users\\\\", text)
        self.assertNotIn("device_token", text)


if __name__ == "__main__":
    unittest.main()