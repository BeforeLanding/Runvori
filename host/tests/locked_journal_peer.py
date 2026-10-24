"""Hold the M0 locked-journal slice open until the test ends the process."""

import sys
import time
from pathlib import Path

from runvori.infrastructure.locked_journal import LockedApprovalJournal


if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    marker = Path(sys.argv[2])
    with LockedApprovalJournal(data_dir) as startup:
        if startup.journal.current_epoch() != 0:
            raise RuntimeError("unexpected_epoch")
        marker.write_text("ready", encoding="ascii")
        while True:
            time.sleep(1)
