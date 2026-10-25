"""Durable approval claim before a single upstream stdio answer."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.codex.approval_dispatch import (
    ApprovalDispatchError, send_approval_once,
)
from runvori.codex.approval_events import (
    ApprovalEventError, decode_upstream_request_key,
    record_approval_request_event,
)
from runvori.codex.event_handoff import process_next_event
from runvori.codex.stdio import StdioClient, TransportError
from runvori.infrastructure.locked_journal import LockedHostJournals


PEER = Path(__file__).with_name("fake_app_server.py")


class ApprovalDispatchTests(unittest.TestCase):
    def test_typed_key_decoder_rejects_noncanonical_values(self):
        self.assertEqual(decode_upstream_request_key("int:23"), 23)
        self.assertEqual(decode_upstream_request_key("str:23"), "23")
        for value in ("23", "int:", "int:+23", "int:023", "int:-0",
                      "str:", "str:\ud800", "int:9223372036854775808"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ApprovalEventError,
                                            "invalid_upstream_request_id"):
                    decode_upstream_request_key(value)

    @staticmethod
    def _running(startup):
        epoch = startup.recover_host_after_isolation(
            old_process_isolated=True, old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart", recorded_at="2026-10-25T00:00:00Z")["epoch"]
        startup.events.create_session("session", {})
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="run-request",
            expected_session_version=0, input_text="continue",
            recorded_at="2026-10-25T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "run-request", process_epoch=epoch,
            recorded_at="2026-10-25T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "run-request", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-25T00:00:03Z")
        return epoch

    @staticmethod
    def _decision(startup, epoch, *, upstream_id=23):
        approval = record_approval_request_event(
            startup, {"id": upstream_id,
                      "method": "item/commandExecution/requestApproval",
                      "params": {"threadId": "thread_demo", "turnId": "turn_demo"}},
            epoch=epoch, recorded_at="2026-10-25T00:00:04Z")
        startup.record_approval_decision(
            approval["approval_id"], actor_id="device",
            request_id="decision-request", expected_version=1,
            decision="allow_once", recorded_at="2026-10-25T00:00:05Z")
        return approval

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_successful_write_waits_for_separate_request_end(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                with StdioClient([sys.executable, str(PEER), "approval"],
                                 cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    client.request("turn/start", {})
                    request = client.next_event(1)
                    approval = self._decision(startup, epoch,
                                              upstream_id=request["id"])
                    result = send_approval_once(
                        client, startup, actor_id="device",
                        request_id="decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                    self.assertEqual(result["status"], "dispatching")
                    self.assertEqual(startup.approvals.get(
                        approval["approval_id"])["status"], "dispatching")
                    with self.assertRaisesRegex(ApprovalDispatchError,
                                                "dispatch_unavailable"):
                        send_approval_once(
                            client, startup, actor_id="device",
                            request_id="decision-request", epoch=epoch,
                            recorded_at="2026-10-25T00:00:07Z")
                    ignored = process_next_event(
                        client, startup, epoch=epoch,
                        recorded_at="2026-10-25T00:00:08Z", timeout=1)
                    self.assertEqual(ignored["kind"], "ignored_notification")
                    closed = process_next_event(
                        client, startup, epoch=epoch,
                        recorded_at="2026-10-25T00:00:09Z", timeout=1)
                    self.assertEqual(closed["kind"], "request_closed")
                    self.assertEqual(closed["record"]["status"], "resolved")
                    self.assertEqual(startup.get_approval_operation(
                        "device", "decision-request")["outcome"],
                        "request_closed")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_send_failure_becomes_unknown_and_is_never_retried(self):
        class FailingClient:
            def __init__(self):
                self.calls = []
                self.closed = False

            def respond_approval(self, request_id, decision):
                self.calls.append((request_id, decision))
                raise TransportError("upstream_write_timeout")

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                approval = self._decision(startup, epoch)
                client = FailingClient()
                result = send_approval_once(
                    client, startup, actor_id="device",
                    request_id="decision-request", epoch=epoch,
                    recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(client.calls, [(23, "accept")])
                self.assertTrue(client.closed)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["outcome"], "dispatch_unconfirmed")
                self.assertEqual(startup.approvals.get(
                    approval["approval_id"])["status"], "unknown")
                with self.assertRaisesRegex(ApprovalDispatchError,
                                            "dispatch_unavailable"):
                    send_approval_once(
                        client, startup, actor_id="device",
                        request_id="decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:07Z")
                self.assertEqual(client.calls, [(23, "accept")])

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_claim_failure_never_calls_upstream(self):
        class RejectClient:
            def respond_approval(self, *_args):
                raise AssertionError("upstream_send_called")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                approval = self._decision(startup, epoch)
                db = startup.events.connection
                db.execute("""CREATE TRIGGER block_claim BEFORE INSERT ON events
                            WHEN NEW.event_json LIKE '%dispatching%'
                            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
                with self.assertRaises(sqlite3.IntegrityError):
                    send_approval_once(
                        RejectClient(), startup, actor_id="device",
                        request_id="decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(startup.approvals.get(
                    approval["approval_id"])["status"], "decision_recorded")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "accepted")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_unmapped_legacy_request_cannot_be_sent(self):
        class RejectClient:
            def respond_approval(self, *_args):
                raise AssertionError("upstream_send_called")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                startup.record_approval_request(
                    "session", "run", approval_id="legacy", epoch=epoch,
                    upstream_request_id="int:23", kind="command",
                    recorded_at="2026-10-25T00:00:04Z")
                startup.record_approval_decision(
                    "legacy", actor_id="device", request_id="decision-request",
                    expected_version=1, decision="deny",
                    recorded_at="2026-10-25T00:00:05Z")
                with self.assertRaisesRegex(ApprovalDispatchError,
                                            "unmapped_approval_request"):
                    send_approval_once(
                        RejectClient(), startup, actor_id="device",
                        request_id="decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "accepted")

    @unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
    def test_lost_upstream_confirmation_keeps_send_uncertain(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            received, effect = folder / "received.txt", folder / "effect.txt"
            with LockedHostJournals(folder) as startup:
                epoch = self._running(startup)
                with StdioClient(
                        [sys.executable, str(PEER),
                         "approval_ack_lost_after_effect", str(received),
                         str(effect)], cwd=ROOT, env=dict(os.environ)) as client:
                    client.initialize()
                    client.request("turn/start", {})
                    request = client.next_event(1)
                    self._decision(startup, epoch, upstream_id=request["id"])
                    sent = send_approval_once(
                        client, startup, actor_id="device",
                        request_id="decision-request", epoch=epoch,
                        recorded_at="2026-10-25T00:00:06Z")
                    self.assertEqual(sent["status"], "dispatching")
                    with self.assertRaisesRegex(TransportError,
                                                "upstream_closed"):
                        client.next_event(2)
                self.assertEqual(received.read_text(encoding="utf-8"),
                                 "answer_received\n")
                self.assertTrue(effect.exists())
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "dispatching")
                startup.recover_host_after_isolation(
                    old_process_isolated=True, old_process_epoch=epoch,
                    expected_run_id="run", expected_run_version=3,
                    reason="upstream_exit",
                    recorded_at="2026-10-25T00:00:07Z")
                self.assertEqual(startup.get_approval_operation(
                    "device", "decision-request")["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
