"""Local thread creation stays on the owned stdio connection."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.owned_event_loop import run_owned_event_loop
from runvori.codex.owned_thread_start import (
    OwnedThreadStartError, OwnedThreadStartQueue,
    start_owned_session_thread_once,
)
from runvori.codex.upstream_lifecycle import launch_owned_upstream
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


@unittest.skipUnless(os.name == "nt", "Windows owned upstream integration")
class OwnedThreadStartTests(unittest.TestCase):
    @staticmethod
    def _owner(startup, mode, *extra):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.register_workspace("workspace", ROOT, sandbox="read-only",
                                   epoch=epoch)
        startup.create_session_in_workspace("session", "workspace", {},
                                            epoch=epoch)
        owner = launch_owned_upstream(
            startup, epoch=epoch,
            command=[sys.executable, str(PEER), mode, *map(str, extra)],
            cwd=ROOT, env=dict(os.environ))
        return epoch, owner

    def test_queue_is_bounded_and_rejects_invalid_identifiers(self):
        queue = OwnedThreadStartQueue(capacity=1)
        queue.submit("session", "local", "create")
        with self.assertRaisesRegex(OwnedThreadStartError,
                                    "thread_start_queue_full"):
            queue.submit("session", "local", "other")
        self.assertEqual(queue.take_nowait(), ("session", "local", "create"))
        self.assertIsNone(queue.take_nowait())
        with self.assertRaisesRegex(OwnedThreadStartError,
                                    "invalid_thread_start_queue_item"):
            queue.submit("", "local", "create")
        with self.assertRaisesRegex(OwnedThreadStartError,
                                    "invalid_thread_start_queue_item"):
            queue.submit("session", "device", "create")

    def test_loop_creates_thread_and_duplicate_signal_replays_binding(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "thread_bootstrap_good")
                queue = OwnedThreadStartQueue()
                queue.submit("session", "local", "create")
                queue.submit("session", "local", "create")
                stop = threading.Event()
                stopper = threading.Timer(0.2, stop.set)
                stopper.start()
                result = run_owned_event_loop(
                    owner, stop=stop, poll_timeout=0.05,
                    thread_starts=queue)
                stopper.join(timeout=2)
                self.assertEqual(result["reason"], "stopped")
                self.assertEqual(result["counts"]["thread_start_confirmed"], 2)
                self.assertEqual(startup.events.get_session_thread("session"),
                                 "thread_demo")
                self.assertEqual(startup.approvals.current_epoch(), epoch)

    def test_lost_thread_start_reply_recovers_without_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            marker = Path(temp) / "created.txt"
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(
                    startup, "thread_bootstrap_lost", marker)
                queue = OwnedThreadStartQueue()
                queue.submit("session", "local", "create")
                queue.submit("session", "local", "create")
                result = run_owned_event_loop(
                    owner, stop=threading.Event(), poll_timeout=0.1,
                    thread_starts=queue)
                self.assertEqual(result["reason"], "upstream_recovered")
                self.assertEqual(result["cause"], "thread_start_fault")
                self.assertEqual(result["epoch"], epoch + 1)
                self.assertEqual(marker.read_text(encoding="utf-8"), "created\n")
                self.assertEqual(startup.events.connection.execute(
                    "SELECT status FROM thread_starts WHERE session_id='session'"
                ).fetchone()[0], "unknown")
                self.assertIsNotNone(queue.take_nowait())

    def test_invalid_local_target_does_not_touch_upstream(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch, owner = self._owner(startup, "thread_bootstrap_good")
                result = start_owned_session_thread_once(
                    owner, session_id="missing", actor_id="local",
                    request_id="create", recorded_at="2026-10-25T00:00:01Z")
                self.assertEqual(result["kind"], "thread_start_rejected")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                self.assertFalse(owner.client._closed)


if __name__ == "__main__":
    unittest.main()
