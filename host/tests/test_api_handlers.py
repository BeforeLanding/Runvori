"""HTTPS layer rules: authentication, protocol gate, rate limit, routing."""

import base64
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from runvori.api.auth import RateLimiter, error_body
from runvori.api.handlers import ApiError, ApiHandlers, parse_bearer
from runvori.application.local_admin_server import (
    LocalAdminClient, LocalAdminError, LocalAdminServer, load_or_create_admin_key,
    pipe_address,
)
from runvori.cli import ROOT
from runvori.infrastructure.pairing_journal import PairingJournal


HOST_ID = "host_" + "1" * 32


class BearerTests(unittest.TestCase):
    def test_only_32_byte_base64_tokens_are_accepted(self):
        token = b"\x01" * 32
        encoded = "Bearer " + base64.b64encode(token).decode()
        self.assertEqual(parse_bearer(encoded), token)
        for value in (None, "", "Bearer", "Basic abc", "Bearer !!!!",
                      "Bearer " + base64.b64encode(b"short").decode()):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ApiError, "unauthorized"):
                    parse_bearer(value)

    def test_error_body_shape_and_retryable_flags(self):
        body = error_body("resource_changed")
        self.assertEqual(set(body["error"]), {"code", "message", "retryable"})
        self.assertFalse(body["error"]["retryable"])
        self.assertTrue(error_body("execution_unavailable")["error"]["retryable"])
        self.assertEqual(error_body("approval_details_insufficient")["error"]["code"],
                         "approval_details_insufficient")


class RateLimiterTests(unittest.TestCase):
    def test_per_source_and_global_limits_are_independent(self):
        now = [0.0]
        limiter = RateLimiter(per_source=2, global_limit=3, window_seconds=10,
                              clock=lambda: now[0])
        self.assertTrue(limiter.allow("a")[0])
        self.assertTrue(limiter.allow("a")[0])
        allowed, retry = limiter.allow("a")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        self.assertTrue(limiter.allow("b")[0])
        self.assertFalse(limiter.allow("c")[0])  # global window exhausted
        now[0] = 11.0
        self.assertTrue(limiter.allow("c")[0])


class FakeDeviceAccess:
    host_id = HOST_ID

    def __init__(self, token=b"\x02" * 32, device_id="dev1"):
        self.token = token
        self.device_id = device_id
        self.revoked = False
        self.read_calls = []
        self.reads = SimpleNamespace(
            snapshot=self._snapshot, events=self._events,
            sessions=self._sessions, run=self._run,
            run_results=self._run_results, run_commands=self._run_commands,
            run_artifacts=self._run_artifacts,
            artifact_content=self._artifact_content,
            cancel_all=lambda: None)

    def authenticate(self, token):
        if self.revoked or token != self.token:
            return None
        return self.device_id

    def exchange(self, secret):
        raise AssertionError("not used")

    @staticmethod
    def _snapshot(**_kwargs):
        return {"session_id": "s", "snapshot_seq": 1, "session_version": 0}

    @staticmethod
    def _events(**_kwargs):
        return {"events": [], "latest_seq": 0}

    @staticmethod
    def _sessions(**_kwargs):
        return {"items": [], "next_cursor": None}

    def _run(self, **kwargs):
        self.read_calls.append(("run", kwargs))
        return {"run_id": kwargs["run_id"], "status": "completed",
                "verification": {"configured": False, "summary": "not_run"}}

    def _run_results(self, **kwargs):
        self.read_calls.append(("results", kwargs))
        return {"run_id": kwargs["run_id"], "status": "completed",
                "verification": {"configured": False, "summary": "not_run",
                                 "items": []}, "command_count": 0}

    def _run_commands(self, **kwargs):
        self.read_calls.append(("commands", kwargs))
        return {"items": [], "next_cursor": None, "has_more": False}

    def _run_artifacts(self, **kwargs):
        self.read_calls.append(("artifacts", kwargs))
        return {"items": [], "next_cursor": None, "has_more": False}

    def _artifact_content(self, **kwargs):
        self.read_calls.append(("artifact_content", kwargs))
        return {"artifact_id": kwargs["artifact_id"], "data": b"abcdef",
                "size_bytes": 6, "offset": kwargs["offset"],
                "next_offset": 6, "sha256": "0" * 64,
                "representation": "text", "truncated": False,
                "dropped_bytes": 0}


class FakeMailbox:
    def __init__(self):
        self.calls = []

    def get_host_view(self):
        self.calls.append("host")
        return {"host_id": HOST_ID, "epoch": 1, "active_run_id": None}

    def get_approval_view(self, *, approval_id):
        self.calls.append(("approval", approval_id))
        return {"approval_id": approval_id, "version": 1, "can_allow": False}

    def list_run_approvals(self, *, run_id, status=None):
        self.calls.append(("run_approvals", run_id, status))
        return {"items": [], "next_cursor": None}

    def start_run(self, **values):
        self.calls.append(("start", values))
        return {"request_id": values["request_id"], "status": "accepted",
                "resource_id": "run"}

    def interrupt_run(self, **values):
        self.calls.append(("interrupt", values))
        return {"request_id": values["request_id"], "status": "terminal",
                "outcome": "completed"}

    def decide_approval(self, **values):
        self.calls.append(("decide", values))
        return {"request_id": values["request_id"], "status": "accepted"}

    def get_operation(self, **values):
        self.calls.append(("operation", values))
        return {"request_id": values["request_id"], "status": "accepted"}


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeDeviceAccess()
        self.mailbox = FakeMailbox()
        self.handlers = ApiHandlers(
            host_name="Test Host", device_access=self.device,
            mailbox=self.mailbox, reads=self.device.reads,
            limiter=RateLimiter(), now=lambda: "2026-10-27T00:00:00Z")

    def _auth(self):
        return self.handlers.authenticate(
            "Bearer " + base64.b64encode(self.device.token).decode())

    def test_actor_comes_from_token_not_from_the_body(self):
        device_id, token = self._auth()
        status, body, _ = self.handlers.dispatch(
            device_id, token, "POST", "/v1/sessions/s/runs", "",
            {"request_id": "r1", "expected_session_version": 0,
             "input": "go", "actor_id": "attacker", "device_id": "other"})
        self.assertEqual(status, 202)
        call = self.mailbox.calls[-1]
        self.assertEqual(call[1]["actor_id"], "dev1")
        self.assertEqual(body["status"], "accepted")

    def test_protocol_mismatch_is_rejected_before_business_logic(self):
        with self.assertRaisesRegex(ApiError, "protocol_incompatible"):
            self.handlers.require_protocol("2")
        with self.assertRaisesRegex(ApiError, "protocol_incompatible"):
            self.handlers.require_protocol(None)
        self.handlers.require_protocol("1")

    def test_revoked_device_cannot_authenticate(self):
        self.device.revoked = True
        with self.assertRaisesRegex(ApiError, "unauthorized"):
            self._auth()

    def test_decision_validation_rejects_unknown_decisions(self):
        _, token = self._auth()
        with self.assertRaisesRegex(ApiError, "invalid_request"):
            self.handlers.dispatch(
                "dev1", token, "POST", "/v1/approvals/a/decision", "",
                {"request_id": "d1", "expected_version": 1,
                 "decision": "allow_forever"})
        status, _, _ = self.handlers.dispatch(
            "dev1", token, "POST", "/v1/approvals/a/decision", "",
            {"request_id": "d1", "expected_version": 1, "decision": "deny"})
        self.assertEqual(status, 202)

    def test_oversized_input_is_rejected_before_the_mailbox(self):
        _, token = self._auth()
        with self.assertRaisesRegex(ApiError, "payload_too_large"):
            self.handlers.dispatch(
                "dev1", token, "POST", "/v1/sessions/s/runs", "",
                {"request_id": "r1", "expected_session_version": 0,
                 "input": "x" * (16 * 1024 + 1)})
        self.assertEqual(self.mailbox.calls, [])

    def test_terminal_interrupt_returns_the_terminal_state(self):
        _, token = self._auth()
        status, body, _ = self.handlers.dispatch(
            "dev1", token, "POST", "/v1/runs/run/interrupt", "",
            {"request_id": "i1", "expected_version": 1})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "terminal")

    def test_unknown_path_is_not_a_business_error(self):
        _, token = self._auth()
        with self.assertRaises(ApiError) as caught:
            self.handlers.dispatch("dev1", token, "GET", "/v1/unknown", "", {})
        self.assertEqual(caught.exception.status, 404)

    def test_artifact_list_and_content_are_authenticated_reads(self):
        _, token = self._auth()
        status, body, _ = self.handlers.dispatch(
            "dev1", token, "GET", "/v1/runs/run/artifacts",
            "limit=5&cursor=art_" + "1" * 32, {})
        self.assertEqual(200, status)
        self.assertEqual([], body["items"])
        call = [item for item in self.device.read_calls
                if item[0] == "artifacts"][-1]
        self.assertEqual("art_" + "1" * 32,
                         call[1]["after_artifact_id"])

        artifact_id = "art_" + "2" * 32
        status, data, headers = self.handlers.dispatch(
            "dev1", token, "GET", f"/v1/artifacts/{artifact_id}/content",
            "offset=0&limit=6", {})
        self.assertEqual(200, status)
        self.assertEqual(b"abcdef", data)
        self.assertEqual("6", headers["X-Artifact-Size"])
        self.assertEqual("6", headers["X-Artifact-Next-Offset"])

        with self.assertRaises(ApiError) as caught:
            self.handlers.dispatch(
                "dev1", token, "GET",
                f"/v1/artifacts/{artifact_id}/content", "offset=abc", {})
        self.assertEqual(416, caught.exception.status)

    def test_session_list_uses_an_opaque_cursor_and_a_limit(self):
        _, token = self._auth()
        status, body, _ = self.handlers.dispatch(
            "dev1", token, "GET", "/v1/sessions", "limit=5&cursor=sess_9", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["items"], [])
        self.assertIsNone(body["next_cursor"])
        self.assertIn("server_time", body)
        for query in ("limit=0", "limit=101", "limit=x", "cursor=",
                      "limit=1&limit=2"):
            with self.subTest(query=query):
                with self.assertRaisesRegex(ApiError, "invalid_cursor"):
                    self.handlers.dispatch(
                        "dev1", token, "GET", "/v1/sessions", query, {})
        with self.assertRaisesRegex(ApiError, "invalid_request"):
            self.handlers.dispatch("dev1", token, "POST", "/v1/sessions", "", {})

    def test_run_detail_results_and_commands_are_authenticated_reads(self):
        _, token = self._auth()
        status, body, _ = self.handlers.dispatch(
            "dev1", token, "GET", "/v1/runs/run", "", {})
        self.assertEqual(200, status)
        self.assertEqual("run", body["run_id"])
        status, body, _ = self.handlers.dispatch(
            "dev1", token, "GET", "/v1/runs/run/results", "", {})
        self.assertEqual(200, status)
        self.assertEqual([], body["verification"]["items"])
        status, _, _ = self.handlers.dispatch(
            "dev1", token, "GET", "/v1/runs/run/commands",
            "limit=2&cursor=cmd1", {})
        self.assertEqual(200, status)
        self.assertEqual(("commands", {
            "token": self.device.token, "run_id": "run",
            "after_item_id": "cmd1", "limit": 2}), self.device.read_calls[-1])
        with self.assertRaisesRegex(ApiError, "invalid_cursor"):
            self.handlers.dispatch(
                "dev1", token, "GET", "/v1/runs/run/commands", "limit=0", {})
        with self.assertRaisesRegex(ApiError, "invalid_cursor"):
            self.handlers.dispatch(
                "dev1", token, "GET", "/v1/runs/run/commands", "cursor=", {})
        with self.assertRaisesRegex(ApiError, "invalid_request"):
            self.handlers.dispatch(
                "dev1", token, "POST", "/v1/runs/run/results", "", {})

    def test_run_approvals_filter_rejects_unknown_status(self):
        _, token = self._auth()
        status, body, _ = self.handlers.dispatch(
            "dev1", token, "GET", "/v1/runs/run/approvals", "status=pending", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.mailbox.calls[-1],
                         ("run_approvals", "run", "pending"))
        with self.assertRaisesRegex(ApiError, "invalid_request"):
            self.handlers.dispatch(
                "dev1", token, "GET", "/v1/runs/run/approvals",
                "status=all", {})


@unittest.skipUnless(os.name == "nt", "Windows named pipe")
class LocalAdminPipeTests(unittest.TestCase):
    def test_round_trip_and_unknown_command(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            seen = []

            def handler(request):
                seen.append(request)
                return {"status": "running", "echo": request["command"]}

            server = LocalAdminServer(data_dir, handler)
            try:
                self.assertEqual(
                    LocalAdminClient(data_dir).call("status"),
                    {"status": "running", "echo": "status"})
                self.assertEqual(seen, [{"command": "status", "arguments": {}}])
            finally:
                server.close()

    def test_wrong_key_cannot_connect(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            server = LocalAdminServer(data_dir, lambda _request: {"ok": True})
            try:
                other = data_dir / "other"
                other.mkdir()
                with self.assertRaises(LocalAdminError):
                    LocalAdminClient(other).call("status")
            finally:
                server.close()

    def test_admin_key_is_stable_and_pipe_name_hides_the_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            first = load_or_create_admin_key(data_dir)
            self.assertEqual(load_or_create_admin_key(data_dir), first)
            self.assertEqual(len(first), 32)
            address = pipe_address(data_dir)
            self.assertTrue(address.startswith(r"\\.\pipe\runvori-host-"))
            self.assertNotIn(str(data_dir), address)

    def test_no_host_running_reports_not_running(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with self.assertRaisesRegex(LocalAdminError, "host_not_running"):
                LocalAdminClient(Path(temp)).call("status")


class PairingIdentityTests(unittest.TestCase):
    def test_authenticate_returns_device_identity_or_none(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with PairingJournal(Path(temp) / "pairing.db") as journal:
                secret, _ = journal.issue_local_secret(host_id=HOST_ID)
                device_id, token = journal.exchange(secret, host_id=HOST_ID)
                self.assertEqual(journal.authenticate(token, host_id=HOST_ID),
                                 device_id)
                self.assertIsNone(journal.authenticate(token, host_id="host_" + "2" * 32))
                self.assertIsNone(journal.authenticate(b"\x00" * 32, host_id=HOST_ID))
                journal.revoke_local_device()
                self.assertIsNone(journal.authenticate(token, host_id=HOST_ID))


if __name__ == "__main__":
    unittest.main()