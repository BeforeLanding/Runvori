"""Real Windows process contention and crash release for the M0 lock slice."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.instance_lock import InstanceLockUnavailable, WindowsInstanceLock


PEER = Path(__file__).with_name("instance_lock_peer.py")


@unittest.skipUnless(os.name == "nt", "Windows file lock")
class InstanceLockTests(unittest.TestCase):
    def test_normal_close_allows_reacquire_without_deleting_lock_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            path = Path(temp) / "host.lock"
            with WindowsInstanceLock(path):
                self.assertTrue(path.is_file())
            with WindowsInstanceLock(path):
                self.assertTrue(path.is_file())

    def test_second_process_is_blocked_and_crash_releases_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            path = Path(temp) / "host.lock"
            marker = Path(temp) / "owner.ready"
            env = dict(os.environ, PYTHONPATH=str(ROOT / "host/src"))
            owner = subprocess.Popen([sys.executable, str(PEER), str(path), str(marker)],
                                     cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline:
                    self.assertIsNone(owner.poll(), "lock owner exited before readiness")
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "lock owner readiness timeout")
                with self.assertRaisesRegex(InstanceLockUnavailable,
                                            "instance_lock_unavailable"):
                    WindowsInstanceLock(path).acquire()
                owner.kill()
                owner.wait(timeout=5)
                with WindowsInstanceLock(path):
                    self.assertTrue(path.is_file())
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
