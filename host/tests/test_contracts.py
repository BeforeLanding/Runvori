import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runvori.cli import PINNED_CODEX, ROOT, SELECTED, run_metadata, verify_contracts
from runvori.codex.stdio import TransportError


class ContractTests(unittest.TestCase):
    def test_committed_schemas_match_manifest(self):
        self.assertEqual(verify_contracts(ROOT / "contracts/codex"), len(SELECTED))

    def test_tampered_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            directory = Path(temp)
            manifest = {"codex_version": PINNED_CODEX, "files": {}}
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(TransportError, "schema_manifest_mismatch"):
                verify_contracts(directory)

    def test_new_cli_version_requires_explicit_review(self):
        with patch("runvori.codex.version_probe.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"codex-cli 999.0.0\n"
            with self.assertRaisesRegex(TransportError, "codex_version_mismatch"):
                run_metadata("codex", {}, ROOT)

    def test_modified_schema_content_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            target = Path(temp) / "contracts"
            shutil.copytree(ROOT / "contracts/codex", target)
            (target / SELECTED[0]).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(TransportError, "schema_hash_mismatch"):
                verify_contracts(target)

    def test_malformed_manifest_rejected_without_traceback(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            directory = Path(temp)
            (directory / "manifest.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(TransportError, "schema_manifest_mismatch"):
                verify_contracts(directory)
