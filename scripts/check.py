"""Deterministic offline checks; no account or model access."""

import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "host/src"))

from runvori.cli import verify_contracts

if __name__ == "__main__":
    (root / ".dev").mkdir(exist_ok=True)
    count = verify_contracts(root / "contracts/codex")
    print(f"Schema integrity: {count} passed", flush=True)
    suite = unittest.defaultTestLoader.discover(str(root / "host/tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
