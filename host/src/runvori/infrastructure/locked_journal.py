"""M0 startup slice: acquire Host ownership before opening the approval journal.

The journal epoch is intentionally not advanced here. A future Host must first
isolate any old app-server process and then call start_epoch_after_isolation.
"""

from __future__ import annotations

from pathlib import Path

from runvori.infrastructure.approval_journal import ApprovalJournal
from runvori.infrastructure.instance_lock import InstanceLockUnavailable, WindowsInstanceLock


class LockedApprovalJournal:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.lock: WindowsInstanceLock | None = None
        self.journal: ApprovalJournal | None = None

    def open(self) -> LockedApprovalJournal:
        if self.lock is not None:
            raise InstanceLockUnavailable("locked_journal_already_open")
        if (not self.data_dir.is_absolute() or not self.data_dir.is_dir()
                or self.data_dir.is_symlink()
                or (hasattr(self.data_dir, "is_junction") and self.data_dir.is_junction())):
            raise InstanceLockUnavailable("unsafe_host_data_dir")

        lock = WindowsInstanceLock(self.data_dir / "host.lock").acquire()
        try:
            journal = ApprovalJournal(self.data_dir / "approvals.db")
        except BaseException:
            lock.close()
            raise
        self.lock = lock
        self.journal = journal
        return self

    def close(self) -> None:
        journal, lock = self.journal, self.lock
        self.journal = None
        self.lock = None
        try:
            if journal is not None:
                journal.close()
        finally:
            if lock is not None:
                lock.close()

    def __enter__(self) -> LockedApprovalJournal:
        return self.open()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
