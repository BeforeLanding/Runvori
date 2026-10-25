"""Behavioral checks for committed event and snapshot boundaries."""

import json
import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.event_journal import EventJournal, EventJournalError


class EventJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".dev")
        self.path = Path(self.temp.name) / "events.db"
        with EventJournal(self.path) as journal:
            journal.create_session("session", {"status": "starting"})

    def tearDown(self):
        self.temp.cleanup()

    def append(self, journal, n, *, payload=None):
        return journal.append("session", run_id="run", kind="message.delta",
                              payload=payload or {"part": n},
                              recorded_at="2026-10-25T00:00:00Z",
                              snapshot={"status": "running", "part": n})

    def test_append_commits_event_and_snapshot_together_across_reopen(self):
        with EventJournal(self.path) as journal:
            first = self.append(journal, 1)
            self.assertEqual(first["seq"], 1)
        with EventJournal(self.path) as reopened:
            self.assertEqual(reopened.snapshot("session"),
                             {"status": "running", "part": 1, "snapshot_seq": 1})
            self.assertEqual(reopened.page("session")["events"], [first])

    def test_invalid_or_oversized_event_does_not_advance_snapshot_or_seq(self):
        with EventJournal(self.path) as journal:
            with self.assertRaisesRegex(EventJournalError, "event_too_large"):
                self.append(journal, 1, payload={"text": "x" * 65536})
            self.assertEqual(journal.snapshot("session"),
                             {"status": "starting", "snapshot_seq": 0})
            self.assertEqual(self.append(journal, 2)["seq"], 1)

    def test_snapshot_update_failure_rolls_back_inserted_event(self):
        with EventJournal(self.path) as journal:
            journal.connection.execute("""
                CREATE TRIGGER block_snapshot BEFORE UPDATE ON sessions
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                self.append(journal, 1)
            self.assertEqual(journal.page("session")["events"], [])
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 0)
            journal.connection.execute("DROP TRIGGER block_snapshot")
            self.assertEqual(self.append(journal, 2)["seq"], 1)

    def test_two_connections_allocate_contiguous_sequences(self):
        def write(n):
            with EventJournal(self.path) as journal:
                return self.append(journal, n)["seq"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, range(20))), list(range(1, 21)))
        with EventJournal(self.path) as journal:
            page = journal.page("session")
            self.assertEqual([event["seq"] for event in page["events"]], list(range(1, 21)))
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 20)

    def test_incremental_and_history_pages_use_exclusive_cursors(self):
        with EventJournal(self.path) as journal:
            for n in range(1, 6):
                self.append(journal, n)
            forward = journal.page("session", after_seq=1, limit=2)
            self.assertEqual([e["seq"] for e in forward["events"]], [2, 3])
            self.assertEqual(forward["next_after_seq"], 3)
            self.assertTrue(forward["has_more"])
            history = journal.page("session", before_seq=6, limit=2)
            self.assertEqual([e["seq"] for e in history["events"]], [4, 5])
            self.assertEqual(history["next_before_seq"], 4)
            self.assertTrue(history["has_more"])
            self.assertEqual([e["seq"] for e in journal.page(
                "session", before_seq=history["next_before_seq"], limit=2)["events"]],
                             [2, 3])
            self.assertEqual(journal.page("session", after_seq=5)["next_after_seq"], 5)

    def test_cursor_rejections_and_unknown_session(self):
        with EventJournal(self.path) as journal:
            for args, code in [({"after_seq": -1}, "invalid_cursor"),
                               ({"after_seq": 1}, "cursor_ahead"),
                               ({"before_seq": 2}, "cursor_ahead"),
                               ({"before_seq": 1, "after_seq": 0}, "invalid_cursor")]:
                with self.assertRaisesRegex(EventJournalError, code):
                    journal.page("session", **args)
            with self.assertRaisesRegex(EventJournalError, "session_not_found"):
                journal.snapshot("missing")

    def test_page_is_bounded_by_bytes_as_well_as_count(self):
        with EventJournal(self.path) as journal:
            for n in range(6):
                self.append(journal, n, payload={"text": "x" * 50000})
            first = journal.page("session", limit=100)
            self.assertEqual(len(first["events"]), 5)
            self.assertTrue(first["has_more"])
            self.assertLessEqual(len(json.dumps(first).encode("utf-8")), 256 * 1024)
            second = journal.page("session", after_seq=first["next_after_seq"])
            self.assertEqual([e["seq"] for e in second["events"]], [6])

    def test_global_slot_rejects_second_session_until_terminal_commit(self):
        with EventJournal(self.path) as journal:
            journal.create_session("other", {"status": "idle"})
            created = journal.reserve_run("session", "run1",
                                          expected_session_version=0,
                                          recorded_at="2026-10-25T00:00:00Z")
            self.assertEqual(created["type"], "run.created")
            self.assertEqual(journal.snapshot("session")["active_run"]["run_id"], "run1")
            with self.assertRaisesRegex(EventJournalError, "active_run_exists"):
                journal.reserve_run("other", "run2", expected_session_version=0,
                                    recorded_at="2026-10-25T00:00:01Z")
            self.assertEqual(journal.snapshot("other")["snapshot_seq"], 0)
        with EventJournal(self.path) as reopened:
            finished = reopened.finish_run("run1", expected_version=1,
                                           terminal="completed",
                                           recorded_at="2026-10-25T00:00:02Z")
            self.assertEqual(finished["type"], "run.completed")
            self.assertIsNone(reopened.snapshot("session")["active_run"])
            self.assertEqual(reopened.snapshot("session")["session_version"], 2)
            reopened.reserve_run("other", "run2", expected_session_version=0,
                                 recorded_at="2026-10-25T00:00:03Z")

    def test_two_connections_can_reserve_only_one_global_run(self):
        with EventJournal(self.path) as journal:
            journal.create_session("other", {})

        def reserve(pair):
            session_id, run_id = pair
            with EventJournal(self.path) as journal:
                try:
                    return journal.reserve_run(
                        session_id, run_id, expected_session_version=0,
                        recorded_at="2026-10-25T00:00:00Z")["type"]
                except EventJournalError as error:
                    return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, [("session", "run1"), ("other", "run2")]))
        self.assertEqual(results.count("run.created"), 1)
        self.assertEqual(results.count("active_run_exists"), 1)

    def test_terminal_is_idempotent_and_cannot_release_later_run(self):
        with EventJournal(self.path) as journal:
            journal.reserve_run("session", "run1", expected_session_version=0,
                                recorded_at="2026-10-25T00:00:00Z")
            first = journal.finish_run("run1", expected_version=1,
                                       terminal="interrupted",
                                       recorded_at="2026-10-25T00:00:01Z")
            journal.reserve_run("session", "run2", expected_session_version=2,
                                recorded_at="2026-10-25T00:00:02Z")
            repeat = journal.finish_run("run1", expected_version=1,
                                        terminal="interrupted",
                                        recorded_at="2026-10-25T00:00:03Z")
            self.assertEqual(repeat, first)
            self.assertEqual(journal.snapshot("session")["active_run"]["run_id"], "run2")
            with self.assertRaisesRegex(EventJournalError, "terminal_conflict"):
                journal.finish_run("run1", expected_version=1, terminal="completed",
                                   recorded_at="2026-10-25T00:00:04Z")
            self.assertEqual([e["type"] for e in journal.page("session")["events"]],
                             ["run.created", "run.interrupted", "run.created"])

    def test_stale_session_version_and_failed_append_do_not_occupy_slot(self):
        with EventJournal(self.path) as journal:
            with self.assertRaisesRegex(EventJournalError, "resource_changed"):
                journal.reserve_run("session", "run1", expected_session_version=1,
                                    recorded_at="2026-10-25T00:00:00Z")
            journal.connection.execute("""
                CREATE TRIGGER block_event BEFORE INSERT ON events
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                journal.reserve_run("session", "run1", expected_session_version=0,
                                    recorded_at="2026-10-25T00:00:00Z")
            self.assertIsNone(journal.connection.execute(
                "SELECT run_id FROM active_slot").fetchone())
            self.assertIsNone(journal.connection.execute(
                "SELECT run_id FROM runs").fetchone())
            journal.connection.execute("DROP TRIGGER block_event")
            journal.reserve_run("session", "run1", expected_session_version=0,
                                recorded_at="2026-10-25T00:00:00Z")

    def test_terminal_write_failure_preserves_run_and_slot(self):
        with EventJournal(self.path) as journal:
            journal.reserve_run("session", "run1", expected_session_version=0,
                                recorded_at="2026-10-25T00:00:00Z")
            journal.connection.execute("""
                CREATE TRIGGER block_terminal BEFORE INSERT ON events
                WHEN NEW.seq = 2
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                journal.finish_run("run1", expected_version=1, terminal="completed",
                                   recorded_at="2026-10-25T00:00:01Z")
            self.assertEqual(journal.connection.execute(
                "SELECT status FROM runs WHERE run_id='run1'").fetchone()[0], "starting")
            self.assertEqual(journal.connection.execute(
                "SELECT run_id FROM active_slot").fetchone()[0], "run1")
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 1)
            journal.connection.execute("DROP TRIGGER block_terminal")
            self.assertEqual(journal.finish_run(
                "run1", expected_version=1, terminal="completed",
                recorded_at="2026-10-25T00:00:02Z")["seq"], 2)

    def test_v1_database_migrates_without_losing_events(self):
        legacy = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy)
        connection.executescript("""
            CREATE TABLE sessions(session_id TEXT PRIMARY KEY, last_seq INTEGER NOT NULL,
                                  snapshot_json TEXT NOT NULL);
            CREATE TABLE events(session_id TEXT NOT NULL, seq INTEGER NOT NULL,
                                event_json TEXT NOT NULL, event_bytes INTEGER NOT NULL,
                                PRIMARY KEY(session_id, seq));
            INSERT INTO sessions VALUES ('old', 1, '{"status":"running"}');
            INSERT INTO events VALUES ('old', 1, '{"seq":1,"type":"message.delta"}', 32);
            PRAGMA user_version=1;
        """)
        connection.close()
        with EventJournal(legacy) as journal:
            self.assertEqual(journal.snapshot("old")["snapshot_seq"], 1)
            self.assertEqual(journal.page("old")["events"][0]["seq"], 1)
            self.assertEqual(journal.connection.execute("PRAGMA user_version").fetchone()[0], 5)
            journal.reserve_run("old", "run", expected_session_version=0,
                                recorded_at="2026-10-25T00:00:00Z")
            self.assertEqual(journal.snapshot("old")["snapshot_seq"], 2)

    def request(self, journal, *, session_id="session", run_id="run1",
                actor_id="device1", request_id="request1", expected=0,
                input_text="continue"):
        return journal.reserve_run_request(
            session_id, run_id, actor_id=actor_id, request_id=request_id,
            expected_session_version=expected, input_text=input_text,
            recorded_at="2026-10-25T00:00:00Z")

    def test_lost_response_replays_original_operation_even_after_run_finishes(self):
        with EventJournal(self.path) as journal:
            accepted = self.request(journal)
            journal.finish_run("run1", expected_version=1, terminal="completed",
                               recorded_at="2026-10-25T00:00:01Z")
        with EventJournal(self.path) as reopened:
            repeated = self.request(reopened, run_id="new-generated-id")
            self.assertEqual(repeated, accepted)
            self.assertEqual(reopened.get_run_operation("device1", "request1"), accepted)
            with self.assertRaisesRegex(EventJournalError, "operation_not_found"):
                reopened.get_run_operation("device2", "request1")
            self.assertEqual(reopened.connection.execute(
                "SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual([e["type"] for e in reopened.page("session")["events"]],
                             ["run.created", "run.completed"])

    def test_same_request_id_with_different_body_or_path_conflicts(self):
        with EventJournal(self.path) as journal:
            journal.create_session("other", {})
            self.request(journal)
            for changes in ({"input_text": "changed"},
                            {"expected": 1}, {"session_id": "other"}):
                with self.assertRaisesRegex(EventJournalError, "idempotency_conflict"):
                    self.request(journal, **changes)
            with self.assertRaisesRegex(EventJournalError, "active_run_exists"):
                self.request(journal, actor_id="device2", run_id="run2",
                             session_id="other")

    def test_two_connections_replaying_one_request_create_one_run(self):
        def submit(n):
            with EventJournal(self.path) as journal:
                return self.request(journal, run_id=f"run{n}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (1, 2)))
        self.assertEqual(results[0], results[1])
        with EventJournal(self.path) as journal:
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM run_operations").fetchone()[0], 1)

    def test_failed_event_insert_rolls_back_operation_and_slot(self):
        with EventJournal(self.path) as journal:
            journal.connection.execute("""
                CREATE TRIGGER block_request_event BEFORE INSERT ON events
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                self.request(journal)
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM run_operations").fetchone()[0], 0)
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM active_slot").fetchone()[0], 0)
            journal.connection.execute("DROP TRIGGER block_request_event")
            self.assertEqual(self.request(journal)["status"], "accepted")

    def test_utf8_input_limit_applies_before_side_effects(self):
        with EventJournal(self.path) as journal:
            with self.assertRaisesRegex(EventJournalError, "input_too_large"):
                self.request(journal, input_text="汉" * 6000)
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM run_operations").fetchone()[0], 0)
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 0)

    def test_v2_database_adds_operation_table_without_resetting_slot(self):
        legacy = Path(self.temp.name) / "v2.db"
        with EventJournal(legacy) as journal:
            journal.create_session("old", {})
            journal.reserve_run("old", "active", expected_session_version=0,
                                recorded_at="2026-10-25T00:00:00Z")
            journal.connection.execute("DROP TABLE run_operations")
            journal.connection.execute("PRAGMA user_version=2")
        with EventJournal(legacy) as reopened:
            self.assertEqual(reopened.connection.execute(
                "PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(reopened.connection.execute(
                "SELECT run_id FROM active_slot").fetchone()[0], "active")
            with self.assertRaisesRegex(EventJournalError, "active_run_exists"):
                self.request(reopened, session_id="old", run_id="new", expected=1)

    def test_claim_once_then_unknown_never_reclaims_or_releases_slot(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
        with EventJournal(self.path) as reopened:
            claimed = reopened.claim_run_dispatch(
                "device1", "request1", process_epoch=1,
                recorded_at="2026-10-25T00:00:01Z")
            self.assertEqual(claimed["status"], "dispatching")
            with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                reopened.claim_run_dispatch(
                    "device1", "request1", process_epoch=1,
                    recorded_at="2026-10-25T00:00:02Z")
            unknown = reopened.mark_run_dispatch_unknown(
                "device1", "request1", recorded_at="2026-10-25T00:00:03Z")
            self.assertEqual(unknown["status"], "unknown")
            self.assertEqual(reopened.get_run_operation("device1", "request1"), unknown)
            self.assertEqual(self.request(reopened, run_id="new-id"), unknown)
            self.assertEqual(reopened.connection.execute(
                "SELECT run_id FROM active_slot").fetchone()[0], "run1")
            self.assertEqual(reopened.snapshot("session")["session_version"], 3)
            self.assertEqual([e["type"] for e in reopened.page("session")["events"]],
                             ["run.created", "operation.updated", "operation.updated"])
        with EventJournal(self.path) as again:
            with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                again.claim_run_dispatch(
                    "device1", "request1", process_epoch=1,
                    recorded_at="2026-10-25T00:00:04Z")

    def test_two_connections_cannot_both_claim_dispatch(self):
        with EventJournal(self.path) as journal:
            self.request(journal)

        def claim(_):
            with EventJournal(self.path) as journal:
                try:
                    return journal.claim_run_dispatch(
                        "device1", "request1", process_epoch=1,
                        recorded_at="2026-10-25T00:00:01Z")["status"]
                except EventJournalError as error:
                    return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(results.count("dispatching"), 1)
        self.assertEqual(results.count("dispatch_unavailable"), 1)
        with EventJournal(self.path) as journal:
            self.assertEqual(len(journal.page("session")["events"]), 2)

    def test_dispatch_event_failure_rolls_back_claim(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.connection.execute("""
                CREATE TRIGGER block_dispatch BEFORE INSERT ON events
                WHEN NEW.seq = 2
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                journal.claim_run_dispatch(
                    "device1", "request1", process_epoch=1,
                    recorded_at="2026-10-25T00:00:01Z")
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "accepted")
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 1)
            journal.connection.execute("DROP TRIGGER block_dispatch")
            self.assertEqual(journal.claim_run_dispatch(
                "device1", "request1", process_epoch=1,
                recorded_at="2026-10-25T00:00:02Z")
                ["status"], "dispatching")

    def test_unknown_event_failure_keeps_dispatching_not_retriable(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch(
                "device1", "request1", process_epoch=1,
                recorded_at="2026-10-25T00:00:01Z")
            journal.connection.execute("""
                CREATE TRIGGER block_unknown BEFORE INSERT ON events
                WHEN NEW.seq = 3
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                journal.mark_run_dispatch_unknown(
                    "device1", "request1", recorded_at="2026-10-25T00:00:02Z")
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "dispatching")
            with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                journal.claim_run_dispatch(
                    "device1", "request1", process_epoch=1,
                    recorded_at="2026-10-25T00:00:03Z")
            journal.connection.execute("DROP TRIGGER block_unknown")
            self.assertEqual(journal.mark_run_dispatch_unknown(
                "device1", "request1", recorded_at="2026-10-25T00:00:04Z")
                ["status"], "unknown")

    def test_unknown_requires_claim_and_terminal_run_cannot_dispatch(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                journal.mark_run_dispatch_unknown(
                    "device1", "request1", recorded_at="2026-10-25T00:00:01Z")
            journal.finish_run("run1", expected_version=1, terminal="interrupted",
                               recorded_at="2026-10-25T00:00:02Z")
            with self.assertRaisesRegex(EventJournalError, "run_terminal"):
                journal.claim_run_dispatch(
                    "device1", "request1", process_epoch=1,
                    recorded_at="2026-10-25T00:00:03Z")
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "accepted")

    def confirm(self, journal, *, epoch=1, thread_id="thread1", turn_id="turn1"):
        return journal.confirm_run_started(
            "device1", "request1", process_epoch=epoch,
            thread_id=thread_id, turn_id=turn_id,
            recorded_at="2026-10-25T00:00:02Z")

    def test_matching_turn_confirms_operation_and_running_snapshot_once(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=7,
                                       recorded_at="2026-10-25T00:00:01Z")
            confirmed = self.confirm(journal, epoch=7)
            self.assertEqual(confirmed["status"], "confirmed")
            self.assertEqual(confirmed["outcome"], "turn_started")
            self.assertEqual(self.confirm(journal, epoch=7), confirmed)
            self.assertEqual(journal.get_run_operation("device1", "request1"), confirmed)
            self.assertEqual(journal.snapshot("session")["active_run"],
                             {"run_id": "run1", "status": "running", "version": 2})
            self.assertEqual(journal.snapshot("session")["session_version"], 3)
            self.assertEqual([e["type"] for e in journal.page("session")["events"]],
                             ["run.created", "operation.updated", "run.started",
                              "operation.updated"])
            journal.finish_run("run1", expected_version=2, terminal="completed",
                               recorded_at="2026-10-25T00:00:03Z")
            self.assertIsNone(journal.snapshot("session")["active_run"])

    def test_late_turn_evidence_can_resolve_unknown_without_reclaim(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=2,
                                       recorded_at="2026-10-25T00:00:01Z")
            journal.mark_run_dispatch_unknown(
                "device1", "request1", recorded_at="2026-10-25T00:00:02Z")
        with EventJournal(self.path) as reopened:
            self.assertEqual(self.confirm(reopened, epoch=2)["status"], "confirmed")
            self.assertEqual(reopened.connection.execute(
                "SELECT process_epoch FROM upstream_turns").fetchone()[0], 2)
            with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                reopened.claim_run_dispatch("device1", "request1", process_epoch=2,
                                            recorded_at="2026-10-25T00:00:03Z")

    def test_unclaimed_wrong_epoch_and_conflicting_turn_are_rejected(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                self.confirm(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=3,
                                       recorded_at="2026-10-25T00:00:01Z")
            with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                self.confirm(journal, epoch=2)
            self.confirm(journal, epoch=3)
            with self.assertRaisesRegex(EventJournalError, "upstream_turn_conflict"):
                self.confirm(journal, epoch=3, turn_id="other")
            self.assertEqual(len(journal.page("session")["events"]), 4)

    def test_turn_binding_cannot_be_reused_by_later_run_in_same_epoch(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=4,
                                       recorded_at="2026-10-25T00:00:01Z")
            self.confirm(journal, epoch=4)
            journal.finish_run("run1", expected_version=2, terminal="completed",
                               recorded_at="2026-10-25T00:00:03Z")
            self.request(journal, run_id="run2", request_id="request2", expected=4)
            journal.claim_run_dispatch("device1", "request2", process_epoch=4,
                                       recorded_at="2026-10-25T00:00:04Z")
            with self.assertRaisesRegex(EventJournalError, "upstream_turn_conflict"):
                journal.confirm_run_started(
                    "device1", "request2", process_epoch=4,
                    thread_id="thread1", turn_id="turn1",
                    recorded_at="2026-10-25T00:00:05Z")
            self.assertEqual(journal.connection.execute(
                "SELECT status FROM runs WHERE run_id='run2'").fetchone()[0], "starting")

    def test_failed_started_event_rolls_back_binding_and_confirmation(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=1,
                                       recorded_at="2026-10-25T00:00:01Z")
            journal.connection.execute("""
                CREATE TRIGGER block_started BEFORE INSERT ON events
                WHEN NEW.seq = 3
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                self.confirm(journal)
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "dispatching")
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM upstream_turns").fetchone()[0], 0)
            self.assertEqual(journal.snapshot("session")["active_run"]["status"],
                             "starting")
            journal.connection.execute("DROP TRIGGER block_started")
            self.assertEqual(self.confirm(journal)["status"], "confirmed")

    def test_failed_operation_event_rolls_back_started_event_too(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=1,
                                       recorded_at="2026-10-25T00:00:01Z")
            journal.connection.execute("""
                CREATE TRIGGER block_confirmation BEFORE INSERT ON events
                WHEN NEW.seq = 4
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                self.confirm(journal)
            self.assertEqual([e["type"] for e in journal.page("session")["events"]],
                             ["run.created", "operation.updated"])
            self.assertEqual(journal.snapshot("session")["active_run"]["status"],
                             "starting")
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "dispatching")
            self.assertEqual(journal.connection.execute(
                "SELECT COUNT(*) FROM upstream_turns").fetchone()[0], 0)

    def test_v3_database_adds_turn_binding_without_resetting_operation(self):
        legacy = Path(self.temp.name) / "v3.db"
        with EventJournal(legacy) as journal:
            journal.create_session("old", {})
            journal.reserve_run_request(
                "old", "run", actor_id="device1", request_id="request1",
                expected_session_version=0, input_text="continue",
                recorded_at="2026-10-25T00:00:00Z")
            journal.connection.execute("DROP TABLE upstream_turns")
            journal.connection.execute("PRAGMA user_version=3")
        with EventJournal(legacy) as reopened:
            self.assertEqual(reopened.connection.execute(
                "PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(reopened.get_run_operation("device1", "request1")["status"],
                             "accepted")
            self.assertIsNone(reopened.connection.execute(
                "SELECT dispatch_epoch FROM run_operations").fetchone()[0])

    def recover(self, journal, *, run_id="run1", version=1, epoch=1,
                isolated=True, reason="host_restart"):
        return journal.recover_run_after_isolation(
            run_id, expected_version=version, old_process_epoch=epoch,
            old_process_isolated=isolated, reason=reason,
            recorded_at="2026-10-25T00:00:05Z")

    def test_unisolated_process_keeps_slot_and_accepted_request(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            with self.assertRaisesRegex(EventJournalError, "old_process_not_isolated"):
                self.recover(journal, isolated=False)
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "accepted")
            self.assertEqual(journal.connection.execute(
                "SELECT run_id FROM active_slot").fetchone()[0], "run1")
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 1)

    def test_accepted_request_fails_and_run_interrupts_after_isolation(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
        with EventJournal(self.path) as reopened:
            interrupted = self.recover(reopened)
            self.assertEqual(interrupted["type"], "run.interrupted")
            self.assertEqual(interrupted["payload"]["reason"], "host_restart")
            self.assertEqual(reopened.get_run_operation("device1", "request1")["status"],
                             "failed")
            self.assertEqual(reopened.get_run_operation("device1", "request1")["outcome"],
                             "not_dispatched")
            self.assertIsNone(reopened.snapshot("session")["active_run"])
            self.assertEqual(reopened.snapshot("session")["session_version"], 3)
            self.assertEqual([e["type"] for e in reopened.page("session")["events"]],
                             ["run.created", "operation.updated", "run.interrupted"])
            self.assertEqual(self.recover(reopened), interrupted)
            with self.assertRaisesRegex(EventJournalError, "recovery_conflict"):
                self.recover(reopened, reason="upstream_exit")
            reopened.reserve_run("session", "run2", expected_session_version=3,
                                 recorded_at="2026-10-25T00:00:06Z")
            self.assertEqual(reopened.snapshot("session")["active_run"]["run_id"],
                             "run2")

    def test_claimed_request_becomes_unknown_only_for_matching_epoch(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=7,
                                       recorded_at="2026-10-25T00:00:01Z")
            with self.assertRaisesRegex(EventJournalError, "stale_epoch"):
                self.recover(journal, epoch=6)
            self.assertEqual(journal.snapshot("session")["active_run"]["run_id"],
                             "run1")
        with EventJournal(self.path) as reopened:
            self.recover(reopened, epoch=7)
            operation = reopened.get_run_operation("device1", "request1")
            self.assertEqual(operation["status"], "unknown")
            self.assertEqual(operation["outcome"], "dispatch_unconfirmed")
            with self.assertRaisesRegex(EventJournalError, "dispatch_unavailable"):
                reopened.claim_run_dispatch("device1", "request1", process_epoch=8,
                                            recorded_at="2026-10-25T00:00:07Z")
            self.assertEqual([e["type"] for e in reopened.page("session")["events"][-2:]],
                             ["operation.updated", "run.interrupted"])

    def test_unknown_and_confirmed_facts_remain_distinct_on_recovery(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.claim_run_dispatch("device1", "request1", process_epoch=2,
                                       recorded_at="2026-10-25T00:00:01Z")
            journal.mark_run_dispatch_unknown(
                "device1", "request1", recorded_at="2026-10-25T00:00:02Z")
            self.recover(journal, epoch=2)
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "unknown")
        with EventJournal(self.path) as journal:
            journal.reserve_run_request(
                "session", "run2", actor_id="device1", request_id="request2",
                expected_session_version=4, input_text="continue",
                recorded_at="2026-10-25T00:00:03Z")
            journal.claim_run_dispatch("device1", "request2", process_epoch=3,
                                       recorded_at="2026-10-25T00:00:04Z")
            journal.confirm_run_started(
                "device1", "request2", process_epoch=3,
                thread_id="thread2", turn_id="turn2",
                recorded_at="2026-10-25T00:00:05Z")
            self.recover(journal, run_id="run2", version=2, epoch=3)
            self.assertEqual(journal.get_run_operation("device1", "request2")["status"],
                             "confirmed")
            self.assertEqual(journal.get_run_operation("device1", "request2")["outcome"],
                             "turn_started")
            self.assertEqual(journal.connection.execute(
                "SELECT status FROM runs WHERE run_id='run2'").fetchone()[0],
                             "interrupted")

    def test_recovery_event_failure_rolls_back_operation_and_slot(self):
        with EventJournal(self.path) as journal:
            self.request(journal)
            journal.connection.execute("""
                CREATE TRIGGER block_interrupt BEFORE INSERT ON events
                WHEN NEW.seq = 3
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                self.recover(journal)
            self.assertEqual(journal.get_run_operation("device1", "request1")["status"],
                             "accepted")
            self.assertEqual(journal.connection.execute(
                "SELECT run_id FROM active_slot").fetchone()[0], "run1")
            self.assertEqual(journal.snapshot("session")["snapshot_seq"], 1)
            journal.connection.execute("DROP TRIGGER block_interrupt")
            self.assertEqual(self.recover(journal)["seq"], 3)

    def test_stale_recovery_cannot_release_newer_run(self):
        with EventJournal(self.path) as journal:
            journal.reserve_run("session", "run1", expected_session_version=0,
                                recorded_at="2026-10-25T00:00:00Z")
            journal.finish_run("run1", expected_version=1, terminal="completed",
                               recorded_at="2026-10-25T00:00:01Z")
            journal.reserve_run("session", "run2", expected_session_version=2,
                                recorded_at="2026-10-25T00:00:02Z")
            with self.assertRaisesRegex(EventJournalError, "recovery_unavailable"):
                self.recover(journal)
            with self.assertRaisesRegex(EventJournalError, "resource_changed"):
                self.recover(journal, run_id="run2", version=2)
            self.assertEqual(journal.connection.execute(
                "SELECT run_id FROM active_slot").fetchone()[0], "run2")


if __name__ == "__main__":
    unittest.main()
