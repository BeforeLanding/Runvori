"""Compose recovered Host identity with its local pairing and read boundary."""

import sqlite3
from pathlib import Path

from runvori.application.command_mailbox import HostCommandMailbox
from runvori.application.device_reads import DeviceReadGateway
from runvori.infrastructure.locked_journal import LockedHostJournals
from runvori.infrastructure.pairing_journal import PairingJournal


class HostDeviceAccessError(ValueError):
    """Safe startup refusal without path or credential material."""


class HostDeviceAccess:
    """Bind all device operations to one recovered, locked Host instance."""

    def __init__(self, journals: LockedHostJournals,
                 mailbox: HostCommandMailbox):
        if (not isinstance(journals, LockedHostJournals)
                or not isinstance(mailbox, HostCommandMailbox)):
            raise HostDeviceAccessError("invalid_host_device_access")
        host_id = journals.current_host_id()
        data_dir = journals.data_dir
        for segment in (data_dir, *data_dir.parents):
            if segment.is_symlink() or (hasattr(segment, "is_junction")
                                        and segment.is_junction()):
                raise HostDeviceAccessError("unsafe_host_data_dir")
        pairing_db = data_dir / "pairing.db"
        if (pairing_db.is_symlink() or (hasattr(pairing_db, "is_junction")
                                       and pairing_db.is_junction())
                or (pairing_db.exists() and not pairing_db.is_file())):
            raise HostDeviceAccessError("unsafe_pairing_store")
        with PairingJournal(pairing_db):
            pass  # Initialize or migrate only after locked Host recovery.
        self.host_id = host_id
        self._pairing_db = pairing_db
        self._journals = journals
        self._owner_events = journals.events
        self.reads = DeviceReadGateway(
            pairing_db, mailbox, host_id=host_id,
            host_available=self._is_active)

    def _is_active(self) -> bool:
        return (self._journals.events is self._owner_events
                and self._journals.lock is not None
                and self._journals._recovered)

    def _require_active(self) -> None:
        if not self._is_active():
            raise HostDeviceAccessError("host_unavailable")

    def issue_local_secret(self) -> tuple[bytes, int]:
        self._require_active()
        with PairingJournal(self._pairing_db) as journal:
            return journal.issue_local_secret(host_id=self.host_id)

    def exchange(self, secret: bytes) -> tuple[str, bytes]:
        self._require_active()
        with PairingJournal(self._pairing_db) as journal:
            return journal.exchange(secret, host_id=self.host_id)

    def authenticate(self, token: bytes) -> str | None:
        """Resolve a bearer token to the device identity on the server side."""
        self._require_active()
        try:
            with PairingJournal(self._pairing_db) as journal:
                return journal.authenticate(token, host_id=self.host_id)
        except sqlite3.Error as error:
            raise HostDeviceAccessError("authentication_unavailable") from error

    def revoke_local_device(self) -> None:
        self._require_active()
        self.reads.revoke_local_device()
