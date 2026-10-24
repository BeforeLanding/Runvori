import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.stdio import RpcError, StdioClient, TransportError

PEER = Path(__file__).with_name("fake_app_server.py")


class TransportTests(unittest.TestCase):
    def client(self, mode="normal", **limits):
        client = StdioClient([sys.executable, "-u", str(PEER), mode],
                             cwd=PEER.parent, env=dict(os.environ), **limits)
        self.addCleanup(client.close)
        return client

    def test_handshake_and_request_order(self):
        client = self.client()
        with self.assertRaisesRegex(TransportError, "not_initialized"):
            client.request("thread/list", {})
        self.assertEqual(client.initialize(), {"method": "initialize"})
        self.assertEqual(client.request("thread/list", {}), {"method": "thread/list"})
        with self.assertRaisesRegex(TransportError, "already_initialized"):
            client.initialize()

    def test_notification_does_not_consume_response(self):
        client = self.client("notification")
        client.initialize()
        self.assertEqual(client.notifications.popleft()["params"]["text"], "中文")

    def test_stderr_does_not_block_or_leak(self):
        self.client("stderr").initialize()

    def test_invalid_responses_poison_connection(self):
        for mode, code in [
            ("exit", "upstream_closed"), ("invalid", "invalid_json_message"),
            ("array", "invalid_json_message"), ("wrong_id", "unexpected_response_id"),
            ("boolean_id", "unexpected_response_id"), ("both", "invalid_rpc_response"),
            ("scalar_result", "invalid_rpc_result"),
            ("server_request", "unsupported_server_request"),
        ]:
            with self.subTest(mode=mode):
                client = self.client(mode)
                with self.assertRaisesRegex(TransportError, code):
                    client.initialize()
                with self.assertRaisesRegex(TransportError, "connection_unusable"):
                    client.initialize()

    def test_timeout_never_retries(self):
        client = self.client("hang", timeout=0.3)
        with self.assertRaisesRegex(TransportError, "rpc_timeout"):
            client.initialize()
        with self.assertRaisesRegex(TransportError, "connection_unusable"):
            client.initialize()
        client.close()
        self.assertIsNotNone(client.process.poll())

    def test_bounded_line(self):
        client = self.client("oversized", max_line=1024)
        with self.assertRaisesRegex(TransportError, "message_too_large"):
            client.initialize()

    def test_peer_not_reading_stdin_is_bounded(self):
        client = self.client("no_read_after_init", timeout=0.5, max_line=2_000_000)
        client.initialize()
        with self.assertRaisesRegex(TransportError, "upstream_write_timeout"):
            client.request("synthetic/request", {"text": "x" * 1_000_000})
        client.close()
        self.assertIsNotNone(client.process.poll())

    def test_flood_fails_instead_of_silent_drop(self):
        client = self.client("flood", max_messages=2)
        with self.assertRaisesRegex(TransportError, "overflow"):
            client.initialize()

    def test_rpc_error_redacted(self):
        client = self.client("error")
        with self.assertRaises(RpcError) as error:
            client.initialize()
        self.assertNotIn("SECRET", str(error.exception))

    def test_valid_rpc_error_keeps_stream_available_without_retry(self):
        client = self.client("recoverable_rpc_error")
        client.initialize()
        with self.assertRaisesRegex(RpcError, "upstream_rpc_error"):
            client.request("turn/interrupt", {"threadId": "thread_demo",
                                              "turnId": "turn_demo"})
        event = client.next_event(1)
        self.assertEqual(event["method"], "turn/completed")
        self.assertEqual(event["params"]["turn"]["status"], "completed")
        self.assertEqual(client.request("thread/resume", {})["method"], "thread/resume")

    def test_explicit_approval_is_sent_once(self):
        client = self.client("approval")
        client.initialize()
        self.assertEqual(client.request("turn/start", {})["turn"]["id"], "turn_demo")
        event = client.next_event(1)
        self.assertEqual(event["method"], "item/commandExecution/requestApproval")
        with self.assertRaises(ValueError):
            client.respond_approval(event["id"], "acceptForSession")
        client.respond_approval(event["id"], "decline")
        with self.assertRaisesRegex(TransportError, "approval_request_not_pending"):
            client.respond_approval(event["id"], "accept")
        self.assertEqual(client.next_event(1)["params"]["decision"], "decline")
        self.assertEqual(client.next_event(1)["method"], "serverRequest/resolved")

    def test_approval_before_turn_response_is_preserved(self):
        client = self.client("approval_before_response")
        client.initialize()
        self.assertEqual(client.request("turn/start", {})["turn"]["id"], "turn_demo")
        event = client.next_event(1)
        self.assertEqual(event["method"], "item/fileChange/requestApproval")
        client.respond_approval(event["id"], "accept")
        self.assertEqual(client.next_event(1)["params"]["decision"], "accept")

    def test_resolved_request_cannot_be_approved(self):
        client = self.client("approval_resolved")
        client.initialize()
        client.request("turn/start", {})
        event = client.next_event(1)
        self.assertEqual(event["method"], "item/commandExecution/requestApproval")
        self.assertEqual(client.next_event(1)["method"], "serverRequest/resolved")
        with self.assertRaisesRegex(TransportError, "approval_request_not_pending"):
            client.respond_approval(event["id"], "accept")

    def test_lost_approval_confirmation_never_retries_with_or_without_effect(self):
        for mode, effect_expected in (("approval_ack_lost_before_effect", False),
                                      ("approval_ack_lost_after_effect", True)):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
                    received = Path(temp) / "received.txt"
                    effect = Path(temp) / "effect.txt"
                    client = StdioClient([sys.executable, "-u", str(PEER), mode,
                                          str(received), str(effect)],
                                         cwd=PEER.parent, env=dict(os.environ), timeout=2)
                    try:
                        client.initialize()
                        client.request("turn/start", {})
                        event = client.next_event(1)
                        self.assertEqual(event["method"], "item/fileChange/requestApproval")
                        client.respond_approval(event["id"], "accept")
                        with self.assertRaisesRegex(TransportError, "upstream_closed"):
                            client.next_event(2)
                        with self.assertRaisesRegex(TransportError, "connection_unusable"):
                            client.respond_approval(event["id"], "accept")
                    finally:
                        client.close()
                    self.assertEqual(received.read_text(encoding="utf-8"),
                                     "answer_received\n")
                    self.assertEqual(effect.exists(), effect_expected)
                    if effect_expected:
                        self.assertEqual(effect.read_text(encoding="utf-8"),
                                         "effect_applied\n")

    def test_close_idempotent_and_stops_reader(self):
        client = self.client()
        client.initialize()
        client.close()
        client.close()
        self.assertIsNotNone(client.process.poll())
        self.assertTrue(all(not t.is_alive() for t in client._threads))

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):
            self.client(timeout=0)
