"""Cross-thread command handoff keeps SQLite on the Host owner thread."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from runvori.application.command_mailbox import (
    CommandMailboxError, HostCommandMailbox,
)
from runvori.cli import ROOT
from runvori.codex.owned_event_loop import run_owned_event_loop
from runvori.codex.owned_interrupt_queue import OwnedInterruptQueue
from runvori.codex.owned_run_start import OwnedRunStartQueue
from runvori.codex.upstream_lifecycle import launch_owned_upstream
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class MailboxShutdownTests(unittest.TestCase):
    def test_close_releases_waiters_and_refuses_new_work(self):
        mailbox = HostCommandMailbox()
        outcomes = {}

        def read():
            try:
                mailbox.wait_session_events(
                    session_id="session", wait_ms=25000)
            except CommandMailboxError as error:
                outcomes["read"] = str(error)

        def write():
            try:
                mailbox.start_run(
                    session_id="session", actor_id="device",
                    request_id="request", expected_session_version=0,
                    input_text="go")
            except CommandMailboxError as error:
                outcomes["write"] = str(error)

        readers = [threading.Thread(target=read), threading.Thread(target=write)]
        for worker in readers:
            worker.start()
        deadline = time.monotonic() + 1
        while mailbox._items.qsize() != 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(mailbox._items.qsize(), 2)
        mailbox.close()
        mailbox.close()
        for worker in readers:
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, {
            "read": "host_stopped", "write": "command_result_unknown"})
        with self.assertRaisesRegex(CommandMailboxError, "host_stopped"):
            mailbox.get_session_snapshot(session_id="session")


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class CommandMailboxTests(unittest.TestCase):
    @staticmethod
    def _ready(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-26T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.bind_session_thread("session", "thread_demo")
        return epoch

    @staticmethod
    def _call_and_serve(mailbox, journals, call, *, runs, interrupts):
        outcome = {}

        def caller():
            try:
                outcome["result"] = call()
            except Exception as error:
                outcome["error"] = error

        worker = threading.Thread(target=caller)
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if mailbox.serve_once(
                    journals, recorded_at="2026-10-26T00:00:01Z",
                    run_starts=runs, interrupts=interrupts):
                break
            time.sleep(0.005)
        else:
            worker.join(timeout=2)
            raise AssertionError("command_not_enqueued")
        worker.join(timeout=2)
        assert not worker.is_alive()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

    def test_start_and_interrupt_are_persisted_before_wakeup(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                mailbox = HostCommandMailbox()
                runs = OwnedRunStartQueue()
                interrupts = OwnedInterruptQueue()
                start = lambda: mailbox.start_run(
                    session_id="session", actor_id="device",
                    request_id="start", expected_session_version=0,
                    input_text="go")
                accepted = self._call_and_serve(
                    mailbox, startup, start, runs=runs,
                    interrupts=interrupts)
                self.assertEqual(accepted["status"], "accepted")
                run_id = accepted["resource_id"]
                self.assertEqual(startup.events.get_run_operation(
                    "device", "start"), accepted)
                self.assertEqual(runs.take_nowait()[1], run_id)
                self.assertEqual(self._call_and_serve(
                    mailbox, startup, start, runs=runs,
                    interrupts=interrupts), accepted)
                self.assertEqual(runs.take_nowait()[1], run_id)

                startup.events.claim_run_dispatch(
                    "device", "start", process_epoch=epoch,
                    recorded_at="2026-10-26T00:00:02Z")
                startup.events.confirm_run_started(
                    "device", "start", process_epoch=epoch,
                    thread_id="thread_demo", turn_id="turn_demo",
                    recorded_at="2026-10-26T00:00:03Z")
                stopped = self._call_and_serve(
                    mailbox, startup,
                    lambda: mailbox.interrupt_run(
                        run_id=run_id, actor_id="device", request_id="stop",
                        expected_version=2),
                    runs=runs, interrupts=interrupts)
                self.assertEqual(stopped["status"], "accepted")
                self.assertEqual(startup.events.get_interrupt_operation(
                    "device", "stop"), stopped)
                self.assertEqual(interrupts.take_nowait(), ("device", "stop"))

    def test_timeout_reports_unknown_then_same_key_can_be_queried(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._ready(startup)
                mailbox = HostCommandMailbox()
                with self.assertRaisesRegex(CommandMailboxError,
                                            "command_result_unknown"):
                    mailbox.start_run(
                        session_id="session", actor_id="device",
                        request_id="start", expected_session_version=0,
                        input_text="go", timeout=0.01)
                runs = OwnedRunStartQueue()
                self.assertTrue(mailbox.serve_once(
                    startup, recorded_at="2026-10-26T00:00:01Z",
                    run_starts=runs, interrupts=OwnedInterruptQueue()))
                found = self._call_and_serve(
                    mailbox, startup,
                    lambda: mailbox.get_operation(
                        kind="run", actor_id="device", request_id="start"),
                    runs=runs, interrupts=OwnedInterruptQueue())
                self.assertEqual(found["status"], "accepted")
                self.assertEqual(runs.take_nowait()[1], found["resource_id"])

    def test_unavailable_or_full_wakeup_does_not_forge_dispatch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._ready(startup)
                mailbox = HostCommandMailbox()
                start = lambda: mailbox.start_run(
                    session_id="session", actor_id="device",
                    request_id="start", expected_session_version=0,
                    input_text="go")
                with self.assertRaisesRegex(CommandMailboxError,
                                            "execution_unavailable"):
                    self._call_and_serve(
                        mailbox, startup, start, runs=None,
                        interrupts=OwnedInterruptQueue())
                self.assertIsNone(startup.events.connection.execute(
                    "SELECT 1 FROM run_operations").fetchone())

                runs = OwnedRunStartQueue(capacity=1)
                runs.submit(
                    session_id="other", run_id="other", actor_id="other",
                    request_id="other", expected_session_version=0,
                    input_text="other")
                with self.assertRaisesRegex(CommandMailboxError,
                                            "dispatch_signal_unavailable"):
                    self._call_and_serve(
                        mailbox, startup, start, runs=runs,
                        interrupts=OwnedInterruptQueue())
                accepted = startup.events.get_run_operation("device", "start")
                self.assertEqual(accepted["status"], "accepted")
                self.assertEqual(runs.take_nowait()[1], "other")
                self.assertEqual(self._call_and_serve(
                    mailbox, startup, start, runs=runs,
                    interrupts=OwnedInterruptQueue()), accepted)
                self.assertEqual(runs.take_nowait()[1], accepted["resource_id"])

    def test_snapshot_then_incremental_and_history_reads_share_owner(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="start",
                    expected_session_version=0, input_text="go",
                    recorded_at="2026-10-26T00:00:01Z")
                mailbox = HostCommandMailbox()
                runs = OwnedRunStartQueue()
                interrupts = OwnedInterruptQueue()

                def read(call):
                    return self._call_and_serve(
                        mailbox, startup, call, runs=runs,
                        interrupts=interrupts)

                self.assertEqual(read(lambda: mailbox.get_host_identity()),
                                 {"host_id": startup.events.get_host_id()})
                snapshot = read(lambda: mailbox.get_session_snapshot(
                    session_id="session"))
                self.assertEqual(snapshot["snapshot_seq"], 1)
                self.assertEqual(snapshot["active_run"]["run_id"], "run")
                startup.events.claim_run_dispatch(
                    "device", "start", process_epoch=epoch,
                    recorded_at="2026-10-26T00:00:02Z")
                newer = read(lambda: mailbox.get_session_events(
                    session_id="session", after_seq=snapshot["snapshot_seq"]))
                self.assertEqual([event["seq"] for event in newer["events"]],
                                 [2])
                self.assertEqual(newer["next_after_seq"], 2)
                view = read(lambda: mailbox.get_session_view(
                    session_id="session", limit=1))
                self.assertEqual(view["snapshot_seq"], 2)
                self.assertEqual([event["seq"] for event in
                                  view["recent_events"]], [2])
                self.assertEqual(view["history_before_seq"], 2)
                history = read(lambda: mailbox.get_session_events(
                    session_id="session", before_seq=3, limit=1))
                self.assertEqual([event["seq"] for event in history["events"]],
                                 [2])
                self.assertEqual(history["next_before_seq"], 2)
                with self.assertRaisesRegex(EventJournalError,
                                            "cursor_ahead"):
                    read(lambda: mailbox.get_session_events(
                        session_id="session", after_seq=3))
                with self.assertRaisesRegex(EventJournalError,
                                            "invalid_cursor"):
                    read(lambda: mailbox.get_session_events(
                        session_id="session", after_seq=1, before_seq=3))
                with self.assertRaisesRegex(EventJournalError,
                                            "session_not_found"):
                    read(lambda: mailbox.get_session_snapshot(
                        session_id="not-managed"))

    def test_wait_events_returns_committed_event_without_advancing_empty_cursor(self):
        for publish in (False, True):
            with self.subTest(publish=publish):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    with LockedHostJournals(Path(temp)) as startup:
                        self._ready(startup)
                        mailbox = HostCommandMailbox()
                        outcome = {}

                        def caller():
                            outcome["page"] = mailbox.wait_session_events(
                                session_id="session", after_seq=0,
                                wait_ms=150 if not publish else 1000)

                        worker = threading.Thread(target=caller)
                        worker.start()
                        served = 0
                        deadline = time.monotonic() + 2
                        while served == 0 and time.monotonic() < deadline:
                            served += int(mailbox.serve_once(
                                startup, recorded_at="2026-10-26T00:00:01Z",
                                run_starts=None, interrupts=None))
                            time.sleep(0.005)
                        self.assertEqual(served, 1)
                        self.assertTrue(worker.is_alive())
                        mailbox.notify_events()
                        while served < 2 and time.monotonic() < deadline:
                            served += int(mailbox.serve_once(
                                startup, recorded_at="2026-10-26T00:00:02Z",
                                run_starts=None, interrupts=None))
                            time.sleep(0.005)
                        if publish:
                            self.assertEqual(served, 2)
                            self.assertTrue(worker.is_alive())
                        if publish:
                            startup.events.reserve_run_request(
                                "session", "run", actor_id="device",
                                request_id="start", expected_session_version=0,
                                input_text="go",
                                recorded_at="2026-10-26T00:00:02Z")
                            mailbox.notify_events()
                        while worker.is_alive() and time.monotonic() < deadline:
                            mailbox.serve_once(
                                startup, recorded_at="2026-10-26T00:00:03Z",
                                run_starts=None, interrupts=None)
                            time.sleep(0.005)
                        worker.join(timeout=1)
                        self.assertFalse(worker.is_alive())
                        page = outcome["page"]
                        self.assertEqual([event["seq"] for event in
                                          page["events"]], [1] if publish else [])
                        self.assertEqual(page["next_after_seq"],
                                         1 if publish else 0)
                        self.assertEqual(startup.events.snapshot("session")[
                            "snapshot_seq"], 1 if publish else 0)

    def test_wait_events_rejects_invalid_duration(self):
        mailbox = HostCommandMailbox()
        for duration in (-1, 25001, True, 1.5):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(CommandMailboxError,
                                            "invalid_wait_ms"):
                    mailbox.wait_session_events(
                        session_id="session", wait_ms=duration)

    def test_cancelled_wait_returns_no_page_even_if_event_is_ready(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._ready(startup)
                mailbox = HostCommandMailbox()
                cancelled = threading.Event()
                outcome = {}

                def caller():
                    try:
                        outcome["page"] = mailbox.wait_session_events(
                            session_id="session", after_seq=0,
                            wait_ms=5000, cancelled=cancelled)
                    except CommandMailboxError as error:
                        outcome["error"] = str(error)

                worker = threading.Thread(target=caller)
                worker.start()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if mailbox.serve_once(
                            startup, recorded_at="2026-10-26T00:00:01Z",
                            run_starts=None, interrupts=None):
                        break
                    time.sleep(0.005)
                else:
                    self.fail("initial_read_not_served")
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="start",
                    expected_session_version=0, input_text="go",
                    recorded_at="2026-10-26T00:00:02Z")
                mailbox.cancel_wait(cancelled)
                while worker.is_alive() and time.monotonic() < deadline:
                    mailbox.serve_once(
                        startup, recorded_at="2026-10-26T00:00:03Z",
                        run_starts=None, interrupts=None)
                    time.sleep(0.005)
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(outcome, {"error": "request_cancelled"})
                self.assertEqual(startup.events.snapshot("session")[
                    "snapshot_seq"], 1)

    def test_pre_cancelled_wait_never_queues_a_read(self):
        mailbox = HostCommandMailbox()
        cancelled = threading.Event()
        mailbox.cancel_wait(cancelled)
        with self.assertRaisesRegex(CommandMailboxError,
                                    "request_cancelled"):
            mailbox.wait_session_events(
                session_id="session", wait_ms=25000,
                cancelled=cancelled)
        self.assertFalse(mailbox.serve_once(
            None, recorded_at="2026-10-26T00:00:00Z",
            run_starts=None, interrupts=None))
        with self.assertRaisesRegex(CommandMailboxError,
                                    "invalid_wait_cancellation"):
            mailbox.cancel_wait(None)

    def test_queued_read_cancels_before_owner_touches_storage(self):
        mailbox = HostCommandMailbox()
        cancelled = threading.Event()
        outcome = {}

        def caller():
            try:
                mailbox.wait_session_events(
                    session_id="session", wait_ms=5000,
                    cancelled=cancelled)
            except CommandMailboxError as error:
                outcome["error"] = str(error)

        worker = threading.Thread(target=caller)
        worker.start()
        deadline = time.monotonic() + 2
        while mailbox._items.qsize() == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(mailbox._items.qsize(), 1)
        mailbox.cancel_wait(cancelled)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, {"error": "request_cancelled"})
        self.assertTrue(mailbox.serve_once(
            None, recorded_at="2026-10-26T00:00:00Z",
            run_starts=None, interrupts=None))

    def test_owner_loop_serves_query_without_moving_sqlite_connection(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._ready(startup)
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="start",
                    expected_session_version=0, input_text="go",
                    recorded_at="2026-10-26T00:00:01Z")
                owner = launch_owned_upstream(
                    startup, epoch=epoch,
                    command=[sys.executable, str(PEER), "thread_bootstrap_good"],
                    cwd=ROOT, env=dict(os.environ))
                mailbox = HostCommandMailbox()
                stop = threading.Event()
                outcome = {}

                def caller():
                    outcome["operation"] = mailbox.get_operation(
                        kind="run", actor_id="device", request_id="start")
                    stop.set()

                worker = threading.Thread(target=caller)
                worker.start()
                result = run_owned_event_loop(
                    owner, stop=stop, poll_timeout=0.05, commands=mailbox)
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result["counts"]["command_served"], 1)
                self.assertEqual(outcome["operation"]["resource_id"], "run")
                with self.assertRaisesRegex(CommandMailboxError, "host_stopped"):
                    mailbox.get_session_snapshot(session_id="session")


if __name__ == "__main__":
    unittest.main()
