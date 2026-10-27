"""Windows process identity by PID and creation time; no process enumeration."""

from __future__ import annotations

import ctypes as c
import os
from ctypes import wintypes as w

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_INVALID_PARAMETER = 87
# A successful probe of the recorded process. Anything else refuses recovery.
ISOLATED_OUTCOMES = frozenset({"absent", "exited", "replaced"})


class ProcessIdentityError(RuntimeError):
    """Stable refusal without process output or command line material."""


class _FileTime(c.Structure):
    _fields_ = [("low", w.DWORD), ("high", w.DWORD)]

    def value(self) -> int:
        return (self.high << 32) | self.low


def _api():
    if os.name != "nt":
        raise ProcessIdentityError("windows_only")
    api = c.WinDLL("kernel32", use_last_error=True)
    declarations = {
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "GetProcessTimes": ([w.HANDLE, c.POINTER(_FileTime),
                             c.POINTER(_FileTime), c.POINTER(_FileTime),
                             c.POINTER(_FileTime)], w.BOOL),
        "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
    }
    for name, (arguments, result) in declarations.items():
        method = getattr(api, name)
        method.argtypes, method.restype = arguments, result
    return api


def _creation_time(api, handle: int) -> int:
    created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
    if not api.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                               c.byref(kernel), c.byref(user)):
        raise ProcessIdentityError("process_times_unavailable")
    created_at = created.value()
    if created_at <= 0:
        raise ProcessIdentityError("process_times_unavailable")
    return created_at


def capture_process_identity(process) -> dict:
    """Read the launcher's identity while its Windows handle is still open."""
    if os.name != "nt":
        raise ProcessIdentityError("windows_only")
    pid = getattr(process, "pid", None)
    raw_handle = getattr(process, "_handle", None)
    try:
        handle = int(raw_handle) if raw_handle is not None else 0
    except (TypeError, ValueError) as error:
        raise ProcessIdentityError("process_identity_unavailable") from error
    if type(pid) is not int or pid <= 0 or handle <= 0:
        raise ProcessIdentityError("process_identity_unavailable")
    return {"pid": pid, "created_at": _creation_time(_api(), handle)}


def probe_process_identity(pid: int, created_at: int) -> str:
    """Classify the recorded process; only ISOLATED_OUTCOMES permit recovery.

    A query failure is reported as inaccessible rather than as absence, so an
    unknown state never authorizes a new epoch.
    """
    if (type(pid) is not int or pid <= 0
            or type(created_at) is not int or created_at <= 0):
        raise ProcessIdentityError("invalid_process_identity")
    api = _api()
    handle = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A reused or long-gone PID yields ERROR_INVALID_PARAMETER; any other
        # failure stays unconfirmed instead of being read as absence.
        return ("absent" if c.get_last_error() == ERROR_INVALID_PARAMETER
                else "inaccessible")
    try:
        if _creation_time(api, handle) != created_at:
            return "replaced"
        exit_code = w.DWORD()
        if not api.GetExitCodeProcess(handle, c.byref(exit_code)):
            return "inaccessible"
        # A terminated process keeps its creation time and reports its real
        # exit code, so this separates it from a live one. An exited process
        # whose code happens to be STILL_ACTIVE is treated as running.
        return "exited" if exit_code.value != STILL_ACTIVE else "running"
    except ProcessIdentityError:
        return "inaccessible"
    finally:
        api.CloseHandle(handle)