"""Owned single-message handoff and fail-closed recovery."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.owned_event_pump import (
    OwnedEventPumpError, pump_owned_event_once,
)
from runvori.codex.upstream_lifecycle import OwnedUpstream, launch_owned_upstream
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


@unittest.skipUnless(os.name == "nt", "Windows owned upstream integration")
class OwnedEventPumpTests(unittest.TestCase):
    @staticmethod
    def _running(startup, *, confirmed=False):
        epoch = startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run_request(
            "session", "run", actor_id="local", request_id="start",
            expected_session_version=0, input_text="hello",
            recorded_at="2026-10-25T00:00:01Z")
        if confirmed:
            startup.events.claim_run_dispatch(
                "local", "start", process_epoch=epoch,
                recorded_at="2026-10-25T00:00:02Z")
            startup.events.confirm_run_started(
                "local", "start", process_epoch=epoch,
                thread_id="thread_demo", turn_id="turn_demo",
                recorded_at="2026-10-25T00:00:03Z")
        return epoch

    @staticmethod
    def _owner(startup, epoch, mode):
        return launch_owned_upstream(
            startup, epoch=epoch,
            command=[sys.executable, str(PEER), mode],
            cwd=ROOT, env=dict(os.environ))

    def test_ordered_messages_persist_without_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup, confirmed=True)
                owner = self._owner(startup, epoch, "event_handoff")
                owner.client.request("turn/start", {})
                kinds = [pump_owned_event_once(
                    owner, timeout=1,
                    recorded_at=f"2026-10-25T00:00:0{n}Z")["kind"]
                    for n in (4, 5, 6)]
                self.assertEqual(kinds, ["approval_request", "turn_terminal",
                                         "request_closed"])
                self.assertIsNone(pump_owned_event_once(
                    owner, timeout=0.05,
                    recorded_at="2026-10-25T00:00:07Z"))
                self.assertEqual(startup.approvals.current_epoch(), epoch)

    def test_upstream_exit_interrupts_active_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                owner = self._owner(startup, epoch, "exit_after_initialized")
                result = pump_owned_event_once(
                    owner, timeout=2,
                    recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(result["kind"], "upstream_recovered")
                self.assertEqual(result["cause"], "transport_fault")
                self.assertEqual(result["recovery"]["epoch"], epoch + 1)
                self.assertEqual(startup.events.connection.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "interrupted")

    def test_persistence_failure_isolates_and_recovers(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup, confirmed=True)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_handoff BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%approval.requested%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                owner = self._owner(startup, epoch, "event_handoff")
                owner.client.request("turn/start", {})
                result = pump_owned_event_once(
                    owner, timeout=1,
                    recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(result["kind"], "upstream_recovered")
                self.assertEqual(result["cause"], "handoff_fault")
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
                self.assertEqual(db.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "interrupted")

    def test_forged_owner_and_invalid_timeout_do_not_consume_event(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                owner = self._owner(startup, epoch, "thread_bootstrap_good")
                forged = OwnedUpstream(startup, epoch)
                forged.client = owner.client
                with self.assertRaisesRegex(EventJournalError,
                                            "upstream_owner_mismatch"):
                    pump_owned_event_once(
                        forged, timeout=1,
                        recorded_at="2026-10-25T00:00:02Z")
                with self.assertRaisesRegex(OwnedEventPumpError,
                                            "invalid_event_pump_request"):
                    pump_owned_event_once(
                        owner, timeout=0,
                        recorded_at="2026-10-25T00:00:02Z")
                self.assertIsNone(owner.client.process.poll())
                self.assertEqual(startup.approvals.current_epoch(), epoch)

    def test_recovery_write_failure_keeps_gate_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                owner = self._owner(startup, epoch, "exit_after_initialized")
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_epoch BEFORE UPDATE ON journal_meta
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaisesRegex(OwnedEventPumpError,
                                            "upstream_recovery_failed"):
                    pump_owned_event_once(
                        owner, timeout=2,
                        recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(startup.approvals.current_epoch(), epoch)
                with self.assertRaisesRegex(EventJournalError,
                                            "host_not_recovered"):
                    startup.events.create_session("blocked", {})
                self.assertEqual(db.execute(
                    "SELECT status FROM runs WHERE run_id='run'").fetchone()[0],
                    "starting")


if __name__ == "__main__":
    unittest.main()
