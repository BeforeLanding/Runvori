"""Projected timeline events from observed items and streamed deltas."""

import os
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.event_handoff import route_observed_event
from runvori.codex.item_events import chunk_text
from runvori.infrastructure.locked_journal import LockedHostJournals


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class TimelineProjectionTests(unittest.TestCase):
    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0, expected_run_id=None,
            expected_run_version=None, reason="host_restart",
            recorded_at="2026-10-27T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="start",
            expected_session_version=0, input_text="go",
            recorded_at="2026-10-27T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "start", process_epoch=epoch,
            recorded_at="2026-10-27T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "start", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-27T00:00:03Z")
        return epoch

    @staticmethod
    def _types(startup):
        return [event["type"] for event in
                startup.events.page("session", after_seq=0, limit=100)["events"]]

    def test_command_and_message_items_become_timeline_events(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                route_observed_event(
                    startup, {"method": "item/started", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "item": {"id": "cmd", "type": "commandExecution",
                                 "command": "python --version",
                                 "cwd": str(startup.data_dir),
                                 "commandActions": [],
                                 "status": "inProgress"}}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:04Z")
                route_observed_event(
                    startup, {"method": "item/commandExecution/outputDelta",
                              "params": {"threadId": "thread_demo",
                                         "turnId": "turn_demo",
                                         "itemId": "cmd",
                                         "delta": "Python 3.12\n"}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:05Z")
                route_observed_event(
                    startup, {"method": "item/completed", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "item": {"id": "cmd", "type": "commandExecution",
                                 "command": "python --version",
                                 "cwd": str(startup.data_dir),
                                 "commandActions": [],
                                 "status": "completed", "exitCode": 0,
                                 "durationMs": 20,
                                 "aggregatedOutput": "Python 3.12\n"}}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:06Z")
                route_observed_event(
                    startup, {"method": "item/started", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "item": {"id": "msg", "type": "agentMessage",
                                 "text": ""}}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:07Z")
                route_observed_event(
                    startup, {"method": "item/agentMessage/delta", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "itemId": "msg", "delta": "完成"}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:08Z")
                route_observed_event(
                    startup, {"method": "item/completed", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "item": {"id": "msg", "type": "agentMessage",
                                 "text": "完成"}}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:09Z")
                events = startup.events.page(
                    "session", after_seq=0, limit=100)["events"]
                self.assertEqual(
                    [event["type"] for event in events],
                    ["run.created", "operation.updated", "run.started",
                     "operation.updated",
                     "command.started", "command.output", "command.completed",
                     "message.started", "message.delta", "message.completed"])
                started = events[4]["payload"]
                self.assertEqual(started["command"], "python --version")
                self.assertEqual(started["item_id"], "cmd")
                self.assertEqual(events[5]["payload"]["text"], "Python 3.12\n")
                completed = events[6]["payload"]
                self.assertEqual(completed["exit_code"], 0)
                self.assertFalse(completed["output_omitted"])
                # Streamed text exists, so the final item text is not repeated.
                self.assertFalse(events[9]["payload"]["text_omitted"])
                self.assertEqual(
                    [event["payload"].get("text") for event in events
                     if event["type"] == "message.delta"], ["完成"])
                # Output never bumps the control version.
                self.assertEqual(
                    startup.events.snapshot("session")["session_version"], 3)

    def test_item_text_is_stored_in_chunks_when_no_deltas_arrived(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                text = "汉" * 6000  # 18,000 UTF-8 bytes
                route_observed_event(
                    startup, {"method": "item/completed", "params": {
                        "threadId": "thread_demo", "turnId": "turn_demo",
                        "item": {"id": "msg", "type": "agentMessage",
                                 "text": text}}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:04Z")
                events = startup.events.page(
                    "session", after_seq=0, limit=100)["events"]
                deltas = [event for event in events
                          if event["type"] == "message.delta"]
                self.assertEqual(len(deltas), 3)
                joined = "".join(event["payload"]["text"] for event in deltas)
                self.assertEqual(joined, text)
                for event in deltas:
                    self.assertLessEqual(
                        len(event["payload"]["text"].encode("utf-8")), 8192)
                completed = [event for event in events
                             if event["type"] == "message.completed"][0]
                self.assertTrue(completed["payload"]["text_omitted"])

    def test_output_for_an_unbound_turn_is_dropped_not_misattributed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                result = route_observed_event(
                    startup, {"method": "item/agentMessage/delta", "params": {
                        "threadId": "thread_demo", "turnId": "other_turn",
                        "itemId": "msg", "delta": "leak"}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:04Z")
                self.assertEqual(result["kind"], "timeline_ignored")
                self.assertEqual(self._types(startup),
                                 ["run.created", "operation.updated",
                                  "run.started", "operation.updated"])

    def test_output_after_the_terminal_is_not_merged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo",
                    epoch=epoch, terminal="completed",
                    recorded_at="2026-10-27T00:00:04Z")
                result = route_observed_event(
                    startup, {"method": "item/commandExecution/outputDelta",
                              "params": {"threadId": "thread_demo",
                                         "turnId": "turn_demo",
                                         "itemId": "cmd",
                                         "delta": "late"}},
                    epoch=epoch, recorded_at="2026-10-27T00:00:05Z")
                self.assertEqual(result["kind"], "timeline_ignored")
                self.assertEqual(result["reason"], "run_not_active")
                self.assertNotIn("command.output", self._types(startup))

    def test_chunking_never_splits_a_character(self):
        text = "a汉🙂b" * 3
        for limit in (1, 3, 4, 5, 7, 8192):
            with self.subTest(limit=limit):
                chunks = chunk_text(text, limit)
                self.assertEqual("".join(chunks), text)
                for chunk in chunks:
                    if len(chunk) == 1:
                        # A single character is never split, even when it is
                        # wider than the requested limit.
                        continue
                    self.assertLessEqual(len(chunk.encode("utf-8")), limit)
        with self.assertRaisesRegex(ValueError, "invalid_timeline_text"):
            chunk_text("x", 0)


if __name__ == "__main__":
    unittest.main()