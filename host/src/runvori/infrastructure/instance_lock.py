"""Windows process-local ownership of a Host data directory.

This M0 slice is not wired to a product Host startup path yet. The caller must
keep the returned object alive for the whole Host lifetime.
"""

from __future__ import annotations

import os
from pathlib import Path


class InstanceLockUnavailable(RuntimeError):
    """A second Host must not start when lock ownership is uncertain."""


class WindowsInstanceLock:
    def __init__(self, path: Path):
        if os.name != "nt":
            raise InstanceLockUnavailable("windows_lock_only")
        self.path = Path(path)
        self._stream = None

    def acquire(self) -> WindowsInstanceLock:
        if self._stream is not None:
            raise InstanceLockUnavailable("instance_lock_already_held")
        if (not self.path.is_absolute() or not self.path.parent.is_dir()
                or self.path.is_symlink()
                or (hasattr(self.path, "is_junction") and self.path.is_junction())):
            raise InstanceLockUnavailable("unsafe_instance_lock_path")

        import msvcrt

        try:
            stream = self.path.open("a+b")
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except BaseException:
                stream.close()
                raise
        except OSError as error:
            raise InstanceLockUnavailable("instance_lock_unavailable") from error
        self._stream = stream
        return self

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        import msvcrt

        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            stream.close()

    def __enter__(self) -> WindowsInstanceLock:
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
