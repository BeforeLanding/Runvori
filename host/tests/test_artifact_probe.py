import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.artifact_probe import CHUNK_SIZE, SCAN_LIMIT, capture_worktree_file


class ArtifactProbeTests(unittest.TestCase):
    def test_text_binary_and_scan_limit_have_distinct_metadata(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            root = Path(temp)
            (root / "small.txt").write_bytes(b"x" * (CHUNK_SIZE - 1) + "中".encode())
            small = capture_worktree_file(root, "small.txt", text_limit=CHUNK_SIZE + 3)
            self.assertEqual(small["representation"], "utf8_text")
            self.assertTrue(small["content_available"])
            self.assertTrue(small["content"].endswith("中"))
            self.assertEqual(small["attribution"], "unknown")

            (root / "binary.bin").write_bytes(b"A\0\xffB")
            binary = capture_worktree_file(root, "binary.bin")
            self.assertEqual(binary["representation"], "binary_or_non_utf8")
            self.assertIsNone(binary["content"])
            self.assertFalse(binary["content_available"])
            self.assertEqual(len(binary["sha256"]), 64)

            with (root / "oversize.txt").open("wb") as stream:
                stream.seek(SCAN_LIMIT)
                stream.write(b"x")
            oversize = capture_worktree_file(root, "oversize.txt")
            self.assertEqual(oversize["reason"], "scan_limit")
            self.assertTrue(oversize["truncated"])
            self.assertIsNone(oversize["sha256"])

    def test_workspace_escape_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            (root / "ok.txt").write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact_outside_workspace"):
                capture_worktree_file(root, "../outside.txt")
            with self.assertRaisesRegex(ValueError, "artifact_outside_workspace"):
                capture_worktree_file(root, str(root / "ok.txt"))
            try:
                (root / "link.txt").symlink_to(root / "ok.txt")
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "artifact_link_not_allowed"):
                capture_worktree_file(root, "link.txt")

    def test_disposable_git_probe_labels_preexisting_changes_and_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = Path(temp) / "fixture"
            script = ROOT / "scripts/probe_artifacts.py"
            def run(action):
                return subprocess.run(
                    [sys.executable, str(script), action, "--fixture", str(fixture)],
                    cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
            prepared = run("prepare")
            self.assertEqual(prepared.returncode, 0)
            inspected = run("inspect")
            self.assertEqual(inspected.returncode, 0)
            report = json.loads(inspected.stdout)
            self.assertTrue(report["git_binary_marker"])
            self.assertEqual(report["large_text"]["reason"], "text_limit")
            self.assertEqual(report["binary"]["reason"], "binary_metadata_only")
            self.assertEqual(report["attribution"], "unknown")
            (fixture / "large.txt").write_bytes(b"changed")
            rejected = run("inspect")
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(json.loads(rejected.stdout)["code"], "invalid_artifact_fixture")

    def test_rename_probe_distinguishes_index_evidence_from_untracked_move(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = Path(temp) / "fixture"
            script = ROOT / "scripts/probe_artifacts.py"

            def run(action):
                return subprocess.run(
                    [sys.executable, str(script), action, "--fixture", str(fixture)],
                    cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)

            self.assertEqual(run("prepare-rename").returncode, 0)
            inspected = run("inspect-rename")
            self.assertEqual(inspected.returncode, 0, inspected.stdout)
            report = json.loads(inspected.stdout)
            self.assertEqual(report["staged_move"], "git_rename_100")
            self.assertEqual(report["unstaged_move"], "deletion_plus_untracked")
            self.assertFalse(report["unstaged_rename_confirmed"])
            self.assertEqual(report["attribution"], "unknown")
            (fixture / "worktree-new.txt").write_bytes(b"tampered")
            rejected = run("inspect-rename")
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(json.loads(rejected.stdout)["code"], "invalid_rename_fixture")


if __name__ == "__main__":
    unittest.main()
