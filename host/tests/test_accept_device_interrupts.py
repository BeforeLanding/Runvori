"""Offline checks for the two real interrupt acceptance specials.

The device scenarios themselves need an emulator and a real Codex turn. These
tests cover the deterministic parts: trigger timeout, terminal competition,
approval invalidation/refusal, the Host-side evidence reader and the model-turn
budget. Nothing here starts Codex or the emulator.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.locked_journal import LockedHostJournals

from approval_fixtures import reviewable_command, seed_workspace_session

sys.path.insert(0, str(ROOT / "scripts"))
import accept_device  # noqa: E402


MARKER = "RUNVORI_SLEEP_STARTED"


def execution_evidence(**overrides) -> dict:
    evidence = {
        "session": "session",
        "session_runs": 1,
        "run_id": "run",
        "run_status": "interrupted",
        "active_slot_run_id": None,
        "interrupt_status": "confirmed",
        "interrupt_outcome": "run_interrupted",
        "approvals": 0,
        "pending_approvals": 0,
        "approval_status": None,
        "approval_resolution_kind": None,
        "approval_turn_terminal_observed": False,
        "approval_late_closure_observed": False,
        "approval_operations": 0,
        "start_signal_observed": True,
    }
    evidence.update(overrides)
    return evidence


def approval_evidence(**overrides) -> dict:
    evidence = execution_evidence(
        approvals=1,
        approval_status="expired",
        approval_resolution_kind="turn_terminal",
        approval_turn_terminal_observed=True,
        start_signal_observed=False,
    )
    evidence.update(overrides)
    return evidence


class PlannedTurnTests(unittest.TestCase):
    def test_two_specials_need_two_real_turns(self):
        wanted = {"ri", "ra"}
        self.assertEqual(2, accept_device.planned_model_turns(True, wanted))
        self.assertEqual(0, accept_device.planned_model_turns(False, wanted))
        self.assertEqual(1, accept_device.planned_model_turns(True, {"ri"}))

    def test_turn_budget_requires_an_exact_integer_count(self):
        self.assertTrue(accept_device.turn_count_matches(2, 2))
        for observed in (None, 0, 1, 3, True, "2"):
            with self.subTest(observed=observed):
                self.assertFalse(
                    accept_device.turn_count_matches(2, observed))

    def test_s3_is_the_synthetic_companion_without_a_turn_budget(self):
        self.assertIn("s3", accept_device.SYNTHETIC_ONLY)
        self.assertNotIn("s3", accept_device.REAL_ONLY)
        self.assertNotIn("s3", accept_device.TURN_SCENARIOS)
        self.assertIn("s3", accept_device.ALL_SCENARIOS)
        self.assertIn("s3", accept_device.METHODS)
        self.assertIn("s3", accept_device.CREDENTIAL_SCENARIOS)
        self.assertEqual(0, accept_device.planned_model_turns(False, {"s3"}))
        self.assertEqual(0, accept_device.planned_model_turns(True, {"s3"}))

    def test_specials_are_real_only_and_turn_scenarios(self):
        for letter in ("ri", "ra"):
            self.assertIn(letter, accept_device.REAL_ONLY)
            self.assertIn(letter, accept_device.TURN_SCENARIOS)
            self.assertNotIn(letter, accept_device.SYNTHETIC_ONLY)
            self.assertIn(letter, accept_device.CREDENTIAL_SCENARIOS)
            self.assertIn(letter, accept_device.ALL_SCENARIOS)
            self.assertIn(letter, accept_device.PROMPTS)
            self.assertIn(letter, accept_device.METHODS)


class ClassifierTests(unittest.TestCase):
    def test_execution_success_requires_terminal_outcome_and_free_slot(self):
        self.assertEqual(
            {"status": "passed"},
            accept_device.classify_execution_interrupt(execution_evidence()))

    def test_execution_trigger_timeout_is_not_success(self):
        verdict = accept_device.classify_execution_interrupt(
            execution_evidence(start_signal_observed=False))
        self.assertEqual("failed", verdict["status"])
        self.assertEqual("start_signal_timeout", verdict["code"])

    def test_execution_terminal_timeout_is_not_success(self):
        for status in (None, "running", "waiting_approval", "interrupting"):
            with self.subTest(status=status):
                verdict = accept_device.classify_execution_interrupt(
                    execution_evidence(run_status=status))
                self.assertEqual("failed", verdict["status"])
                self.assertIn(verdict["code"], {
                    "terminal_timeout", "unexpected_terminal:" + str(status)})

    def test_natural_completion_is_a_race_not_an_interrupt_success(self):
        verdict = accept_device.classify_execution_interrupt(
            execution_evidence(
                run_status="completed",
                interrupt_status="confirmed",
                interrupt_outcome="run_completed"))
        self.assertEqual("failed", verdict["status"])
        self.assertEqual("terminal_race_natural_completion", verdict["code"])

    def test_returned_rpc_without_settled_operation_is_unconfirmed(self):
        for status, outcome in (("accepted", None), ("dispatching", None),
                                ("unknown", "dispatch_unconfirmed"),
                                ("failed", "not_dispatched")):
            with self.subTest(status=status, outcome=outcome):
                verdict = accept_device.classify_execution_interrupt(
                    execution_evidence(
                        interrupt_status=status, interrupt_outcome=outcome))
                self.assertEqual("failed", verdict["status"])
                self.assertEqual("interrupt_unconfirmed", verdict["code"])

    def test_execution_requires_the_active_slot_to_be_released(self):
        verdict = accept_device.classify_execution_interrupt(
            execution_evidence(active_slot_run_id="run"))
        self.assertEqual("failed", verdict["status"])
        self.assertEqual("active_slot_not_released", verdict["code"])

    def test_approval_turn_termination_invalidates_without_a_decision(self):
        self.assertEqual(
            {"status": "passed"},
            accept_device.classify_approval_interrupt(approval_evidence()))

    def test_approval_still_pending_or_missing_is_not_invalidated(self):
        for overrides, code in (
                ({"pending_approvals": 1, "approval_status": "pending",
                  "approval_resolution_kind": None},
                 "approval_still_pending"),
                ({"approvals": 0, "approval_status": None,
                  "approval_resolution_kind": None,
                  "approval_turn_terminal_observed": False},
                 "approval_missing"),
                # A plain upstream closure is not a turn-terminal expiry.
                ({"approval_resolution_kind": "request_closed",
                  "approval_turn_terminal_observed": False},
                 "approval_not_invalidated"),
                ({"approval_resolution_kind": "unexpected_kind"},
                 "approval_not_invalidated"),
                ({"approval_status": "resolved"},
                 "approval_not_invalidated")):
            with self.subTest(code=code):
                verdict = accept_device.classify_approval_interrupt(
                    approval_evidence(**overrides))
                self.assertEqual("failed", verdict["status"])
                self.assertEqual(code, verdict["code"])

    def test_late_upstream_closure_keeps_the_turn_terminal_invalidation(self):
        verdict = accept_device.classify_approval_interrupt(
            approval_evidence(
                approval_resolution_kind="request_closed",
                approval_late_closure_observed=True))
        self.assertEqual({"status": "passed"}, verdict)

    def test_approval_decision_recorded_is_never_success(self):
        verdict = accept_device.classify_approval_interrupt(
            approval_evidence(approval_operations=1))
        self.assertEqual("failed", verdict["status"])
        self.assertEqual("approval_decision_recorded", verdict["code"])

    def test_approval_natural_completion_is_a_race(self):
        verdict = accept_device.classify_approval_interrupt(
            approval_evidence(run_status="completed"))
        self.assertEqual("failed", verdict["status"])
        self.assertEqual("terminal_race_natural_completion", verdict["code"])


@unittest.skipUnless(os.name == "nt", "Windows locked Host integration")
class EvidenceIntegrationTests(unittest.TestCase):
    @staticmethod
    def _running(startup) -> int:
        epoch = startup.recover_host_after_isolation(
            old_process_epoch=0,
            expected_run_id=None, expected_run_version=None,
            reason="host_restart",
            recorded_at="2026-10-30T00:00:00Z")["epoch"]
        seed_workspace_session(startup, epoch)
        startup.events.reserve_run_request(
            "session", "run", actor_id="device", request_id="start",
            expected_session_version=0, input_text="go",
            recorded_at="2026-10-30T00:00:01Z")
        startup.events.claim_run_dispatch(
            "device", "start", process_epoch=epoch,
            recorded_at="2026-10-30T00:00:02Z")
        startup.events.confirm_run_started(
            "device", "start", process_epoch=epoch,
            thread_id="thread_demo", turn_id="turn_demo",
            recorded_at="2026-10-30T00:00:03Z")
        return epoch

    @staticmethod
    def _stop(startup, epoch: int) -> dict:
        version = startup.events.connection.execute(
            "SELECT version FROM runs WHERE run_id='run'").fetchone()[0]
        startup.events.reserve_interrupt_request(
            "run", actor_id="device", request_id="stop",
            expected_version=version,
            recorded_at="2026-10-30T00:00:04Z")
        return startup.events.claim_interrupt_dispatch(
            "device", "stop", process_epoch=epoch,
            recorded_at="2026-10-30T00:00:05Z")

    def test_execution_evidence_passes_after_confirmed_interrupt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                # The marker may arrive split across deltas; the evidence
                # reader must join the stream before looking for it.
                for piece in ("RUNVORI_SLEEP_", "STARTED\n"):
                    startup.events.append_timeline(
                        "session", "run", kind="command.output",
                        payload={"item_id": "item", "command": "probe",
                                 "cwd": str(startup.data_dir)},
                        text=piece,
                        recorded_at="2026-10-30T00:00:03Z")
                self._stop(startup, epoch)
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo",
                    epoch=epoch, terminal="interrupted",
                    recorded_at="2026-10-30T00:00:06Z")
                evidence = accept_device.interrupt_evidence(
                    Path(temp), "session")
                self.assertTrue(evidence["start_signal_observed"])
                self.assertEqual("interrupted", evidence["run_status"])
                self.assertEqual("confirmed", evidence["interrupt_status"])
                self.assertEqual("run_interrupted",
                                 evidence["interrupt_outcome"])
                self.assertIsNone(evidence["active_slot_run_id"])
                self.assertEqual(
                    {"status": "passed"},
                    accept_device.classify_execution_interrupt(evidence))

    def test_approval_evidence_passes_and_late_decision_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                startup.record_approval_request(
                    "session", "run", approval_id="approval", epoch=epoch,
                    upstream_request_id="approval-request", kind="command",
                    details=reviewable_command(startup.data_dir),
                    turn_id="turn_demo",
                    recorded_at="2026-10-30T00:00:03Z")
                self._stop(startup, epoch)
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo",
                    epoch=epoch, terminal="interrupted",
                    recorded_at="2026-10-30T00:00:06Z")

                evidence = accept_device.interrupt_evidence(
                    Path(temp), "session")
                self.assertFalse(evidence["start_signal_observed"])
                self.assertEqual(1, evidence["approvals"])
                self.assertEqual(0, evidence["pending_approvals"])
                self.assertEqual("expired", evidence["approval_status"])
                self.assertEqual("turn_terminal",
                                 evidence["approval_resolution_kind"])
                self.assertTrue(
                    evidence["approval_turn_terminal_observed"])
                self.assertFalse(
                    evidence["approval_late_closure_observed"])
                self.assertEqual(0, evidence["approval_operations"])
                self.assertIsNone(evidence["active_slot_run_id"])
                self.assertEqual(
                    {"status": "passed"},
                    accept_device.classify_approval_interrupt(evidence))
                # The same record also models the trigger-timeout branch: no
                # real output marker means the phone never had a start signal.
                self.assertEqual(
                    "start_signal_timeout",
                    accept_device.classify_execution_interrupt(
                        evidence)["code"])

                with self.assertRaisesRegex(EventJournalError,
                                            "approval_expired"):
                    startup.record_approval_decision(
                        "approval", actor_id="device",
                        request_id="late-decision", expected_version=2,
                        decision="allow_once",
                        recorded_at="2026-10-30T00:00:07Z")
                count = startup.events.connection.execute(
                    "SELECT COUNT(*) FROM approval_operations").fetchone()[0]
                self.assertEqual(0, count)
                after = accept_device.interrupt_evidence(
                    Path(temp), "session")
                self.assertEqual(0, after["approval_operations"])

    def test_late_upstream_closure_preserves_turn_terminal_evidence(self):
        # Real Codex closes the pending request just after the interrupt
        # expires it. The current row becomes request_closed, but the
        # turn-terminal expiry remains the durable cause in the event stream.
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                startup.record_approval_request(
                    "session", "run", approval_id="approval", epoch=epoch,
                    upstream_request_id="approval-request", kind="command",
                    details=reviewable_command(startup.data_dir),
                    turn_id="turn_demo",
                    recorded_at="2026-10-30T00:00:03Z")
                self._stop(startup, epoch)
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo",
                    epoch=epoch, terminal="interrupted",
                    recorded_at="2026-10-30T00:00:06Z")
                closed = startup.record_approval_request_closed(
                    "approval", epoch=epoch,
                    upstream_request_id="approval-request",
                    recorded_at="2026-10-30T00:00:07Z")
                self.assertEqual("expired", closed["status"])
                self.assertEqual("request_closed",
                                 closed["resolution_kind"])

                evidence = accept_device.interrupt_evidence(
                    Path(temp), "session")
                self.assertEqual("expired", evidence["approval_status"])
                self.assertEqual("request_closed",
                                 evidence["approval_resolution_kind"])
                self.assertTrue(
                    evidence["approval_turn_terminal_observed"])
                self.assertTrue(
                    evidence["approval_late_closure_observed"])
                self.assertEqual(0, evidence["approval_operations"])
                self.assertEqual(
                    {"status": "passed"},
                    accept_device.classify_approval_interrupt(evidence))

                version = startup.events.connection.execute(
                    "SELECT version FROM approvals WHERE approval_id='approval'"
                ).fetchone()[0]
                with self.assertRaisesRegex(EventJournalError,
                                            "approval_expired"):
                    startup.record_approval_decision(
                        "approval", actor_id="device",
                        request_id="late-decision", expected_version=version,
                        decision="allow_once",
                        recorded_at="2026-10-30T00:00:08Z")
                count = startup.events.connection.execute(
                    "SELECT COUNT(*) FROM approval_operations").fetchone()[0]
                self.assertEqual(0, count)

    def test_natural_completion_wins_is_recorded_as_race(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with LockedHostJournals(Path(temp)) as startup:
                epoch = self._running(startup)
                # The marker may arrive split across deltas; the evidence
                # reader must join the stream before looking for it.
                for piece in ("RUNVORI_SLEEP_", "STARTED\n"):
                    startup.events.append_timeline(
                        "session", "run", kind="command.output",
                        payload={"item_id": "item", "command": "probe",
                                 "cwd": str(startup.data_dir)},
                        text=piece,
                        recorded_at="2026-10-30T00:00:03Z")
                self._stop(startup, epoch)
                startup.record_upstream_turn_terminal(
                    thread_id="thread_demo", turn_id="turn_demo",
                    epoch=epoch, terminal="completed",
                    recorded_at="2026-10-30T00:00:06Z")
                evidence = accept_device.interrupt_evidence(
                    Path(temp), "session")
                self.assertEqual("completed", evidence["run_status"])
                self.assertEqual("confirmed", evidence["interrupt_status"])
                self.assertEqual("run_completed",
                                 evidence["interrupt_outcome"])
                verdict = accept_device.classify_execution_interrupt(evidence)
                self.assertEqual("failed", verdict["status"])
                self.assertEqual("terminal_race_natural_completion",
                                 verdict["code"])


class TransportTests(unittest.TestCase):
    def test_emulator_keeps_nat_alias_and_loopback_bind(self):
        transport = accept_device.build_transport(
            "emulator-5554", "", "", emulator=True)
        self.assertEqual("10.0.2.2", transport["endpoint"])
        self.assertEqual("127.0.0.1", transport["listen"])
        self.assertEqual("IP:10.0.2.2", transport["san"])
        self.assertEqual("emulator_nat", transport["report"]["kind"])
        self.assertEqual("loopback", transport["report"]["bind"])
        self.assertEqual("required",
                         transport["report"]["tls"]["hostname"])

    def test_physical_device_requires_an_explicit_endpoint(self):
        with self.assertRaises(accept_device.OrchestrationFailure) as caught:
            accept_device.build_transport("R58M123", "", "", emulator=False)
        self.assertEqual("host_address_required_for_device",
                         str(caught.exception))

    def test_lan_ip_binds_the_same_literal_without_exposing_all(self):
        transport = accept_device.build_transport(
            "R58M123", "192.168.1.20", "", emulator=False)
        self.assertEqual("192.168.1.20", transport["endpoint"])
        self.assertEqual("192.168.1.20", transport["listen"])
        self.assertEqual("IP:192.168.1.20", transport["san"])
        self.assertEqual("non_loopback", transport["report"]["bind"])
        self.assertNotIn("endpoint", transport["report"])

    def test_explicit_wildcard_listener_is_recorded_as_non_loopback(self):
        transport = accept_device.build_transport(
            "R58M123", "192.168.1.20", "0.0.0.0", emulator=False)
        self.assertEqual("0.0.0.0", transport["listen"])
        self.assertEqual("non_loopback", transport["report"]["bind"])

    def test_dns_endpoint_requires_an_explicit_listener(self):
        with self.assertRaises(accept_device.OrchestrationFailure) as caught:
            accept_device.build_transport(
                "R58M123", "runvori.local", "", emulator=False)
        self.assertEqual("listen_address_required_for_dns",
                         str(caught.exception))
        transport = accept_device.build_transport(
            "R58M123", "runvori.local", "127.0.0.1", emulator=False)
        self.assertEqual("DNS:runvori.local", transport["san"])
        self.assertEqual("runvori.local", transport["url_host"])

    def test_ipv6_endpoint_is_bracketed_only_in_the_url(self):
        transport = accept_device.build_transport(
            "R58M123", "fd00::20", "", emulator=False)
        self.assertEqual("fd00::20", transport["endpoint"])
        self.assertEqual("[fd00::20]", transport["url_host"])
        self.assertEqual("IP:fd00::20", transport["san"])

    def test_endpoint_rejects_scheme_port_and_path(self):
        for value in ("https://192.168.1.20", "192.168.1.20:8443",
                      "192.168.1.20/v1", "host name"):
            with self.subTest(value=value):
                with self.assertRaises(
                        accept_device.OrchestrationFailure):
                    accept_device.build_transport(
                        "R58M123", value, "", emulator=False)

    def test_baseline_is_synthetic_and_needs_zero_model_turns(self):
        wanted = set(accept_device.DEVICE_BASELINE)
        self.assertFalse(wanted & accept_device.REAL_ONLY)
        self.assertTrue(wanted <= set(accept_device.ALL_SCENARIOS))
        self.assertEqual(0, accept_device.planned_model_turns(False, wanted))
        default = (set(accept_device.ALL_SCENARIOS)
                   - accept_device.REAL_ONLY
                   - accept_device.BASELINE_ONLY)
        for letter in ("off", "rec", "fs"):
            self.assertIn(letter, accept_device.METHODS)
            self.assertIn(letter, accept_device.ALL_SCENARIOS)
            self.assertNotIn(letter, default)


if __name__ == "__main__":
    unittest.main()
