"""DEV-007 Host slices: session list, run approvals and the timeline budget."""

import os
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.event_journal import (
    EventJournalError, MAX_RUN_TIMELINE_BYTES, MAX_TIMELINE_CHUNK_BYTES,
)
from runvori.infrastructure.locked_journal import LockedHostJournals

from approval_fixtures import reviewable_command, seed_workspace_session


@unittest.skipUnless(os.name == "nt", "Windows startup lock")
class SessionListTests(unittest.TestCase):
    @staticmethod
    def _epoch(startup):
        return startup.recover_host_after_isolation(
            old_process_epoch=0, expected_run_id=None,
            expected_run_version=None, reason="host_restart",
            recorded_at="2026-10-27T00:00:00Z")["epoch"]

    def test_list_is_cursor_stable_and_reports_latest_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._epoch(startup)
                startup.register_workspace("ws", startup.data_dir,
                                           sandbox="read-only", epoch=epoch)
                for name in ("a", "b", "c"):
                    startup.create_session_in_workspace(
                        name, "ws", {"title": f"t-{name}"}, epoch=epoch)
                first = startup.events.list_sessions(limit=2)
                self.assertEqual([i["session_id"] for i in first["items"]],
                                 ["a", "b"])
                self.assertEqual(first["next_cursor"], "b")
                self.assertEqual(first["items"][0]["title"], "t-a")
                self.assertEqual(first["items"][0]["workspace_name"],
                                 Path(temp).name)
                second = startup.events.list_sessions(
                    after_session_id=first["next_cursor"], limit=2)
                self.assertEqual([i["session_id"] for i in second["items"]],
                                 ["c"])
                self.assertIsNone(second["next_cursor"])
                # Appending events must not shift an existing page boundary.
                startup.events.reserve_run(
                    "a", "run-a", expected_session_version=0,
                    recorded_at="2026-10-27T00:00:01Z")
                again = startup.events.list_sessions(limit=2)
                self.assertEqual([i["session_id"] for i in again["items"]],
                                 ["a", "b"])
                self.assertEqual(again["items"][0]["latest_run"]["run_id"],
                                 "run-a")
                self.assertEqual(again["items"][0]["active_run"]["status"],
                                 "starting")

    def test_pending_approval_count_and_filtered_run_approvals(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._epoch(startup)
                seed_workspace_session(startup, epoch)
                startup.events.reserve_run_request(
                    "session", "run", actor_id="device", request_id="req",
                    expected_session_version=0, input_text="continue",
                    recorded_at="2026-10-27T00:00:01Z")
                startup.events.claim_run_dispatch(
                    "device", "req", process_epoch=epoch,
                    recorded_at="2026-10-27T00:00:02Z")
                startup.events.confirm_run_started(
                    "device", "req", process_epoch=epoch,
                    thread_id="thread", turn_id="turn",
                    recorded_at="2026-10-27T00:00:03Z")
                startup.record_approval_request(
                    "session", "run", approval_id="approval", epoch=epoch,
                    upstream_request_id="upstream", kind="command",
                    details=reviewable_command(startup.data_dir),
                    turn_id="turn",
                    recorded_at="2026-10-27T00:00:04Z")
                summary = startup.events.list_sessions()["items"][0]
                self.assertEqual(summary["pending_approval_count"], 1)
                self.assertEqual(summary["active_run"]["status"],
                                 "waiting_approval")
                self.assertEqual(summary["updated_at"],
                                 "2026-10-27T00:00:04Z")
                pending = startup.list_run_approvals("run", status="pending")
                self.assertEqual([i["approval_id"] for i in pending["items"]],
                                 ["approval"])
                self.assertEqual(
                    startup.list_run_approvals(
                        "run", status="resolved")["items"], [])
                with self.assertRaisesRegex(EventJournalError, "run_not_found"):
                    startup.list_run_approvals("missing")
                with self.assertRaisesRegex(EventJournalError, "invalid_request"):
                    startup.list_run_approvals("run", status="all")


@unittest.skipUnless(os.name == "nt", "Windows startup lock")
class TimelineTests(unittest.TestCase):
    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0, expected_run_id=None,
            expected_run_version=None, reason="host_restart",
            recorded_at="2026-10-27T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run("session", "run",
                                   expected_session_version=0,
                                   recorded_at="2026-10-27T00:00:01Z")
        return epoch

    def test_timeline_advances_seq_but_not_the_snapshot_or_versions(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._running(startup)
                before = startup.events.snapshot("session")
                startup.events.append_timeline(
                    "session", "run", kind="message.delta",
                    payload={"item_id": "m1", "chunk": 0}, text="你",
                    recorded_at="2026-10-27T00:00:02Z")
                startup.events.append_timeline(
                    "session", "run", kind="command.output",
                    payload={"item_id": "c1", "chunk": 0}, text="ok\n",
                    recorded_at="2026-10-27T00:00:03Z")
                after = startup.events.snapshot("session")
                # The cursor advances; control state and the stored snapshot
                # do not, so open approvals cannot conflict from new output.
                self.assertEqual(after.pop("snapshot_seq"),
                                 before.pop("snapshot_seq") + 2)
                self.assertEqual(after, before)
                page = startup.events.page("session", after_seq=1, limit=10)
                self.assertEqual([e["type"] for e in page["events"]],
                                 ["message.delta", "command.output"])
                self.assertEqual(page["events"][0]["payload"]["text"], "你")
                self.assertEqual(page["latest_seq"], 3)

    def test_timeline_requires_an_active_bound_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._running(startup)
                with self.assertRaisesRegex(EventJournalError, "run_not_found"):
                    startup.events.append_timeline(
                        "session", "other", kind="message.delta",
                        payload={"item_id": "m1"}, text="x",
                        recorded_at="2026-10-27T00:00:02Z")
                with self.assertRaisesRegex(EventJournalError,
                                            "timeline_chunk_too_large"):
                    startup.events.append_timeline(
                        "session", "run", kind="message.delta",
                        payload={"item_id": "m1"},
                        text="x" * (MAX_TIMELINE_CHUNK_BYTES + 1),
                        recorded_at="2026-10-27T00:00:02Z")
                with self.assertRaisesRegex(EventJournalError,
                                            "invalid_timeline_event"):
                    startup.events.append_timeline(
                        "session", "run", kind="message.completed",
                        payload={"item_id": "m1"}, text="x",
                        recorded_at="2026-10-27T00:00:02Z")
                startup.events.finish_run(
                    "run", expected_version=1, terminal="completed",
                    recorded_at="2026-10-27T00:00:04Z")
                with self.assertRaisesRegex(EventJournalError, "run_not_active"):
                    startup.events.append_timeline(
                        "session", "run", kind="message.delta",
                        payload={"item_id": "m1"}, text="late",
                        recorded_at="2026-10-27T00:00:05Z")

    def test_budget_cuts_text_once_and_records_the_gap(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                self._running(startup)
                chunk = "y" * MAX_TIMELINE_CHUNK_BYTES
                count = MAX_RUN_TIMELINE_BYTES // MAX_TIMELINE_CHUNK_BYTES
                for index in range(count):
                    startup.events.append_timeline(
                        "session", "run", kind="message.delta",
                        payload={"item_id": "m1", "chunk": index}, text=chunk,
                        recorded_at="2026-10-27T00:00:02Z")
                overflow = startup.events.append_timeline(
                    "session", "run", kind="message.delta",
                    payload={"item_id": "m1", "chunk": count}, text="tail",
                    recorded_at="2026-10-27T00:00:03Z")
                self.assertTrue(overflow["payload"]["text_omitted"])
                self.assertEqual(overflow["payload"]["dropped_bytes"], 4)
                self.assertNotIn("text", overflow["payload"])
                later = startup.events.append_timeline(
                    "session", "run", kind="message.delta",
                    payload={"item_id": "m1", "chunk": count + 1}, text="more",
                    recorded_at="2026-10-27T00:00:04Z")
                self.assertTrue(later["payload"]["text_omitted"])
                saved = startup.events.append_timeline(
                    "session", "run", kind="command.completed",
                    payload={"item_id": "c1"}, text=None,
                    recorded_at="2026-10-27T00:00:05Z")
                self.assertNotIn("text_omitted", saved["payload"])
                # The gap is announced exactly once, where it starts: the
                # marker must not repeat for every later omitted chunk.
                page = startup.events.page(
                    "session", after_seq=overflow["seq"] - 1, limit=100)
                markers = [event for event in page["events"]
                           if event["type"] == "timeline.truncated"]
                self.assertEqual(len(markers), 1)
                self.assertEqual(markers[0]["payload"]["reason"],
                                 "run_timeline_budget")
                self.assertEqual(markers[0]["payload"]["dropped_bytes"], 4)
                # The marker sits right after the event that opened the gap.
                self.assertLess(overflow["seq"], markers[0]["seq"])
                self.assertLess(markers[0]["seq"], later["seq"])


if __name__ == "__main__":
    unittest.main()