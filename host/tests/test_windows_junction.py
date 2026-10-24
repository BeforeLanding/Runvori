"""Real Windows junction boundary check for the two local file readers."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.approval_journal import ApprovalJournal
from runvori.infrastructure.artifact_probe import capture_worktree_file


@unittest.skipUnless(sys.platform == "win32", "Windows junction test")
class WindowsJunctionTests(unittest.TestCase):
    def test_junction_parent_is_rejected_before_file_read(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            workspace = folder / "workspace"
            workspace.mkdir()
            outside = folder / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_bytes(b"secret")
            linked = workspace / "linked"
            environment = dict(os.environ,
                               RUNVORI_TEST_LINK=str(linked),
                               RUNVORI_TEST_TARGET=str(outside))
            created = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference='Stop'; "
                 "New-Item -ItemType Junction -Path $env:RUNVORI_TEST_LINK "
                 "-Target $env:RUNVORI_TEST_TARGET | Out-Null"],
                capture_output=True, text=True, timeout=15, env=environment,
                check=False)
            if created.returncode != 0:
                self.skipTest("junction creation unavailable on this host")
            self.assertTrue(linked.is_junction())
            try:
                with self.assertRaisesRegex(ValueError, "artifact_link_not_allowed"):
                    capture_worktree_file(workspace, "linked/secret.txt")

                with ApprovalJournal(folder / "journal.db") as journal:
                    epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                    journal.record_request(approval_id="a", epoch=epoch,
                                           upstream_request_id="upstream_a", kind="file_change")
                    journal.record_decision(approval_id="a", request_id="req_a",
                                            decision="accept", expected_version=1)
                    journal.claim_dispatch(approval_id="a", epoch=epoch)
                    journal.start_epoch_after_isolation(old_process_isolated=True)
                    result = journal.inspect_unknown_file(
                        approval_id="a", workspace=workspace,
                        relative_path="linked/secret.txt", expected_bytes=b"secret")
                    self.assertEqual(result["file_snapshot"], "unsafe_path")
                self.assertEqual((outside / "secret.txt").read_bytes(), b"secret")
            finally:
                linked.rmdir()


if __name__ == "__main__":
    unittest.main()
