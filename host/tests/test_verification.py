import unittest
from pathlib import Path

from runvori.codex.verification import RequiredCommandEvidence, RequiredVerificationSet


class RequiredCommandEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tracker = RequiredCommandEvidence(
            thread_id="thread-1", turn_id="turn-1", cwd=Path.cwd(),
            matches_command=lambda value: value == "python --version")

    def emit(self, method, *, item_id="cmd-1", command="python --version",
             cwd=None, exit_code=0, status="completed", at=None,
             thread_id="thread-1"):
        item = {"type": "commandExecution", "id": item_id,
                "command": command, "cwd": str(cwd or Path.cwd()),
                "status": status, "exitCode": exit_code}
        params = {"threadId": thread_id, "turnId": "turn-1", "item": item}
        if method == "item/started":
            params["startedAtMs"] = 100 if at is None else at
        else:
            params["completedAtMs"] = 200 if at is None else at
        self.tracker.observe(method, params)

    def test_complete_matched_command_uses_actual_exit_code(self):
        self.emit("item/started")
        self.emit("item/completed")
        self.assertEqual(self.tracker.status("completed"), "passed")
        other = RequiredCommandEvidence(
            thread_id="thread-1", turn_id="turn-1", cwd=Path.cwd(),
            matches_command=lambda value: value == "python --version")
        self.tracker = other
        self.emit("item/started")
        self.emit("item/completed", exit_code=2)
        self.assertEqual(other.status("completed"), "failed")

    def test_failed_item_with_nonzero_exit_is_failed_verification(self):
        self.emit("item/started")
        self.emit("item/completed", exit_code=7, status="failed")
        self.assertEqual(self.tracker.status("completed"), "failed")

    def test_missing_execution_is_distinct_from_incomplete_execution(self):
        self.assertEqual(self.tracker.status("started"), "pending")
        self.assertEqual(self.tracker.status("completed"), "not_run")
        self.emit("item/started")
        self.assertEqual(self.tracker.status("started"), "running")
        self.assertEqual(self.tracker.status("interrupted"), "unknown")

    def test_approved_command_without_item_is_unknown(self):
        self.tracker.note_approved("cmd-1")
        self.assertEqual(self.tracker.status("completed"), "unknown")
        self.emit("item/started", item_id="other")
        self.emit("item/completed", item_id="other")
        self.assertEqual(self.tracker.status("completed"), "unknown")

    def test_wrong_workspace_or_command_cannot_satisfy_requirement(self):
        self.emit("item/started", cwd=Path.cwd().parent)
        self.emit("item/completed", cwd=Path.cwd().parent)
        self.emit("item/started", item_id="other", command="echo done")
        self.emit("item/completed", item_id="other", command="echo done")
        self.assertEqual(self.tracker.status("completed"), "not_run")

    def test_missing_exit_timestamp_or_identity_is_unknown(self):
        self.emit("item/started")
        self.emit("item/completed", exit_code=None)
        self.assertEqual(self.tracker.status("completed"), "unknown")
        for change in (dict(at=50), dict(thread_id="other")):
            with self.subTest(change=change):
                tracker = RequiredCommandEvidence(
                    thread_id="thread-1", turn_id="turn-1", cwd=Path.cwd(),
                    matches_command=lambda value: value == "python --version")
                self.tracker = tracker
                self.emit("item/started")
                self.emit("item/completed", **change)
                self.assertEqual(tracker.status("completed"), "unknown")

        tracker = RequiredCommandEvidence(
            thread_id="thread-1", turn_id="turn-1", cwd=Path.cwd(),
            matches_command=lambda value: value == "python --version")
        tracker.observe("item/started", {"threadId": "thread-1", "turnId": "turn-1",
                                        "startedAtMs": 100, "item": {
                                            "type": "commandExecution", "id": "cmd-1",
                                            "command": "python --version"}})
        self.assertEqual(tracker.status("completed"), "unknown")

    def test_two_matches_cannot_be_silently_selected(self):
        for item_id in ("cmd-1", "cmd-2"):
            self.emit("item/started", item_id=item_id)
            self.emit("item/completed", item_id=item_id)
        self.assertEqual(self.tracker.status("completed"), "unknown")

    def test_same_item_changing_command_cannot_pass(self):
        self.emit("item/started")
        self.emit("item/completed", command="echo done")
        self.assertEqual(self.tracker.status("completed"), "unknown")


class RequiredVerificationSetTests(unittest.TestCase):
    def make_set(self):
        return RequiredVerificationSet({
            command: RequiredCommandEvidence(
                thread_id="thread-1", turn_id="turn-1", cwd=Path.cwd(),
                matches_command=lambda value, expected=command: value == expected)
            for command in ("python --version", "git --version")
        })

    def emit_pair(self, group, command, item_id, exit_code):
        item = {"type": "commandExecution", "id": item_id,
                "command": command, "cwd": str(Path.cwd()),
                "status": "inProgress", "exitCode": None}
        common = {"threadId": "thread-1", "turnId": "turn-1"}
        group.observe("item/started", dict(common, item=item, startedAtMs=10))
        group.observe("item/completed", dict(
            common, item=dict(item, status="completed", exitCode=exit_code),
            completedAtMs=20))

    def test_all_required_commands_need_distinct_complete_evidence(self):
        group = self.make_set()
        self.assertEqual(group.snapshot("completed")["summary"], "not_run")
        self.emit_pair(group, "python --version", "one", 0)
        self.assertEqual(group.snapshot("completed"), {"items": {
            "python --version": "passed", "git --version": "not_run"},
            "summary": "not_run"})
        self.emit_pair(group, "git --version", "two", 0)
        self.assertEqual(group.snapshot("completed")["summary"], "passed")

    def test_failed_command_outranks_incomplete_or_nonrequired_command(self):
        group = self.make_set()
        self.emit_pair(group, "python --version", "one", 3)
        group.observe("item/started", {"threadId": "thread-1", "turnId": "turn-1",
                                       "startedAtMs": 10, "item": {
                                           "type": "commandExecution", "id": "other",
                                           "command": "echo done", "cwd": str(Path.cwd())}})
        self.assertEqual(group.snapshot("completed")["summary"], "failed")
        self.assertEqual(group.snapshot("completed")["items"]["git --version"], "not_run")

    def test_failed_command_outranks_running_requirement(self):
        group = self.make_set()
        self.emit_pair(group, "python --version", "failed", 1)
        group.observe("item/started", {"threadId": "thread-1", "turnId": "turn-1",
                                       "startedAtMs": 10, "item": {
                                           "type": "commandExecution", "id": "running",
                                           "command": "git --version", "cwd": str(Path.cwd())}})
        self.assertEqual(group.snapshot("running")["summary"], "failed")

    def test_shared_item_id_is_unknown_not_two_passes(self):
        group = self.make_set()
        self.emit_pair(group, "python --version", "same", 0)
        self.emit_pair(group, "git --version", "same", 0)
        self.assertEqual(group.snapshot("completed")["summary"], "unknown")
        self.assertEqual(set(group.snapshot("completed")["items"].values()), {"unknown"})

    def test_running_and_terminal_unknown_precedence(self):
        group = self.make_set()
        group.note_approved("python --version", "expected")
        self.assertEqual(group.snapshot("completed")["summary"], "unknown")
        group.observe("item/started", {"threadId": "thread-1", "turnId": "turn-1",
                                       "startedAtMs": 10, "item": {
                                           "type": "commandExecution", "id": "running",
                                           "command": "git --version", "cwd": str(Path.cwd())}})
        self.assertEqual(group.snapshot("running")["summary"], "running")

    def test_empty_registration_cannot_pass(self):
        self.assertEqual(RequiredVerificationSet({}).snapshot("completed"),
                         {"items": {}, "summary": "not_run"})


if __name__ == "__main__":
    unittest.main()
