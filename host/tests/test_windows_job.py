import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runvori.cli import ROOT
from runvori.codex.stdio import StdioClient

PEER = Path(__file__).with_name("process_tree_peer.py")


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".dev")
        self.addCleanup(self.temp.cleanup)
        self.marker = Path(self.temp.name) / "leaf.pid"
        self.api = c.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.api.OpenProcess.restype = w.HANDLE
        self.api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.api.WaitForSingleObject.restype = w.DWORD
        self.api.CloseHandle.argtypes = [w.HANDLE]

    def leaf_handle(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                pid = int(self.marker.read_text(encoding="ascii"))
                handle = self.api.OpenProcess(0x100000, False, pid)
                if handle:
                    self.addCleanup(self.api.CloseHandle, handle)
                    self.assertEqual(self.api.WaitForSingleObject(handle, 0), 258)
                    return handle
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(0.01)
        self.fail("descendant readiness timeout")

    def client(self, mode):
        client = StdioClient([sys.executable, str(PEER), mode, str(self.marker)],
                             cwd=ROOT, env=dict(os.environ))
        self.addCleanup(client.close)
        return client

    def test_close_kills_grandchildren_holding_pipes(self):
        client = self.client("tree")
        handle = self.leaf_handle()
        client.close()
        self.assertEqual(self.api.WaitForSingleObject(handle, 5000), 0)
        self.assertTrue(all(not t.is_alive() for t in client._threads))

    def test_parent_exit_does_not_detach_descendants(self):
        client = self.client("exit")
        handle = self.leaf_handle()
        client.process.wait(timeout=10)
        client.close()
        self.assertEqual(self.api.WaitForSingleObject(handle, 5000), 0)

    def test_owner_crash_closes_job_and_kills_descendants(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
        owner = subprocess.Popen([sys.executable, str(PEER), "owner", str(self.marker)],
                                 cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        def cleanup():
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=5)
        self.addCleanup(cleanup)
        handle = self.leaf_handle()
        owner.kill()
        owner.wait(timeout=5)
        self.assertEqual(self.api.WaitForSingleObject(handle, 5000), 0)

    def test_assignment_failure_never_starts_target(self):
        with patch("runvori.codex.windows_job.WindowsJob.assign", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                self.client("tree")
        self.assertFalse(self.marker.exists())
