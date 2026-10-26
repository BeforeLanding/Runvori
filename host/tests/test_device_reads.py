"""Device authentication gates internal snapshot and event reads."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from runvori.application.command_mailbox import (
    CommandMailboxError, HostCommandMailbox,
)
from runvori.application.device_reads import DeviceReadError, DeviceReadGateway
from runvori.cli import ROOT
from runvori.infrastructure.event_journal import EventJournal
from runvori.infrastructure.pairing_journal import PairingJournal

HOST_ID = "host_" + "1" * 32
OTHER_HOST_ID = "host_" + "2" * 32


class FakeMailbox(HostCommandMailbox):
    def __init__(self, on_read=None):
        super().__init__()
        self.calls = []
        self.on_read = on_read

    def get_session_view(self, **kwargs):
        self.calls.append(("snapshot", kwargs))
        if self.on_read:
            self.on_read()
        return {"session_id": kwargs["session_id"], "snapshot_seq": 1}

    def wait_session_events(self, **kwargs):
        self.calls.append(("wait", kwargs))
        if self.on_read:
            self.on_read()
        return {"events": [], "next_after_seq": kwargs["after_seq"],
                "latest_seq": kwargs["after_seq"], "has_more": False}

    def get_session_events(self, **kwargs):
        self.calls.append(("history", kwargs))
        if self.on_read:
            self.on_read()
        return {"events": [{"seq": 1}], "next_before_seq": 1,
                "latest_seq": 1, "has_more": False}


class DeviceReadTests(unittest.TestCase):
    def test_new_host_identity_rejects_old_device_before_read(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            root = Path(temp)
            with EventJournal(root / "old-host.db") as old_host:
                old_id = old_host.get_host_id()
            with PairingJournal(root / "pairing.db") as pairing:
                secret, _ = pairing.issue_local_secret(host_id=old_id)
                _, token = pairing.exchange(secret, host_id=old_id)
            with EventJournal(root / "new-host.db") as rebuilt:
                new_id = rebuilt.get_host_id()
            self.assertNotEqual(old_id, new_id)
            mailbox = FakeMailbox()
            gateway = DeviceReadGateway(root / "pairing.db", mailbox,
                                        host_id=new_id)
            with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                gateway.snapshot(token=token, session_id="session")
            self.assertEqual(mailbox.calls, [])
            gateway.revoke_local_device()
            with PairingJournal(root / "pairing.db") as pairing:
                replacement, _ = pairing.issue_local_secret(host_id=new_id)
                _, fresh_token = pairing.exchange(replacement, host_id=new_id)
            self.assertEqual(gateway.snapshot(
                token=fresh_token, session_id="session")["snapshot_seq"], 1)

    def test_local_revoke_cancels_active_long_poll(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            db = Path(temp) / "pairing.db"
            with PairingJournal(db) as pairing:
                secret, _ = pairing.issue_local_secret(host_id=HOST_ID)
                _, token = pairing.exchange(secret, host_id=HOST_ID)

            class BlockingMailbox(HostCommandMailbox):
                def __init__(self):
                    super().__init__()
                    self.entered = threading.Event()

                def wait_session_events(self, **kwargs):
                    self.entered.set()
                    kwargs["cancelled"].wait(timeout=2)
                    raise CommandMailboxError("request_cancelled")

            mailbox = BlockingMailbox()
            gateway = DeviceReadGateway(db, mailbox, host_id=HOST_ID)
            outcome = {}

            def read():
                try:
                    gateway.events(token=token, session_id="session",
                                   wait_ms=25000)
                except CommandMailboxError as error:
                    outcome["error"] = str(error)

            worker = threading.Thread(target=read)
            worker.start()
            self.assertTrue(mailbox.entered.wait(timeout=2))
            gateway.revoke_local_device()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(outcome["error"], "request_cancelled")
            with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                gateway.snapshot(token=token, session_id="session")

    def test_local_revoke_cancels_queued_snapshot(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            db = Path(temp) / "pairing.db"
            with PairingJournal(db) as pairing:
                secret, _ = pairing.issue_local_secret(host_id=HOST_ID)
                _, token = pairing.exchange(secret, host_id=HOST_ID)
            mailbox = HostCommandMailbox()
            gateway = DeviceReadGateway(db, mailbox, host_id=HOST_ID)
            outcome = {}

            def read():
                try:
                    gateway.snapshot(token=token, session_id="session")
                except CommandMailboxError as error:
                    outcome["error"] = str(error)

            worker = threading.Thread(target=read)
            worker.start()
            deadline = time.monotonic() + 2
            while mailbox._items.qsize() != 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(mailbox._items.qsize(), 1)
            gateway.revoke_local_device()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(outcome["error"], "request_cancelled")
            self.assertEqual(gateway._active, {})

    def test_paired_snapshot_crosses_real_owner_mailbox(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            root = Path(temp)
            with EventJournal(root / "events.db") as events:
                host_id = events.get_host_id()
                with PairingJournal(root / "pairing.db") as pairing:
                    secret, _ = pairing.issue_local_secret(host_id=host_id)
                    _, token = pairing.exchange(secret, host_id=host_id)
                events.create_session("session", {"session_version": 0})
                mailbox = HostCommandMailbox()
                gateway = DeviceReadGateway(root / "pairing.db", mailbox,
                                            host_id=host_id)
                outcome = {}

                def read():
                    outcome["snapshot"] = gateway.snapshot(
                        token=token, session_id="session")

                worker = threading.Thread(target=read)
                worker.start()
                deadline = time.monotonic() + 2
                while not mailbox.serve_once(
                        SimpleNamespace(events=events),
                        recorded_at="2026-10-26T00:00:00Z",
                        run_starts=None, interrupts=None):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.005)
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(outcome["snapshot"]["session_id"], "session")
                self.assertEqual(outcome["snapshot"]["snapshot_seq"], 0)

    def test_authentication_and_recheck_after_snapshot(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            db = Path(temp) / "pairing.db"
            with PairingJournal(db) as pairing:
                secret, _ = pairing.issue_local_secret(host_id=HOST_ID)
                _, token = pairing.exchange(secret, host_id=HOST_ID)
            mailbox = FakeMailbox()
            gateway = DeviceReadGateway(db, mailbox, host_id=HOST_ID)
            with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                gateway.snapshot(token=b"wrong", session_id="session")
            self.assertEqual(mailbox.calls, [])
            self.assertEqual(gateway.snapshot(
                token=token, session_id="session")["snapshot_seq"], 1)

            def revoke():
                with PairingJournal(db) as pairing:
                    pairing.revoke_local_device()

            mailbox.on_read = revoke
            with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                gateway.snapshot(token=token, session_id="session")

            with PairingJournal(db) as pairing:
                secret, _ = pairing.issue_local_secret(host_id=HOST_ID)
                _, replacement = pairing.exchange(secret, host_id=HOST_ID)

            def revoke_and_fail():
                revoke()
                raise RuntimeError("storage_error")

            mailbox.on_read = revoke_and_fail
            with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                gateway.snapshot(token=replacement, session_id="session")

    def test_long_poll_rechecks_revocation_and_cancellation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            db = Path(temp) / "pairing.db"
            with PairingJournal(db) as pairing:
                secret, _ = pairing.issue_local_secret(host_id=HOST_ID)
                _, token = pairing.exchange(secret, host_id=HOST_ID)

            def revoke_during_wait():
                time.sleep(0.01)
                with PairingJournal(db) as pairing:
                    pairing.revoke_local_device()

            mailbox = FakeMailbox(revoke_during_wait)
            gateway = DeviceReadGateway(db, mailbox, host_id=HOST_ID)
            with self.assertRaisesRegex(DeviceReadError, "unauthorized"):
                gateway.events(token=token, session_id="session",
                               after_seq=4, wait_ms=25000)
            self.assertEqual(len(mailbox.calls), 1)
            self.assertLessEqual(mailbox.calls[0][1]["wait_ms"], 250)

            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaisesRegex(CommandMailboxError, "request_cancelled"):
                gateway.events(token=token, session_id="session",
                               cancelled=cancelled)
            self.assertEqual(len(mailbox.calls), 1)

    def test_history_read_and_invalid_wait_boundary(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            db = Path(temp) / "pairing.db"
            with PairingJournal(db) as pairing:
                secret, _ = pairing.issue_local_secret(host_id=HOST_ID)
                _, token = pairing.exchange(secret, host_id=HOST_ID)
            mailbox = FakeMailbox()
            gateway = DeviceReadGateway(db, mailbox, host_id=HOST_ID)
            page = gateway.events(
                token=token, session_id="session", before_seq=2)
            self.assertEqual(page["events"], [{"seq": 1}])
            with self.assertRaisesRegex(DeviceReadError, "invalid_wait_ms"):
                gateway.events(token=token, session_id="session",
                               before_seq=2, wait_ms=1)
            self.assertEqual(len(mailbox.calls), 1)


if __name__ == "__main__":
    unittest.main()
