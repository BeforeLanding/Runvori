import os
import sys
import unittest
from pathlib import Path

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
