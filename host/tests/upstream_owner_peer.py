"""Hold a locked Host with a recorded upstream until the test kills this process.

Modes:
- ``owned``: launch the fake app-server through ``launch_owned_upstream`` so the
  launcher identity is persisted and the Job kills the tree when this process
  dies. Recovery afterwards must succeed.
- ``leftover``: persist the identity of a process that is outside the Job and
  keep it alive. Recovery afterwards must be refused.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from runvori.codex.upstream_lifecycle import OwnedUpstream, launch_owned_upstream
from runvori.infrastructure.locked_journal import LockedHostJournals
from runvori.infrastructure.process_identity import capture_process_identity


if __name__ == "__main__":
    mode, data_dir, marker = sys.argv[1:4]
    data_dir, marker = Path(data_dir), Path(marker)
    with LockedHostJournals(data_dir) as startup:
        if startup.approvals.current_epoch() != 0:
            raise RuntimeError("unexpected_epoch")
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        if mode == "owned":
            owner = launch_owned_upstream(
                startup, epoch=epoch,
                command=[sys.executable, str(Path(__file__).with_name(
                    "fake_app_server.py")), "thread_bootstrap_good"],
                cwd=Path(__file__).resolve().parents[2],
                env=dict(os.environ))
            pid = owner.client.process.pid
        else:
            owner = OwnedUpstream(startup, epoch)
            startup.reserve_upstream_owner(owner, epoch=epoch)
            leftover = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"])
            identity = capture_process_identity(leftover)
            startup.record_upstream_process(
                owner, epoch=epoch, pid=identity["pid"],
                created_at=identity["created_at"],
                recorded_at="2026-10-25T00:00:01Z")
            pid = identity["pid"]
        # Write atomically so a reader never sees a partially written marker.
        staging = marker.with_suffix(".tmp")
        staging.write_text(f"ready {pid}", encoding="ascii")
        staging.replace(marker)
        while True:
            time.sleep(1)