import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runvori.cli import ROOT
from runvori.codex.execution_probe import (
    BINARY_BYTES, BINARY_COMMAND, DIRTY_TRACKED_BEFORE,
    FILE_CHANGE_BYTES, INTERRUPT_COMMAND,
    INTERRUPT_MARKER, SAFE_COMMAND, WRITE_COMMAND,
    NONZERO_VERIFY_COMMAND, SECOND_VERIFY_COMMAND, MOVE_DESTINATION_BYTES,
    exact_move_item, is_safe_command, move_item_matches, prepare_dirty_fixture,
    prepare_binary_fixture, prepare_fixture, prepare_move_fixture, run_probe,
    safe_accept_decision,
    shape, validate_binary_fixture, validate_dirty_fixture,
    validate_fixture, validate_move_fixture,
)
from runvori.codex.stdio import RpcError, TransportError


class FakeClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.events = iter([
            {"id": "request_1", "method": "item/commandExecution/requestApproval",
             "params": {"command": "PRIVATE_COMMAND", "secretPath": "C:/private"}},
            {"method": "turn/completed", "params": {
                "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
        ])
        self.answers = []
        self.interrupts = []
        self.write_on_accept = False
        self.move_on_accept = False
        self.binary_on_accept = False
        self.write_content = b"RUNVORI_PROBE_OK"
        self.expected_sandbox = "workspace-write"
        self.closed = False
        self.resume_calls = 0
        self.interrupt_error = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.closed = True

    def initialize(self):
        return {}

    def request(self, method, params):
        if method == "thread/start":
            assert params["sandbox"] == self.expected_sandbox
            assert params["approvalPolicy"] == "on-request"
            assert params["cwd"] == str(self.fixture)
            return {"thread": {"id": "thread_1"}}
        if method == "turn/start":
            assert params["threadId"] == "thread_1"
            return {"turn": {"id": "turn_1"}}
        if method == "turn/interrupt":
            self.interrupts.append(params)
            if self.interrupt_error:
                raise RpcError("upstream_rpc_error")
            return {}
        assert method == "thread/resume"
        self.resume_calls += 1
        return {"thread": {"id": "thread_1"}}

    def next_event(self, _timeout):
        assert not self.closed
        return next(self.events, None)

    def respond_approval(self, request_id, decision):
        self.answers.append((request_id, decision))
        if decision == "accept" and self.write_on_accept:
            (self.fixture / "probe-result.txt").write_bytes(self.write_content)
        if decision == "accept" and self.move_on_accept:
            (self.fixture / "move-source.txt").rename(
                self.fixture / "move-destination.txt")
            (self.fixture / "move-destination.txt").write_bytes(MOVE_DESTINATION_BYTES)
        if decision == "accept" and self.binary_on_accept:
            (self.fixture / "binary.bin").write_bytes(BINARY_BYTES)


class ExecutionProbeTests(unittest.TestCase):
    def test_binary_command_uses_real_exit_and_metadata_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_binary_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.binary_on_accept = True
            fake.events = iter([
                {"id": 53, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": "thread_1", "turnId": "turn_1",
                            "itemId": "binary_1", "kind": "command",
                            "command": BINARY_COMMAND, "cwd": str(fixture)}},
                {"method": "item/completed", "params": {"item": {
                    "id": "binary_1", "exitCode": 0}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="binary_command")
            self.assertEqual(fake.answers, [(53, "accept")])
            self.assertTrue(report["accepted_command_exit_zero"])
            self.assertTrue(report["binary_match"])
            self.assertTrue(report["binary_git_marker"])
            self.assertEqual(report["binary_representation"], "binary_or_non_utf8")
            self.assertFalse(report["binary_content_available"])
            self.assertEqual(report["binary_size_bytes"], len(BINARY_BYTES))
            self.assertTrue(report["git_status_expected_transition"])
            self.assertIsNone(report["file_match"])
            (fixture / "binary.bin").write_bytes(b"tampered")
            with self.assertRaisesRegex(TransportError, "invalid_binary_fixture"):
                validate_binary_fixture(ROOT, fixture)

    def test_fixture_boundary_and_occupied_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = Path(temp) / "fixture"
            prepare_fixture(ROOT, fixture)
            self.assertEqual(validate_fixture(ROOT, fixture), fixture.resolve())
            with self.assertRaisesRegex(TransportError, "fixture_not_empty"):
                prepare_fixture(ROOT, fixture)
            with self.assertRaisesRegex(TransportError, "fixture_outside_workspace_scratch"):
                prepare_fixture(ROOT, Path(temp).parent.parent / "outside")

    def test_opt_in_probe_redacts_values_and_answers_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="decline")
            self.assertEqual(fake.answers, [("request_1", "decline")])
            self.assertFalse(report["file_match"])
            self.assertEqual(report["resume"], "recognized")
            self.assertNotIn("PRIVATE_COMMAND", json.dumps(report))
            self.assertNotIn("C:/private", json.dumps(report))
            self.assertEqual(shape({"C:/sensitive": "text"}), {"unlisted_field_count": 1})

    def test_accept_probe_only_allows_exact_scoped_command(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            params = {"threadId": "thread_1", "turnId": "turn_1", "itemId": "item_1",
                      "kind": "command", "command": SAFE_COMMAND, "cwd": str(fixture)}
            fake.events = iter([
                {"id": 7, "method": "item/commandExecution/requestApproval", "params": params},
                {"method": "item/completed", "params": {"item": {
                    "id": "item_1", "exitCode": 0}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept")
            self.assertEqual(fake.answers, [(7, "accept")])
            self.assertEqual(report["approval_checks"][0]["decision"], "accept")
            self.assertEqual(report["approval_checks"][0]["reason"],
                             "allowlisted_fixture_command")
            self.assertTrue(report["approval_checks"][0]["review"]["command_exact"])
            self.assertTrue(report["accepted_command_exit_zero"])
            self.assertFalse(report["file_match"])
            for altered in (
                dict(params, command=SAFE_COMMAND + " & whoami"),
                dict(params, cwd=str(account_home)),
                dict(params, networkApprovalContext={"host": "example.com"}),
                dict(params, proposedNetworkPolicyAmendments=[]),
                dict(params, unknownPermission=True),
                dict(params, kind="writeStdin"),
            ):
                decision, _ = safe_accept_decision(
                    {"method": "item/commandExecution/requestApproval", "params": altered},
                    target=fixture, thread_id="thread_1", turn_id="turn_1")
                self.assertEqual(decision, "decline")
            decision, _ = safe_accept_decision(
                {"method": "item/fileChange/requestApproval", "params": params},
                target=fixture, thread_id="thread_1", turn_id="turn_1")
            self.assertEqual(decision, "decline")
            decision, _ = safe_accept_decision(
                {"method": "item/commandExecution/requestApproval", "params": dict(
                    params, environmentId="runtime", proposedExecpolicyAmendment=["candidate"],
                    availableDecisions=["accept", "decline"])},
                target=fixture, thread_id="thread_1", turn_id="turn_1")
            self.assertEqual(decision, "accept")

    def test_registered_verification_requires_correlated_structured_events(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            item = {"type": "commandExecution", "id": "check_1",
                    "command": SAFE_COMMAND, "cwd": str(fixture),
                    "status": "inProgress", "exitCode": None}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "item": item,
                    "startedAtMs": 100}},
                {"id": 70, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "check_1",
                    "kind": "command", "command": SAFE_COMMAND, "cwd": str(fixture)}},
                {"method": "item/completed", "params": {
                    "threadId": "thread_1", "turnId": "turn_1",
                    "item": dict(item, status="completed", exitCode=0),
                    "completedAtMs": 200}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="verify_command")
            self.assertEqual(fake.answers, [(70, "accept")])
            self.assertEqual(report["required_verification_status"], "passed")
            self.assertNotIn(str(fixture), json.dumps(report))

    def test_two_registered_commands_need_separate_approvals_and_items(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            events = []
            for request_id, item_id, command in (
                    (71, "python_1", SAFE_COMMAND),
                    (72, "git_1", SECOND_VERIFY_COMMAND)):
                item = {"type": "commandExecution", "id": item_id,
                        "command": command, "cwd": str(fixture),
                        "status": "inProgress", "exitCode": None}
                events.extend([
                    {"method": "item/started", "params": {
                        "threadId": "thread_1", "turnId": "turn_1",
                        "item": item, "startedAtMs": request_id}},
                    {"id": request_id, "method": "item/commandExecution/requestApproval",
                     "params": {"threadId": "thread_1", "turnId": "turn_1",
                                "itemId": item_id, "kind": "command",
                                "command": command, "cwd": str(fixture)}},
                    {"method": "item/completed", "params": {
                        "threadId": "thread_1", "turnId": "turn_1",
                        "item": dict(item, status="completed", exitCode=0),
                        "completedAtMs": request_id + 1}},
                ])
            events.append({"method": "turn/completed", "params": {
                "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}})
            fake.events = iter(events)
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="verify_two_commands")
            self.assertEqual(fake.answers, [(71, "accept"), (72, "accept")])
            self.assertEqual(report["required_verification_items"], {
                "python": "passed", "git": "passed"})
            self.assertEqual(report["required_verification_summary"], "passed")
            self.assertIsNone(report["accepted_command_exit_zero"])

    def test_completed_turn_does_not_hide_failed_required_command(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            item = {"type": "commandExecution", "id": "failure_1",
                    "command": NONZERO_VERIFY_COMMAND, "cwd": str(fixture),
                    "status": "inProgress", "exitCode": None}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1",
                    "item": item, "startedAtMs": 100}},
                {"id": 73, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": "thread_1", "turnId": "turn_1",
                            "itemId": "failure_1", "kind": "command",
                            "command": NONZERO_VERIFY_COMMAND, "cwd": str(fixture)}},
                {"method": "item/completed", "params": {
                    "threadId": "thread_1", "turnId": "turn_1",
                    "item": dict(item, status="failed", exitCode=7),
                    "completedAtMs": 200}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="verify_nonzero")
            self.assertEqual(fake.answers, [(73, "accept")])
            self.assertEqual(report["turn_status"], "completed")
            self.assertEqual(report["required_verification_status"], "failed")
            self.assertEqual(report["required_verification_summary"], "failed")
            self.assertTrue(report["fixed_nonzero_exit_seven"])

    def test_only_pinned_shell_wrapper_is_allowed(self):
        executable = (Path.home() / ".cache/codex-runtimes/codex-primary-runtime"
                      / "dependencies/native/powershell/pwsh.exe")
        wrapped = f'"{str(executable).replace(chr(92), chr(92) * 2)}" -Command \'{SAFE_COMMAND}\''
        with patch("runvori.codex.execution_probe.Path.is_file", return_value=True):
            self.assertTrue(is_safe_command(wrapped))
            self.assertTrue(is_safe_command(
                wrapped.replace(" -Command ", " -NoProfile -Command ")))
            write_wrapped = wrapped.replace(SAFE_COMMAND, WRITE_COMMAND).replace(
                " -Command ", " -NoProfile -Command ")
            self.assertTrue(is_safe_command(write_wrapped, WRITE_COMMAND))
            quoted = f'"{str(executable).replace(chr(92), chr(92) * 2)}"'
            binary_wrapped = (f'{quoted} -Command "'
                              f'{BINARY_COMMAND.replace(chr(34), chr(92) + chr(34))}"')
            self.assertTrue(is_safe_command(binary_wrapped, BINARY_COMMAND))
            self.assertFalse(is_safe_command(binary_wrapped + "; whoami", BINARY_COMMAND))
            self.assertFalse(is_safe_command(binary_wrapped.replace("binary.bin", "other.bin"),
                                             BINARY_COMMAND))
            self.assertFalse(is_safe_command(write_wrapped + "; Get-ChildItem", WRITE_COMMAND))
            self.assertFalse(is_safe_command(wrapped + "; whoami"))
            self.assertFalse(is_safe_command(wrapped.replace("pwsh.exe", "other.exe")))

    def test_write_scenario_requires_exact_command_and_bytes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.write_on_accept = True
            params = {"threadId": "thread_1", "turnId": "turn_1", "itemId": "write_1",
                      "kind": "command", "command": WRITE_COMMAND, "cwd": str(fixture)}
            fake.events = iter([
                {"id": 8, "method": "item/commandExecution/requestApproval", "params": params},
                {"method": "item/completed", "params": {"item": {
                    "id": "write_1", "exitCode": 0}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with self.assertRaisesRegex(ValueError, "scenario_requires_accept_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="decline", scenario="write_file")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept", scenario="write_file")
            self.assertEqual(fake.answers, [(8, "accept")])
            self.assertTrue(report["file_match"])
            self.assertTrue(report["accepted_command_exit_zero"])
            self.assertEqual(report["scenario"], "write_file")
            with self.assertRaisesRegex(TransportError, "invalid_disposable_fixture"):
                validate_fixture(ROOT, fixture)
            decision, _ = safe_accept_decision(
                {"method": "item/commandExecution/requestApproval", "params": dict(
                    params, command=WRITE_COMMAND + "; Get-ChildItem")},
                target=fixture, thread_id="thread_1", turn_id="turn_1",
                expected=WRITE_COMMAND)
            self.assertEqual(decision, "decline")

    def test_interrupt_requires_terminal_event_after_rpc_response(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            params = {"threadId": "thread_1", "turnId": "turn_1", "itemId": "sleep_1",
                      "kind": "command", "command": INTERRUPT_COMMAND, "cwd": str(fixture)}
            fake.events = iter([
                {"id": 9, "method": "item/commandExecution/requestApproval", "params": params},
                {"method": "item/commandExecution/outputDelta", "params": {
                    "itemId": "sleep_1", "delta": INTERRUPT_MARKER + "\n"}},
                {"method": "item/completed", "params": {"item": {
                    "id": "sleep_1", "exitCode": None}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "interrupted"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept", scenario="interrupt")
            self.assertEqual(fake.answers, [(9, "accept")])
            self.assertEqual(fake.interrupts, [{"threadId": "thread_1", "turnId": "turn_1"}])
            self.assertEqual(report["interrupt_rpc"], "responded")
            self.assertEqual(report["interrupt_trigger"], "output_marker")
            self.assertEqual(report["turn_status"], "interrupted")
            self.assertEqual(report["accepted_command_exit_kind"], "missing")

    def test_interrupt_completion_race_remains_completed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([
                {"id": 10, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "sleep_1",
                    "kind": "command", "command": INTERRUPT_COMMAND, "cwd": str(fixture)}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept", scenario="interrupt")
            self.assertEqual(fake.interrupts, [])
            self.assertEqual(report["interrupt_rpc"], "not_requested")
            self.assertEqual(report["turn_status"], "completed")

    def test_late_interrupt_cannot_replace_observed_completed_turn(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
                    account_home = Path(temp) / "account"
                    account_home.mkdir()
                    fake = FakeClient(fixture)
                    fake.interrupt_error = rejected
                    fake.events = iter([
                        {"id": 11, "method": "item/commandExecution/requestApproval",
                         "params": {"threadId": "thread_1", "turnId": "turn_1",
                                    "itemId": "command_1", "kind": "command",
                                    "command": SAFE_COMMAND, "cwd": str(fixture)}},
                        {"method": "item/completed", "params": {"item": {
                            "id": "command_1", "exitCode": 0}}},
                        {"method": "turn/completed", "params": {
                            "threadId": "thread_1", "turn": {"id": "turn_1",
                                                        "status": "completed"}}},
                    ])
                    with self.assertRaisesRegex(ValueError, "scenario_requires_accept_mode"):
                        run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                  executable="fake", decision="decline",
                                  scenario="interrupt_after_turn_completed")
                    with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                        report = run_probe(root=ROOT, fixture=fixture,
                                           account_home=account_home, executable="fake",
                                           decision="accept",
                                           scenario="interrupt_after_turn_completed")
                    self.assertEqual(fake.answers, [(11, "accept")])
                    self.assertEqual(len(fake.interrupts), 1)
                    self.assertEqual(report["turn_status"], "completed")
                    self.assertEqual(report["accepted_command_exit_kind"], "zero")
                    self.assertEqual(report["interrupt_trigger"], "completed_turn")
                    self.assertEqual(report["interrupt_rpc"],
                                     "rejected" if rejected else "responded")
                    self.assertEqual(report["resume"], "recognized")

    def test_late_interrupt_requires_confirmed_command_result(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([
                {"id": 12, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": "thread_1", "turnId": "turn_1",
                            "itemId": "command_1", "kind": "command",
                            "command": SAFE_COMMAND, "cwd": str(fixture)}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1",
                                                "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="interrupt_after_turn_completed")
            self.assertEqual(fake.interrupts, [])
            self.assertEqual(report["interrupt_rpc"], "not_requested")
            self.assertEqual(report["accepted_command_exit_kind"], "not_observed")
            self.assertEqual(report["turn_status"], "completed")

    def test_command_completion_interrupt_uses_observed_terminal(self):
        for terminal, rejected in (("completed", False), ("interrupted", False),
                                   ("completed", True)):
            with self.subTest(terminal=terminal, rejected=rejected):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
                    account_home = Path(temp) / "account"
                    account_home.mkdir()
                    fake = FakeClient(fixture)
                    fake.interrupt_error = rejected
                    fake.events = iter([
                        {"id": 31, "method": "item/commandExecution/requestApproval",
                         "params": {"threadId": "thread_1", "turnId": "turn_1",
                                    "itemId": "command_1", "kind": "command",
                                    "command": SAFE_COMMAND, "cwd": str(fixture)}},
                        {"method": "item/completed", "params": {"item": {
                            "id": "command_1", "exitCode": 0}}},
                        {"method": "turn/completed", "params": {
                            "threadId": "thread_1", "turn": {"id": "turn_1",
                                                        "status": terminal}}},
                    ])
                    with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                        report = run_probe(root=ROOT, fixture=fixture,
                                           account_home=account_home, executable="fake",
                                           decision="accept",
                                           scenario="interrupt_after_command_completed")
                    self.assertEqual(fake.answers, [(31, "accept")])
                    self.assertEqual(len(fake.interrupts), 1)
                    self.assertEqual(report["interrupt_trigger"], "command_exit_zero")
                    self.assertEqual(report["interrupt_rpc"],
                                     "rejected" if rejected else "responded")
                    self.assertEqual(report["turn_status"], terminal)
                    self.assertEqual(report["resume"], "recognized")

    def test_delayed_command_completion_cut_records_delay_and_terminal(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.interrupt_error = True
            fake.events = iter([
                {"id": 32, "method": "item/commandExecution/requestApproval",
                 "params": {"threadId": "thread_1", "turnId": "turn_1",
                            "itemId": "command_1", "kind": "command",
                            "command": SAFE_COMMAND, "cwd": str(fixture)}},
                {"method": "item/completed", "params": {"item": {
                    "id": "command_1", "exitCode": 0}}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1",
                                                "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake), \
                    patch("runvori.codex.execution_probe.COMMAND_COMPLETION_RACE_DELAY_SECONDS",
                          0.001):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="interrupt_after_command_completed_delayed")
            self.assertEqual(len(fake.interrupts), 1)
            self.assertEqual(report["interrupt_delay_ms"], 1)
            self.assertEqual(report["interrupt_rpc"], "rejected")
            self.assertEqual(report["turn_status"], "completed")
            self.assertEqual(report["resume"], "recognized")

    def test_interrupt_immediately_after_approval_uses_terminal_status(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([
                {"id": 15, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "sleep_1",
                    "kind": "command", "command": INTERRUPT_COMMAND, "cwd": str(fixture)}},
                {"method": "serverRequest/resolved", "params": {
                    "threadId": "thread_1", "requestId": 15}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "interrupted"}}},
            ])
            with self.assertRaisesRegex(ValueError, "scenario_requires_accept_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="decline",
                          scenario="interrupt_on_approval")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="interrupt_on_approval")
            self.assertEqual(fake.answers, [(15, "accept")])
            self.assertEqual(fake.interrupts, [{"threadId": "thread_1", "turnId": "turn_1"}])
            self.assertEqual(report["interrupt_trigger"], "approval_answered")
            self.assertEqual(report["interrupt_rpc"], "responded")
            self.assertEqual(report["turn_status"], "interrupted")
            self.assertEqual(report["accepted_command_exit_kind"], "not_observed")

    def test_interrupt_on_approval_never_accepts_second_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            params = {"threadId": "thread_1", "turnId": "turn_1", "itemId": "sleep_1",
                      "kind": "command", "command": INTERRUPT_COMMAND, "cwd": str(fixture)}
            fake.events = iter([
                {"id": 16, "method": "item/commandExecution/requestApproval", "params": params},
                {"id": 17, "method": "item/commandExecution/requestApproval",
                 "params": dict(params, itemId="sleep_2")},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                with self.assertRaisesRegex(TransportError,
                                            "unexpected_approval_after_interrupt"):
                    run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                              executable="fake", decision="accept",
                              scenario="interrupt_on_approval")
            self.assertEqual(fake.answers, [(16, "accept")])
            self.assertEqual(fake.interrupts, [{"threadId": "thread_1", "turnId": "turn_1"}])

    def test_read_only_file_change_probe_declines_without_writing(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.expected_sandbox = "read-only"
            fake.events = iter([
                {"id": 18, "method": "item/fileChange/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "patch_1",
                    "startedAtMs": 1, "grantRoot": None}},
                {"method": "serverRequest/resolved", "params": {
                    "threadId": "thread_1", "requestId": 18}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with self.assertRaisesRegex(ValueError,
                                        "file_change_scenario_requires_decline_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="accept",
                          scenario="file_change_decline")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="decline",
                                   scenario="file_change_decline")
            self.assertEqual(fake.answers, [(18, "decline")])
            self.assertEqual(report["file_change_approval_count"], 1)
            self.assertEqual(report["command_approval_count"], 0)
            self.assertTrue(report["approval_checks"][0]["review"]["thread_exact"])
            self.assertEqual(report["approval_checks"][0]["review"]["grant_root_kind"],
                             "NoneType")
            self.assertFalse(report["file_match"])

    def test_move_probe_declines_and_preserves_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_move_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.expected_sandbox = "read-only"
            item = {"type": "fileChange", "id": "move_1", "status": "inProgress",
                    "changes": [{"path": str(fixture / "move-source.txt"),
                                 "kind": {"type": "update", "move_path": str(
                                     fixture / "move-destination.txt")},
                                 "diff": "redacted test diff"}]}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "item": item}},
                {"id": 51, "method": "item/fileChange/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "move_1",
                    "startedAtMs": 1, "grantRoot": None}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with self.assertRaisesRegex(ValueError, "file_change_scenario_requires_decline_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="accept",
                          scenario="file_change_move_decline")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="decline",
                                   scenario="file_change_move_decline")
            self.assertEqual(fake.answers, [(51, "decline")])
            self.assertTrue(report["move_item_observed"])
            self.assertTrue(report["move_kind_paths_matched"])
            self.assertTrue(report["move_source_unchanged"])
            self.assertTrue(report["move_destination_absent"])
            self.assertNotIn(str(fixture), json.dumps(report))
            altered = dict(item, changes=[dict(item["changes"][0],
                                               kind={"type": "update", "move_path": str(
                                                   account_home / "outside.txt")})])
            self.assertFalse(move_item_matches(altered, fixture))
            (fixture / "move-source.txt").write_bytes(b"tampered")
            with self.assertRaisesRegex(TransportError, "invalid_move_fixture"):
                validate_move_fixture(ROOT, fixture)

    def test_move_accept_requires_exact_diff_and_checks_result(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_move_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.expected_sandbox = "read-only"
            fake.move_on_accept = True
            destination = fixture / "move-destination.txt"
            diff = ("@@ -1 +1 @@\n-RUNVORI_MOVE_OLD\n+RUNVORI_MOVE_NEW\n\n\n"
                    f"Moved to: {destination}")
            change = {"path": str(fixture / "move-source.txt"),
                      "kind": {"type": "update", "move_path": str(destination)},
                      "diff": diff}
            item = {"type": "fileChange", "id": "move_1", "status": "inProgress",
                    "changes": [change]}
            self.assertTrue(exact_move_item(item, fixture))
            for bad_change in (dict(change, diff=diff + "unexpected"),
                               dict(change, kind={"type": "update", "move_path": str(
                                   account_home / "outside.txt")}),
                               dict(change, extra="unexpected")):
                self.assertFalse(exact_move_item(dict(item, changes=[bad_change]), fixture))
            request = {"threadId": "thread_1", "turnId": "turn_1", "itemId": "move_1",
                       "startedAtMs": 1, "grantRoot": None}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "item": item}},
                {"id": 52, "method": "item/fileChange/requestApproval", "params": request},
                {"method": "item/completed", "params": {
                    "threadId": "thread_1", "turnId": "turn_1",
                    "item": dict(item, status="completed")}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="file_change_move_accept")
            self.assertEqual(fake.answers, [(52, "accept")])
            self.assertTrue(report["file_change_item_allowlisted"])
            self.assertEqual(report["accepted_file_change_status"], "completed")
            self.assertTrue(report["move_item_observed"])
            self.assertTrue(report["move_kind_paths_matched"])
            self.assertTrue(report["move_source_absent"])
            self.assertTrue(report["move_destination_match"])
            self.assertTrue(report["git_status_expected_transition"])

    def test_file_change_accept_requires_matching_item_and_exact_bytes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.expected_sandbox = "read-only"
            fake.write_on_accept = True
            fake.write_content = FILE_CHANGE_BYTES
            item = {"type": "fileChange", "id": "patch_1", "status": "inProgress",
                    "changes": [{"path": str(fixture / "probe-result.txt"),
                                 "kind": {"type": "add"},
                                 "diff": FILE_CHANGE_BYTES.decode("ascii")}]}
            request = {"threadId": "thread_1", "turnId": "turn_1",
                       "itemId": "patch_1", "startedAtMs": 1, "grantRoot": None}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "item": item}},
                {"id": 19, "method": "item/fileChange/requestApproval", "params": request},
                {"method": "item/completed", "params": {"item": dict(item, status="completed")}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with self.assertRaisesRegex(ValueError,
                                        "file_change_scenario_requires_accept_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="decline", scenario="file_change_accept")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="file_change_accept")
            self.assertEqual(fake.answers, [(19, "accept")])
            self.assertTrue(report["file_change_item_allowlisted"])
            self.assertEqual(report["accepted_file_change_status"], "completed")
            self.assertTrue(report["file_match"])
            self.assertTrue(report["fixture_only_expected_file"])
            self.assertTrue(report["fixture_entries_within_allowlist"])

    def test_dirty_fixture_preserves_preexisting_changes_and_labels_git_view(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_dirty_fixture(ROOT, Path(temp) / "fixture")
            self.assertEqual(validate_dirty_fixture(ROOT, fixture), fixture)
            (fixture / "tracked.txt").write_bytes(b"unexpected edit\n")
            with self.assertRaisesRegex(TransportError, "invalid_dirty_fixture"):
                validate_dirty_fixture(ROOT, fixture)
            (fixture / "tracked.txt").write_bytes(DIRTY_TRACKED_BEFORE)
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.expected_sandbox = "read-only"
            fake.write_on_accept = True
            fake.write_content = FILE_CHANGE_BYTES
            item = {"type": "fileChange", "id": "patch_1", "status": "inProgress",
                    "changes": [{"path": str(fixture / "probe-result.txt"),
                                 "kind": {"type": "add"},
                                 "diff": FILE_CHANGE_BYTES.decode("ascii")}]}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "item": item}},
                {"id": 40, "method": "item/fileChange/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "patch_1",
                    "startedAtMs": 1, "grantRoot": None}},
                {"method": "item/completed", "params": {"item": dict(item, status="completed")}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="file_change_accept_dirty")
            self.assertEqual(fake.answers, [(40, "accept")])
            self.assertTrue(report["file_match"])
            self.assertTrue(report["preexisting_files_unchanged"])
            self.assertTrue(report["git_status_expected_transition"])
            self.assertTrue(report["git_diff_excludes_untracked"])
            self.assertTrue(report["fixture_only_expected_file"])

    def test_file_change_cut_after_accept_preserves_unknown_terminal_state(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.expected_sandbox = "read-only"
            fake.write_on_accept = True
            fake.write_content = FILE_CHANGE_BYTES
            item = {"type": "fileChange", "id": "patch_1", "status": "inProgress",
                    "changes": [{"path": str(fixture / "probe-result.txt"),
                                 "kind": {"type": "add"},
                                 "diff": FILE_CHANGE_BYTES.decode("ascii")}]}
            fake.events = iter([
                {"method": "item/started", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "item": item}},
                {"id": 21, "method": "item/fileChange/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "patch_1",
                    "startedAtMs": 1, "grantRoot": None}},
                {"method": "item/completed", "params": {"item": dict(item, status="completed")}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
            ])
            with self.assertRaisesRegex(ValueError, "file_change_scenario_requires_accept_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="decline",
                          scenario="file_change_cut_after_accept")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="accept",
                                   scenario="file_change_cut_after_accept")
            self.assertEqual(fake.answers, [(21, "accept")])
            self.assertTrue(fake.closed)
            self.assertEqual(fake.resume_calls, 0)
            self.assertEqual(report["cutoff"], "after_approval_write")
            self.assertTrue(report["managed_process_close_returned"])
            self.assertEqual(report["turn_status"], "unknown")
            self.assertEqual(report["accepted_file_change_status"], "not_observed")
            self.assertEqual(report["resume"], "not_attempted")
            self.assertTrue(report["file_match"])
            self.assertTrue(report["fixture_entries_within_allowlist"])

    def test_file_change_accept_declines_tampered_item_or_request(self):
        base_change = {"kind": {"type": "add"},
                       "diff": FILE_CHANGE_BYTES.decode("ascii")}
        for alteration in ("wrong_path", "wrong_diff", "extra_change", "wrong_kind",
                           "wrong_item_id", "grant_root", "extra_request_field"):
            with self.subTest(alteration=alteration):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
                    account_home = Path(temp) / "account"
                    account_home.mkdir()
                    fake = FakeClient(fixture)
                    fake.expected_sandbox = "read-only"
                    fake.write_on_accept = True
                    change = dict(base_change, path=str(fixture / "probe-result.txt"))
                    item = {"type": "fileChange", "id": "patch_1", "status": "inProgress",
                            "changes": [change]}
                    request = {"threadId": "thread_1", "turnId": "turn_1",
                               "itemId": "patch_1", "startedAtMs": 1, "grantRoot": None}
                    if alteration == "wrong_path":
                        change["path"] = str(fixture / "other.txt")
                    elif alteration == "wrong_diff":
                        change["diff"] += "EXTRA\n"
                    elif alteration == "extra_change":
                        item["changes"].append(dict(change))
                    elif alteration == "wrong_kind":
                        change["kind"] = {"type": "update"}
                    elif alteration == "wrong_item_id":
                        request["itemId"] = "other_item"
                    elif alteration == "grant_root":
                        request["grantRoot"] = str(fixture)
                    else:
                        request["unknownScope"] = True
                    fake.events = iter([
                        {"method": "item/started", "params": {
                            "threadId": "thread_1", "turnId": "turn_1", "item": item}},
                        {"id": 20, "method": "item/fileChange/requestApproval", "params": request},
                        {"method": "turn/completed", "params": {
                            "threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}},
                    ])
                    with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                        report = run_probe(root=ROOT, fixture=fixture,
                                           account_home=account_home, executable="fake",
                                           decision="accept", scenario="file_change_accept")
                    self.assertEqual(fake.answers, [(20, "decline")])
                    self.assertFalse(report["file_match"])

    def test_interrupt_pending_approval_matches_resolution_without_answer(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([
                {"id": 11, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "pending_1",
                    "kind": "command", "command": SAFE_COMMAND, "cwd": str(fixture)}},
                {"method": "serverRequest/resolved", "params": {
                    "threadId": "thread_1", "requestId": 11}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "interrupted"}}},
            ])
            with self.assertRaisesRegex(ValueError, "pending_scenario_requires_defer_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="accept", scenario="interrupt_pending")
            with self.assertRaisesRegex(ValueError, "pending_scenario_requires_defer_mode"):
                run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                          executable="fake", decision="defer", scenario="read_only")
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="defer",
                                   scenario="interrupt_pending")
            self.assertEqual(fake.answers, [])
            self.assertEqual(fake.interrupts, [{"threadId": "thread_1", "turnId": "turn_1"}])
            self.assertEqual(report["approval_checks"][0]["decision"], "defer")
            self.assertTrue(report["pending_request_allowlisted"])
            self.assertTrue(report["pending_request_resolved"])
            self.assertEqual(report["turn_status"], "interrupted")

    def test_interrupt_pending_without_resolution_stays_unknown(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([
                {"id": 12, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "pending_1",
                    "kind": "command", "command": SAFE_COMMAND, "cwd": str(fixture)}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "interrupted"}}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake), \
                    patch("runvori.codex.execution_probe.PENDING_RESOLUTION_GRACE_SECONDS", 0.01):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="defer",
                                   scenario="interrupt_pending")
            self.assertFalse(report["pending_request_resolved"])
            self.assertEqual(fake.answers, [])

    def test_interrupt_pending_rejects_unexpected_request_before_interrupt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([{"id": 14,
                                 "method": "item/commandExecution/requestApproval",
                                 "params": {"threadId": "thread_1", "turnId": "turn_1",
                                            "itemId": "unexpected_1", "kind": "command",
                                            "command": "whoami", "cwd": str(fixture)}}])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake):
                with self.assertRaisesRegex(TransportError, "pending_approval_not_allowlisted"):
                    run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                              executable="fake", decision="defer",
                              scenario="interrupt_pending")
            self.assertEqual(fake.answers, [])
            self.assertEqual(fake.interrupts, [])

    def test_pending_resolution_after_terminal_is_still_recorded(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            fixture = prepare_fixture(ROOT, Path(temp) / "fixture")
            account_home = Path(temp) / "account"
            account_home.mkdir()
            fake = FakeClient(fixture)
            fake.events = iter([
                {"id": 13, "method": "item/commandExecution/requestApproval", "params": {
                    "threadId": "thread_1", "turnId": "turn_1", "itemId": "pending_1",
                    "kind": "command", "command": SAFE_COMMAND, "cwd": str(fixture)}},
                {"method": "turn/completed", "params": {
                    "threadId": "thread_1", "turn": {"id": "turn_1", "status": "interrupted"}}},
                {"method": "serverRequest/resolved", "params": {
                    "threadId": "thread_1", "requestId": 13}},
            ])
            with patch("runvori.codex.execution_probe.StdioClient", return_value=fake), \
                    patch("runvori.codex.execution_probe.PENDING_RESOLUTION_GRACE_SECONDS", 0.01):
                report = run_probe(root=ROOT, fixture=fixture, account_home=account_home,
                                   executable="fake", decision="defer",
                                   scenario="interrupt_pending")
            self.assertTrue(report["pending_request_resolved"])
            self.assertEqual(fake.answers, [])


if __name__ == "__main__":
    unittest.main()
