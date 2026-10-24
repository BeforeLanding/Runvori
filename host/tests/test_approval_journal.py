import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runvori.cli import ROOT
from runvori.infrastructure.approval_journal import ApprovalJournal, JournalError


class ApprovalJournalTests(unittest.TestCase):
    def test_reopen_expires_unclaimed_and_never_reclaims_dispatched_decision(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "journal.db"
            with ApprovalJournal(database) as journal:
                epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                for name in ("pending", "recorded", "claimed"):
                    journal.record_request(approval_id=name, epoch=epoch,
                                           upstream_request_id=name, kind="file_change")
                journal.record_decision(approval_id="recorded", request_id="req_recorded",
                                        decision="accept", expected_version=1)
                journal.record_decision(approval_id="claimed", request_id="req_claimed",
                                        decision="accept", expected_version=1)
                sent_once = journal.claim_dispatch(approval_id="claimed", epoch=epoch)
                self.assertEqual(sent_once["status"], "dispatching")
                with self.assertRaisesRegex(JournalError, "dispatch_unavailable"):
                    journal.claim_dispatch(approval_id="claimed", epoch=epoch)
            with ApprovalJournal(database) as journal:
                with self.assertRaisesRegex(JournalError, "old_process_not_isolated"):
                    journal.start_epoch_after_isolation(old_process_isolated=False)
                self.assertEqual(journal.current_epoch(), epoch)
                next_epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                self.assertEqual(next_epoch, epoch + 1)
                self.assertEqual(journal.get("pending")["status"], "expired")
                self.assertEqual(journal.get("recorded")["status"], "expired")
                self.assertEqual(journal.get("claimed")["status"], "unknown")
                self.assertEqual(journal.get("claimed")["decision"], "accept")
                with self.assertRaisesRegex(JournalError, "dispatch_unavailable"):
                    journal.claim_dispatch(approval_id="recorded", epoch=next_epoch)
                with self.assertRaisesRegex(JournalError, "dispatch_unavailable"):
                    journal.claim_dispatch(approval_id="claimed", epoch=epoch)

    def test_lost_http_response_and_conflicting_decisions(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with ApprovalJournal(Path(temp) / "journal.db") as journal:
                epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                journal.record_request(approval_id="a", epoch=epoch,
                                       upstream_request_id="upstream_a", kind="command")
                journal.record_request(approval_id="b", epoch=epoch,
                                       upstream_request_id="upstream_b", kind="command")
                first = journal.record_decision(approval_id="a", request_id="req_a",
                                                decision="accept", expected_version=1)
                repeated = journal.record_decision(approval_id="a", request_id="req_a",
                                                   decision="accept", expected_version=1)
                same_decision = journal.record_decision(approval_id="a", request_id="req_other",
                                                        decision="accept", expected_version=1)
                self.assertEqual(first, repeated)
                self.assertEqual(first, same_decision)
                self.assertEqual(first["decision_request_id"], "req_a")
                with self.assertRaisesRegex(JournalError, "decision_conflict"):
                    journal.record_decision(approval_id="a", request_id="req_other",
                                            decision="decline", expected_version=1)
                with self.assertRaisesRegex(JournalError, "idempotency_conflict"):
                    journal.record_decision(approval_id="b", request_id="req_a",
                                            decision="accept", expected_version=1)
                with self.assertRaisesRegex(JournalError, "resource_changed"):
                    journal.record_decision(approval_id="b", request_id="req_b",
                                            decision="accept", expected_version=2)
                self.assertEqual(journal.get("b")["status"], "pending")

    def test_request_closed_is_distinct_from_file_effect(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with ApprovalJournal(Path(temp) / "journal.db") as journal:
                epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                journal.record_request(approval_id="a", epoch=epoch,
                                       upstream_request_id="upstream_a", kind="file_change")
                journal.record_decision(approval_id="a", request_id="req_a",
                                        decision="accept", expected_version=1)
                journal.claim_dispatch(approval_id="a", epoch=epoch)
                closed = journal.record_request_closed(approval_id="a", epoch=epoch)
                self.assertEqual(closed["status"], "resolved")
                self.assertEqual(closed["resolution_kind"], "request_closed")
                self.assertIsNone(closed["file_snapshot"])
                self.assertEqual(journal.record_request_closed(
                    approval_id="a", epoch=epoch), closed)

    def test_recovery_file_snapshot_never_changes_unknown_or_allows_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            folder = Path(temp)
            database = folder / "journal.db"
            target = folder / "probe-result.txt"
            with ApprovalJournal(database) as journal:
                epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                journal.record_request(approval_id="a", epoch=epoch,
                                       upstream_request_id="upstream_a", kind="file_change")
                journal.record_decision(approval_id="a", request_id="req_a",
                                        decision="accept", expected_version=1)
                journal.claim_dispatch(approval_id="a", epoch=epoch)
            with ApprovalJournal(database) as journal:
                journal.start_epoch_after_isolation(old_process_isolated=True)
                absent = journal.inspect_unknown_file(
                    approval_id="a", path=target, expected_bytes=b"expected\n")
                self.assertEqual(absent["file_snapshot"], "absent")
                target.write_bytes(b"expected\n")
                present = journal.inspect_unknown_file(
                    approval_id="a", path=target, expected_bytes=b"expected\n")
                self.assertEqual(present["file_snapshot"], "matches")
                self.assertEqual(present["status"], "unknown")
                self.assertIsNone(present["resolution_kind"])
                with self.assertRaisesRegex(JournalError, "dispatch_unavailable"):
                    journal.claim_dispatch(approval_id="a", epoch=journal.current_epoch())

    def test_two_connections_accept_only_one_decision(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "journal.db"
            with ApprovalJournal(database) as journal:
                epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                journal.record_request(approval_id="a", epoch=epoch,
                                       upstream_request_id="upstream_a", kind="command")

            def decide(decision):
                with ApprovalJournal(database) as journal:
                    try:
                        return journal.record_decision(
                            approval_id="a", request_id="req_" + decision,
                            decision=decision, expected_version=1)["decision"]
                    except JournalError as error:
                        return str(error)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(decide, ("accept", "decline")))
            self.assertEqual(len(set(results) & {"accept", "decline"}), 1)
            self.assertIn("decision_conflict", results)
            with ApprovalJournal(database) as journal:
                self.assertEqual(journal.get("a")["status"], "decision_recorded")

    def test_reused_upstream_id_is_scoped_to_epoch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            database = Path(temp) / "journal.db"
            with ApprovalJournal(database) as journal:
                old_epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                journal.record_request(approval_id="old", epoch=old_epoch,
                                       upstream_request_id="7", kind="command")
                new_epoch = journal.start_epoch_after_isolation(old_process_isolated=True)
                journal.record_request(approval_id="new", epoch=new_epoch,
                                       upstream_request_id="7", kind="command")
                with self.assertRaisesRegex(JournalError, "approval_expired"):
                    journal.record_decision(approval_id="old", request_id="old_key",
                                            decision="accept", expected_version=2)
                with self.assertRaisesRegex(JournalError, "stale_epoch"):
                    journal.record_request_closed(approval_id="old", epoch=old_epoch)
                current = journal.record_decision(approval_id="new", request_id="new_key",
                                                  decision="decline", expected_version=1)
                self.assertEqual(current["status"], "decision_recorded")
                self.assertEqual(journal.get("old")["status"], "expired")


if __name__ == "__main__":
    unittest.main()
