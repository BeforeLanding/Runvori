"""Hold both journals and one active slot until the test kills this process."""

import sys
import time
from pathlib import Path

from runvori.infrastructure.locked_journal import LockedHostJournals


if __name__ == "__main__":
    data_dir = Path(sys.argv[1])
    marker = Path(sys.argv[2])
    with LockedHostJournals(data_dir) as startup:
        if startup.approvals.current_epoch() != 0:
            raise RuntimeError("unexpected_epoch")
        startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")
        startup.events.create_session("session", {"status": "idle"})
        startup.events.reserve_run(
            "session", "run", expected_session_version=0,
            recorded_at="2026-10-25T00:00:00Z")
        marker.write_text("ready", encoding="ascii")
        while True:
            time.sleep(1)
