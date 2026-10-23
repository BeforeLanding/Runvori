import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runvori.cli import ROOT
from runvori.codex.execution_probe import (
    SAFE_COMMAND, is_safe_command, prepare_fixture, run_probe, safe_accept_decision,
    shape, validate_fixture,
)
from runvori.codex.stdio import TransportError


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

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def initialize(self):
        return {}

    def request(self, method, params):
        if method == "thread/start":
            assert params["sandbox"] == "workspace-write"
            assert params["approvalPolicy"] == "on-request"
            assert params["cwd"] == str(self.fixture)
            return {"thread": {"id": "thread_1"}}
        if method == "turn/start":
            assert params["threadId"] == "thread_1"
            return {"turn": {"id": "turn_1"}}
        assert method == "thread/resume"
        return {"thread": {"id": "thread_1"}}

    def next_event(self, _timeout):
        return next(self.events)

    def respond_approval(self, request_id, decision):
        self.answers.append((request_id, decision))
        (self.fixture / "probe-result.txt").write_bytes(b"RUNVORI_PROBE_OK\n")


class ExecutionProbeTests(unittest.TestCase):
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
            self.assertTrue(report["file_match"])
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
                             "allowlisted_read_only_command")
            self.assertTrue(report["approval_checks"][0]["review"]["command_exact"])
            self.assertTrue(report["accepted_command_exit_zero"])
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

    def test_only_pinned_shell_wrapper_is_allowed(self):
        executable = (Path.home() / ".cache/codex-runtimes/codex-primary-runtime"
                      / "dependencies/native/powershell/pwsh.exe")
        wrapped = f'"{str(executable).replace(chr(92), chr(92) * 2)}" -Command \'{SAFE_COMMAND}\''
        with patch("runvori.codex.execution_probe.Path.is_file", return_value=True):
            self.assertTrue(is_safe_command(wrapped))
            self.assertFalse(is_safe_command(wrapped + "; whoami"))
            self.assertFalse(is_safe_command(wrapped.replace("pwsh.exe", "other.exe")))


if __name__ == "__main__":
    unittest.main()
