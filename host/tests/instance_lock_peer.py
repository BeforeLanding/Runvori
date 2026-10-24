"""Separate lock owner used by the Windows process-exit test."""

import sys
import time
from pathlib import Path

from runvori.infrastructure.instance_lock import WindowsInstanceLock


if __name__ == "__main__":
    lock_path = Path(sys.argv[1])
    marker = Path(sys.argv[2])
    with WindowsInstanceLock(lock_path):
        marker.write_text("held", encoding="ascii")
        while True:
            time.sleep(1)
