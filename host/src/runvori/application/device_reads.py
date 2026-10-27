"""Authenticated internal reads before the product HTTPS adapter exists."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from runvori.application.command_mailbox import (
    CommandMailboxError, HostCommandMailbox,
)
from runvori.infrastructure.host_identity import valid_host_id
from runvori.infrastructure.pairing_journal import PairingJournal


class DeviceReadError(ValueError):
    """Safe refusal without credential or stored content."""


class DeviceReadGateway:
    """Check the current paired device around owner-thread mailbox reads."""

    def __init__(self, pairing_db: Path, mailbox: HostCommandMailbox, *,
                 host_id: str,
                 host_available: Callable[[], bool] | None = None):
        if (not isinstance(pairing_db, Path)
                or not isinstance(mailbox, HostCommandMailbox)
                or not valid_host_id(host_id)
                or (host_available is not None and not callable(host_available))):
            raise DeviceReadError("invalid_read_gateway")
        self._pairing_db = pairing_db
        self._mailbox = mailbox
        self._host_id = host_id
        self._host_available = host_available
        self._active_lock = threading.Lock()
        self._active: dict[threading.Event, int] = {}

    @contextmanager
    def _tracked(self, cancelled: threading.Event | None):
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise DeviceReadError("invalid_cancellation")
        signal = cancelled if cancelled is not None else threading.Event()
        with self._active_lock:
            self._active[signal] = self._active.get(signal, 0) + 1
        try:
            yield signal
        finally:
            with self._active_lock:
                count = self._active[signal] - 1
                if count:
                    self._active[signal] = count
                else:
                    del self._active[signal]
                if signal.is_set():
                    raise CommandMailboxError("request_cancelled")

    def revoke_local_device(self) -> None:
        """Commit trusted local revocation, then cancel in-flight reads."""
        self._require_host_available()
        with self._active_lock:
            with PairingJournal(self._pairing_db) as journal:
                journal.revoke_local_device()
            for signal in self._active:
                self._mailbox.cancel_wait(signal)

    def cancel_all(self) -> None:
        """Cancel every in-flight read; used when the listener stops."""
        with self._active_lock:
            signals = list(self._active)
        for signal in signals:
            try:
                self._mailbox.cancel_wait(signal)
            except CommandMailboxError:
                pass

    def _require_host_available(self) -> None:
        if self._host_available is not None and not self._host_available():
            raise DeviceReadError("host_unavailable")

    def _require_authorized(self, journal: PairingJournal, token: bytes,
                            cancelled: threading.Event | None) -> None:
        self._require_host_available()
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise DeviceReadError("invalid_cancellation")
        if cancelled is not None and cancelled.is_set():
            raise CommandMailboxError("request_cancelled")
        try:
            authorized = journal.authenticates(token, host_id=self._host_id)
        except sqlite3.Error as error:
            raise DeviceReadError("authentication_unavailable") from error
        if not authorized:
            raise DeviceReadError("unauthorized")
        if cancelled is not None and cancelled.is_set():
            raise CommandMailboxError("request_cancelled")

    def snapshot(self, *, token: bytes, session_id: str,
                 limit: int = 50,
                 cancelled: threading.Event | None = None) -> dict:
        with self._tracked(cancelled) as signal, PairingJournal(
                self._pairing_db) as journal:
            self._require_authorized(journal, token, signal)
            try:
                result = self._mailbox.get_session_view(
                    session_id=session_id, limit=limit, cancelled=signal)
            finally:
                self._require_authorized(journal, token, signal)
            return result

    def sessions(self, *, token: bytes, after_session_id: str | None = None,
                 limit: int = 50,
                 cancelled: threading.Event | None = None) -> dict:
        with self._tracked(cancelled) as signal, PairingJournal(
                self._pairing_db) as journal:
            self._require_authorized(journal, token, signal)
            try:
                result = self._mailbox.list_sessions(
                    after_session_id=after_session_id, limit=limit,
                    cancelled=signal)
            finally:
                self._require_authorized(journal, token, signal)
            return result

    def events(self, *, token: bytes, session_id: str,
               after_seq: int | None = None,
               before_seq: int | None = None, limit: int = 50,
               wait_ms: int = 0,
               cancelled: threading.Event | None = None) -> dict:
        with self._tracked(cancelled) as signal, PairingJournal(
                self._pairing_db) as journal:
            self._require_authorized(journal, token, signal)
            if (type(wait_ms) is not int or not 0 <= wait_ms <= 25000
                    or (before_seq is not None and wait_ms != 0)):
                raise DeviceReadError("invalid_wait_ms")
            if before_seq is not None:
                try:
                    result = self._mailbox.get_session_events(
                        session_id=session_id, before_seq=before_seq,
                        after_seq=after_seq, limit=limit, cancelled=signal)
                finally:
                    self._require_authorized(journal, token, signal)
                return result
            cursor = 0 if after_seq is None else after_seq
            deadline = time.monotonic() + wait_ms / 1000
            while True:
                self._require_authorized(journal, token, signal)
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                try:
                    result = self._mailbox.wait_session_events(
                        session_id=session_id, after_seq=cursor,
                        limit=limit, wait_ms=min(250, remaining_ms),
                        cancelled=signal)
                finally:
                    self._require_authorized(journal, token, signal)
                if result["events"] or time.monotonic() >= deadline:
                    return result
