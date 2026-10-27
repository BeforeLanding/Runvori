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

from runvori.infrastructure.host_identity import valid_host_id


PAIRING_SECONDS = 300
SECRET_BYTES = 32


class PairingError(RuntimeError):
    """A stable rejection code without credential material."""


class PairingJournal:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self._initialize_schema()
        except BaseException:
            self.connection.close()
            raise

    def _initialize_schema(self) -> None:
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS pairing_challenge (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                secret_digest BLOB NOT NULL,
                expires_at INTEGER NOT NULL,
                consumed INTEGER NOT NULL CHECK (consumed IN (0, 1)),
                host_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paired_devices (
                device_id TEXT PRIMARY KEY,
                token_digest BLOB NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                host_id TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_device
                ON paired_devices(active) WHERE active = 1;
        """)
        for table in ("pairing_challenge", "paired_devices"):
            if "host_id" not in {row["name"] for row in self.connection.execute(
                    f"PRAGMA table_info({table})")}:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN host_id TEXT")
        legacy = (self.connection.execute(
            "SELECT 1 FROM pairing_challenge WHERE host_id IS NULL").fetchone()
            or self.connection.execute(
                "SELECT 1 FROM paired_devices WHERE active=1 AND host_id IS NULL").fetchone())
        if legacy:
            self._begin()
            try:
                self.connection.execute(
                    "DELETE FROM pairing_challenge WHERE host_id IS NULL")
                self.connection.execute(
                    "UPDATE paired_devices SET active=0 WHERE host_id IS NULL")
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> PairingJournal:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _begin(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def issue_local_secret(self, *, host_id: str) -> tuple[bytes, int]:
        """Replace any unconsumed challenge; reject if a device is active."""
        if not valid_host_id(host_id):
            raise PairingError("invalid_host_id")
        secret = secrets.token_bytes(SECRET_BYTES)
        expires_at = int(self.clock()) + PAIRING_SECONDS
        self._begin()
        try:
            if self.connection.execute(
                    "SELECT 1 FROM paired_devices WHERE active = 1").fetchone():
                raise PairingError("device_already_paired")
            self.connection.execute("DELETE FROM pairing_challenge")
            self.connection.execute(
                "INSERT INTO pairing_challenge "
                "(singleton, secret_digest, expires_at, consumed, host_id) "
                "VALUES (1, ?, ?, 0, ?)",
                (hashlib.sha256(secret).digest(), expires_at, host_id))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return secret, expires_at

    def exchange(self, secret: bytes, *, host_id: str) -> tuple[str, bytes]:
        """Consume once and commit the sole active token before returning it."""
        if (not valid_host_id(host_id) or not isinstance(secret, bytes)
                or len(secret) != SECRET_BYTES):
            raise PairingError("pairing_unavailable")
        candidate = hashlib.sha256(secret).digest()
        token = secrets.token_bytes(SECRET_BYTES)
        device_id = secrets.token_hex(16)
        self._begin()
        try:
            now = int(self.clock())
            row = self.connection.execute(
                "SELECT secret_digest, expires_at, consumed, host_id "
                "FROM pairing_challenge "
                "WHERE singleton = 1").fetchone()
            if (row is None or row["consumed"] or now >= row["expires_at"]
                    or row["host_id"] != host_id
                    or not hmac.compare_digest(candidate, row["secret_digest"])):
                raise PairingError("pairing_unavailable")
            if self.connection.execute(
                    "SELECT 1 FROM paired_devices WHERE active = 1").fetchone():
                raise PairingError("pairing_unavailable")
            self.connection.execute(
                "UPDATE pairing_challenge SET consumed = 1 WHERE singleton = 1")
            self.connection.execute(
                "INSERT INTO paired_devices (device_id, token_digest, active, host_id) "
                "VALUES (?, ?, 1, ?)",
                (device_id, hashlib.sha256(token).digest(), host_id))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return device_id, token

    def authenticates(self, token: bytes, *, host_id: str) -> bool:
        return self.authenticate(token, host_id=host_id) is not None

    def authenticate(self, token: bytes, *, host_id: str) -> str | None:
        """Return the active device identity, or None; never guess a match."""
        if (not valid_host_id(host_id) or not isinstance(token, bytes)
                or len(token) != SECRET_BYTES):
            return None
        row = self.connection.execute(
            "SELECT device_id, token_digest, host_id FROM paired_devices "
            "WHERE active = 1").fetchone()
        if row is None or row["host_id"] != host_id:
            return None
        if not hmac.compare_digest(hashlib.sha256(token).digest(),
                                   row["token_digest"]):
            return None
        return row["device_id"]

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
