"""Run the local source package without installing dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host/src"))

from runvori.cli import main

if __name__ == "__main__":
    sys.exit(main())
