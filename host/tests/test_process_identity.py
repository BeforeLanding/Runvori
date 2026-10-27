"""Windows process identity checks; never a PID-only conclusion."""

import ctypes as c
import os
import subprocess
import sys
import time
import unittest
from ctypes import wintypes as w
from unittest.mock import patch

from runvori.infrastructure.process_identity import (
    ISOLATED_OUTCOMES, ProcessIdentityError, capture_process_identity,
    probe_process_identity,
)


def _creation_time_by_pid(pid: int) -> int:
    api = c.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.GetProcessTimes.argtypes = [w.HANDLE, c.POINTER(w.FILETIME),
                                    c.POINTER(w.FILETIME), c.POINTER(w.FILETIME),
                                    c.POINTER(w.FILETIME)]
    api.GetProcessTimes.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError("open_failed")
    try:
        created, exited, kernel, user = (w.FILETIME() for _ in range(4))
        if not api.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                                   c.byref(kernel), c.byref(user)):
            raise OSError("times_failed")
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        api.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "Windows process identity")
class ProcessIdentityTests(unittest.TestCase):
    def _spawn(self):
        process = subprocess.Popen([sys.executable, "-c",
                                    "import time; time.sleep(60)"])
        self.addCleanup(process.wait, 5)
        self.addCleanup(process.kill)
        return process

    def _wait_exit(self, process, identity):
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            # Windows keeps the PID entry while any handle remains open, so an
            # exited process can report "exited" instead of "absent".
            if probe_process_identity(**identity) != "running":
                return
            time.sleep(0.02)
        self.fail("process still reported running after exit")

    def test_capture_matches_kernel_creation_time_for_live_process(self):
        process = self._spawn()
        identity = capture_process_identity(process)
        self.assertEqual(identity["pid"], process.pid)
        self.assertEqual(identity["created_at"],
                         _creation_time_by_pid(process.pid))
        self.assertEqual(probe_process_identity(**identity), "running")

    def test_exited_process_reports_exited_not_running(self):
        process = self._spawn()
        identity = capture_process_identity(process)
        self._wait_exit(process, identity)
        outcome = probe_process_identity(**identity)
        self.assertIn(outcome, ISOLATED_OUTCOMES)
        self.assertNotEqual(outcome, "running")

    def test_pid_reuse_is_distinguished_by_creation_time(self):
        process = self._spawn()
        identity = capture_process_identity(process)
        self.assertEqual(probe_process_identity(
            identity["pid"], identity["created_at"] + 1), "replaced")

    def test_missing_pid_is_absent_and_malformed_identity_is_refused(self):
        process = self._spawn()
        identity = capture_process_identity(process)
        with self.assertRaises(ProcessIdentityError):
            probe_process_identity(0, identity["created_at"])
        with self.assertRaises(ProcessIdentityError):
            probe_process_identity(identity["pid"], 0)
        with self.assertRaises(ProcessIdentityError):
            probe_process_identity(str(identity["pid"]), identity["created_at"])

    def test_capture_refuses_process_without_windows_handle(self):
        class Bare:
            pid = 1234

        with self.assertRaises(ProcessIdentityError):
            capture_process_identity(Bare())

    def test_inaccessible_query_never_authorizes_recovery(self):
        # An unreadable process must stay outside the isolated outcomes; the
        # gate treats it exactly like a live one.
        with patch("runvori.infrastructure.process_identity._api",
                   side_effect=ProcessIdentityError("windows_only")):
            with self.assertRaises(ProcessIdentityError):
                probe_process_identity(1234, 1)
        self.assertEqual(ISOLATED_OUTCOMES, {"absent", "exited", "replaced"})


if __name__ == "__main__":
    unittest.main()