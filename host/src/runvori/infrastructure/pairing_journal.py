"""Standalone SQLite proof of one-time local pairing and single-device issuance.

No HTTP API, rate limiting, TLS binding, or Android credential storage is
implemented here. Only a trusted Host-local caller may issue or revoke.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path


PAIRING_SECONDS = 300
SECRET_BYTES = 32


class PairingError(RuntimeError):
    """A stable rejection code without credential material."""


class PairingJournal:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS pairing_challenge (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                secret_digest BLOB NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed INTEGER NOT NULL CHECK (consumed IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS paired_devices (
                device_id TEXT PRIMARY KEY,
                token_digest BLOB NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_device
                ON paired_devices(active) WHERE active = 1;
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> PairingJournal:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def issue_local_secret(self) -> tuple[bytes, int]:
        """Replace any unconsumed challenge; reject if a device is active."""
        secret = secrets.token_bytes(SECRET_BYTES)
        expires_at = int(self.clock()) + PAIRING_SECONDS
        self._begin()
        try:
            if self.connection.execute(
                    "SELECT 1 FROM paired_devices WHERE active = 1").fetchone():
                raise PairingError("device_already_paired")
            self.connection.execute("DELETE FROM pairing_challenge")
            self.connection.execute(
                "INSERT INTO pairing_challenge (singleton, secret_digest, expires_at, consumed) "
                "VALUES (1, ?, ?, 0)",
                (hashlib.sha256(secret).digest(), expires_at))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return secret, expires_at

    def exchange(self, secret: bytes) -> tuple[str, bytes]:
        """Consume once and commit the sole active token before returning it."""
        if not isinstance(secret, bytes) or len(secret) != SECRET_BYTES:
            raise PairingError("pairing_unavailable")
        candidate = hashlib.sha256(secret).digest()
        token = secrets.token_bytes(SECRET_BYTES)
        device_id = secrets.token_hex(16)
        self._begin()
        try:
            now = int(self.clock())
            row = self.connection.execute(
                "SELECT secret_digest, expires_at, consumed FROM pairing_challenge "
                "WHERE singleton = 1").fetchone()
            if (row is None or row["consumed"] or now >= row["expires_at"]
                    or not hmac.compare_digest(candidate, row["secret_digest"])):
                raise PairingError("pairing_unavailable")
            if self.connection.execute(
                    "SELECT 1 FROM paired_devices WHERE active = 1").fetchone():
                raise PairingError("pairing_unavailable")
            self.connection.execute(
                "UPDATE pairing_challenge SET consumed = 1 WHERE singleton = 1")
            self.connection.execute(
                "INSERT INTO paired_devices (device_id, token_digest, active) "
                "VALUES (?, ?, 1)", (device_id, hashlib.sha256(token).digest()))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return device_id, token

    def authenticates(self, token: bytes) -> bool:
        if not isinstance(token, bytes) or len(token) != SECRET_BYTES:
            return False
        row = self.connection.execute(
            "SELECT token_digest FROM paired_devices WHERE active = 1").fetchone()
        return bool(row and hmac.compare_digest(
            hashlib.sha256(token).digest(), row["token_digest"]))

    def revoke_local_device(self) -> None:
        """A Host-local action; invalidate the active token and pending secret."""
        self._begin()
        try:
            self.connection.execute("UPDATE paired_devices SET active = 0 WHERE active = 1")
            self.connection.execute("DELETE FROM pairing_challenge")
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
