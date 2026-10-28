"""Durable interrupt intent, one-shot claim, and terminal competition."""

import os
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.interrupt_dispatch import send_interrupt_once
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class InterruptJournalTests(unittest.TestCase):
    @staticmethod
    def _starting(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.bind_session_thread("session", "thread_demo")
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="start",
            expected_session_version=0, input_text="go",
            recorded_at="2026-10-25T00:00:01Z")
        return epoch

    def test_cancel_before_run_dispatch_is_terminal_and_replayable(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._starting(startup)
                cancelled = startup.events.reserve_interrupt_request(
                    "run", actor_id="device", request_id="stop",
                    expected_version=1, recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(cancelled["status"], "confirmed")
                self.assertEqual(cancelled["outcome"], "run_interrupted")
                self.assertEqual(startup.events.reserve_interrupt_request(
                    "run", actor_id="device", request_id="stop",
                    expected_version=1, recorded_at="2026-10-25T00:00:03Z"),
                    cancelled)
                self.assertEqual(startup.events.get_run_operation(
                    "device", "start")["outcome"],
                    "cancelled_before_dispatch")
                self.assertIsNone(startup.events.snapshot("session")["active_run"])
                self.assertEqual([event["type"] for event in
                                  startup.events.page("session")["events"]][-1],
                                 "run.interrupted")
                with self.assertRaisesRegex(EventJournalError,
                                            "dispatch_unavailable"):
                    startup.events.claim_run_dispatch(
                        "device", "start", process_epoch=epoch,
                        recorded_at="2026-10-25T00:00:04Z")

    def test_claimed_start_accepts_interrupt_then_dispatches_after_binding(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._starting(startup)
                startup.events.claim_run_dispatch(
                    "device", "start", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:02Z")
                accepted = startup.events.reserve_interrupt_request(
                    "run", actor_id="device", request_id="stop",
                    expected_version=1, recorded_at="2026-10-25T00:00:03Z")
                self.assertEqual(accepted["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "interrupting")
                self.assertEqual(startup.events.get_run_operation(
                    "device", "start")["status"], "dispatching")
                with self.assertRaisesRegex(EventJournalError,
                                            "operation_not_found"):
                    startup.events.claim_interrupt_dispatch(
                        "device", "stop", process_epoch=epoch,
                        recorded_at="2026-10-25T00:00:04Z")
                startup.events.confirm_run_started(
                    "device", "start", process_epoch=epoch,
                    thread_id="thread_demo", turn_id="turn_demo",
                    recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "interrupting")

                class Client:
                    calls = []

                    def request(self, method, params):
                        self.calls.append((method, params))
                        return {}

                client = Client()
                sent = send_interrupt_once(
                    client, startup, actor_id="device", request_id="stop",
                    epoch=epoch, recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(sent["status"], "dispatching")
                self.assertEqual(client.calls, [
                    ("turn/interrupt", {"threadId": "thread_demo",
                                        "turnId": "turn_demo"})])
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
                    terminal="interrupted", recorded_at="2026-10-25T00:00:07Z")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["outcome"], "run_interrupted")

    def test_cancel_event_failure_rolls_back_all_state(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._starting(startup)
                startup.events.connection.execute(
                    """CREATE TRIGGER block_cancel BEFORE INSERT ON events
                       WHEN NEW.event_json LIKE '%cancelled_before_dispatch%'
                       BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(Exception):
                    startup.events.reserve_interrupt_request(
                        "run", actor_id="device", request_id="stop",
                        expected_version=1, recorded_at="2026-10-25T00:00:02Z")
                self.assertEqual(startup.events.get_run_operation(
                    "device", "start")["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "starting")
                with self.assertRaisesRegex(EventJournalError,
                                            "operation_not_found"):
                    startup.events.get_interrupt_operation("device", "stop")

    def test_claimed_start_lost_before_binding_settles_interrupt_unsent(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._starting(startup)
                startup.events.claim_run_dispatch(
                    "device", "start", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:02Z")
                startup.events.reserve_interrupt_request(
                    "run", actor_id="device", request_id="stop",
                    expected_version=1, recorded_at="2026-10-25T00:00:03Z")
                startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=2,
                    reason="upstream_exit", recorded_at="2026-10-25T00:00:04Z")
                self.assertEqual(startup.events.get_run_operation(
                    "device", "start")["status"], "unknown")
                interrupt = startup.events.get_interrupt_operation(
                    "device", "stop")
                self.assertEqual(interrupt["status"], "failed")
                self.assertEqual(interrupt["outcome"], "not_dispatched")
                self.assertIsNone(startup.events.snapshot("session")["active_run"])

    def test_claimed_start_terminal_before_interrupt_send_stays_unsent(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._starting(startup)
                startup.events.claim_run_dispatch(
                    "device", "start", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:02Z")
                startup.events.reserve_interrupt_request(
                    "run", actor_id="device", request_id="stop",
                    expected_version=1, recorded_at="2026-10-25T00:00:03Z")
                startup.events.confirm_run_started(
                    "device", "start", process_epoch=epoch,
                    thread_id="thread_demo", turn_id="turn_demo",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
                    terminal="completed", recorded_at="2026-10-25T00:00:05Z")
                interrupt = startup.events.get_interrupt_operation(
                    "device", "stop")
                self.assertEqual(interrupt["status"], "failed")
                self.assertEqual(interrupt["outcome"], "terminal_before_dispatch")
                with self.assertRaisesRegex(EventJournalError,
                                            "interrupt_turn_unavailable"):
                    startup.get_bound_interrupt_turn("run", epoch=epoch)

    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="start",
            expected_session_version=0, input_text="go",
            recorded_at="2026-10-25T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "start", process_epoch=epoch,
            recorded_at="2026-10-25T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "start", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-25T00:00:03Z")
        return epoch

    @staticmethod
    def _interrupt(startup):
        return startup.events.reserve_interrupt_request(
            "run", actor_id="device", request_id="stop",
            expected_version=2, recorded_at="2026-10-25T00:00:04Z")

    def test_accept_replay_conflict_and_one_claim(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                accepted = self._interrupt(startup)
                self.assertEqual(accepted["status"], "accepted")
                self.assertEqual(self._interrupt(startup), accepted)
                self.assertEqual(startup.events.reserve_interrupt_request(
                    "run", actor_id="device", request_id="another",
                    expected_version=3,
                    recorded_at="2026-10-25T00:00:05Z"), accepted)
                with self.assertRaisesRegex(EventJournalError,
                                            "idempotency_conflict"):
                    startup.events.reserve_interrupt_request(
                        "run", actor_id="device", request_id="stop",
                        expected_version=3,
                        recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "interrupting")
                claimed = startup.events.claim_interrupt_dispatch(
                    "device", "stop", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(claimed["status"], "dispatching")
                with self.assertRaisesRegex(EventJournalError,
                                            "dispatch_unavailable"):
                    startup.events.claim_interrupt_dispatch(
                        "device", "stop", process_epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                unknown = startup.events.mark_interrupt_dispatch_unknown(
                    "device", "stop", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(unknown["status"], "unknown")
                self.assertEqual(unknown["outcome"], "dispatch_unconfirmed")

    def test_terminal_before_dispatch_fails_operation_atomically(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                self._interrupt(startup)
                terminal = startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
                    terminal="completed", recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(terminal["type"], "run.completed")
                operation = startup.events.get_interrupt_operation("device", "stop")
                self.assertEqual(operation["status"], "failed")
                self.assertEqual(operation["outcome"], "terminal_before_dispatch")
                self.assertIsNone(startup.events.snapshot("session")["active_run"])
                self.assertEqual(startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo", epoch=epoch,
                    terminal="completed", recorded_at="2026-10-25T00:00:06Z"),
                    terminal)

    def test_terminal_after_claim_reports_actual_outcome(self):
        for terminal in ("completed", "interrupted"):
            with self.subTest(terminal=terminal):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    with LockedHostJournals(Path(temp)) as startup:
                        epoch = self._running(startup)
                        self._interrupt(startup)
                        startup.events.claim_interrupt_dispatch(
                            "device", "stop", process_epoch=epoch,
                            recorded_at="2026-10-25T00:00:05Z")
                        startup.record_upstream_turn_terminal(
                            thread_id="thread_demo", turn_id="turn_demo",
                            epoch=epoch, terminal=terminal,
                            recorded_at="2026-10-25T00:00:06Z")
                        operation = startup.events.get_interrupt_operation(
                            "device", "stop")
                        self.assertEqual(operation["status"], "confirmed")
                        self.assertEqual(operation["outcome"], f"run_{terminal}")

    def test_isolation_settles_unsent_and_uncertain_interrupts(self):
        for claimed in (False, True):
            with self.subTest(claimed=claimed):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    with LockedHostJournals(Path(temp)) as startup:
                        epoch = self._running(startup)
                        self._interrupt(startup)
                        if claimed:
                            startup.events.claim_interrupt_dispatch(
                                "device", "stop", process_epoch=epoch,
                                recorded_at="2026-10-25T00:00:05Z")
                        startup.recover_host_after_isolation(
                            old_process_epoch=epoch,
                            expected_run_id="run", expected_run_version=3,
                            reason="upstream_exit",
                            recorded_at="2026-10-25T00:00:06Z")
                        operation = startup.events.get_interrupt_operation(
                            "device", "stop")
                        self.assertEqual(operation["status"],
                                         "unknown" if claimed else "failed")
                        self.assertEqual(operation["outcome"],
                                         "dispatch_unconfirmed" if claimed
                                         else "not_dispatched")

    def test_terminal_event_failure_rolls_back_interrupt_settlement(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                self._interrupt(startup)
                startup.events.connection.execute(
                    """CREATE TRIGGER block_terminal BEFORE INSERT ON events
                       WHEN NEW.event_json LIKE '%run.completed%'
                       BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(Exception):
                    startup.record_upstream_turn_terminal(
                        thread_id="thread_demo", turn_id="turn_demo",
                        epoch=epoch, terminal="completed",
                        recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "accepted")
                self.assertEqual(startup.events.snapshot("session")[
                    "active_run"]["status"], "interrupting")

    def test_already_unknown_interrupt_is_not_rewritten_on_isolation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                self._interrupt(startup)
                startup.events.claim_interrupt_dispatch(
                    "device", "stop", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                startup.events.mark_interrupt_dispatch_unknown(
                    "device", "stop", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:06Z")
                before = startup.events.snapshot("session")["snapshot_seq"]
                startup.recover_host_after_isolation(
                    old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="upstream_exit",
                    recorded_at="2026-10-25T00:00:07Z")
                self.assertEqual(startup.events.snapshot("session")[
                    "snapshot_seq"], before + 1)
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "unknown")

    def test_epoch_mismatch_and_acceptance_failure_do_not_advance(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                db = startup.events.connection
                db.execute(
                    """CREATE TRIGGER block_interrupt BEFORE INSERT ON events
                       WHEN NEW.event_json LIKE '%\"action\":\"interrupt\"%'
                       BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(Exception):
                    self._interrupt(startup)
                self.assertEqual(db.execute(
                    "SELECT status, version FROM runs WHERE run_id='run'"
                ).fetchone()[:], ("running", 2))
                self.assertIsNone(db.execute(
                    "SELECT 1 FROM interrupt_operations").fetchone())
                db.execute("DROP TRIGGER block_interrupt")
                self._interrupt(startup)
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    startup.events.claim_interrupt_dispatch(
                        "device", "stop", process_epoch=epoch + 1,
                        recorded_at="2026-10-25T00:00:05Z")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "accepted")
                startup.events.claim_interrupt_dispatch(
                    "device", "stop", process_epoch=epoch,
                    recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                    startup.events.mark_interrupt_dispatch_unknown(
                        "device", "stop", process_epoch=epoch + 1,
                        recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop")["status"], "dispatching")

    def test_v9_upgrade_adds_interrupt_table_without_changing_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            with LockedHostJournals(folder) as startup:
                self._running(startup)
                startup.events.connection.execute("DROP TABLE interrupt_operations")
                startup.events.connection.execute("PRAGMA user_version=9")
            with LockedHostJournals(folder) as reopened:
                self.assertEqual(reopened.events.connection.execute(
                    "PRAGMA user_version").fetchone()[0], 17)
                self.assertEqual(reopened.events.connection.execute(
                    "SELECT status FROM runs WHERE run_id='run'"
                ).fetchone()[0], "running")
                self.assertEqual(reopened.events.connection.execute(
                    "SELECT COUNT(*) FROM interrupt_operations"
                ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
