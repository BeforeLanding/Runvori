"""Trusted Host-local administration over a Windows named pipe.

Only a process running as the same OS user can open the pipe, and every
request must present the admin key from the data directory. Commands are a
fixed allowlist executed on the SQLite owner thread through the mailbox; this
module never touches the database or the upstream connection directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from multiprocessing import connection
from pathlib import Path
from typing import Any, Callable


MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 32 * 1024
PIPE_PREFIX = "runvori-host-"
COMMANDS = frozenset({
    "status", "issue-pairing", "revoke-device", "register-workspace",
    "create-session", "start-thread",
})


class LocalAdminError(ValueError):
    """A safe refusal code without paths, secrets or database contents."""


def pipe_address(data_dir: Path) -> str:
    """A per-installation pipe name; the data path itself is not exposed."""
    digest = hashlib.sha256(
        str(Path(data_dir).resolve()).encode("utf-8")).hexdigest()[:16]
    return rf"\\.\pipe\{PIPE_PREFIX}{digest}"


def load_or_create_admin_key(data_dir: Path) -> bytes:
    """Keep one per-installation key; a partial or foreign file fails closed."""
    path = Path(data_dir) / "admin.key"
    if path.exists() or path.is_symlink():
        try:
            key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        except (OSError, UnicodeError, ValueError) as error:
            raise LocalAdminError("admin_key_unreadable") from error
        if len(key) < 32:
            raise LocalAdminError("admin_key_invalid")
        return key
    key = secrets.token_bytes(32)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(key.hex() + "\n", encoding="ascii")
    os.replace(staging, path)
    return key


class LocalAdminServer:
    """Accept serialised local commands until stopped."""

    def __init__(self, data_dir: Path, handler: Callable[[dict], dict],
                 *, key: bytes | None = None):
        if not isinstance(data_dir, Path) or not callable(handler):
            raise LocalAdminError("invalid_local_admin_server")
        self._handler = handler
        self._key = load_or_create_admin_key(data_dir) if key is None else key
        if not isinstance(self._key, bytes) or len(self._key) < 32:
            raise LocalAdminError("admin_key_invalid")
        self.address = pipe_address(data_dir)
        self._listener = connection.Listener(self.address, family="AF_PIPE",
                                             authkey=self._key)
        self._stopped = threading.Event()
        self._worker = threading.Thread(target=self._serve, daemon=True)
        self._worker.start()

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        try:
            self._listener.close()
        except OSError:
            pass
        self._worker.join(timeout=5)

    def _serve(self) -> None:
        while not self._stopped.is_set():
            try:
                incoming = self._listener.accept()
            except (OSError, EOFError):
                return
            try:
                with incoming:
                    # PipeConnection has no settimeout; a closed peer surfaces
                    # as EOFError/OSError and is handled below.
                    self._handle(incoming)
            except (OSError, EOFError, ValueError, UnicodeError):
                continue

    def _handle(self, incoming) -> None:
        try:
            payload = incoming.recv_bytes(MAX_REQUEST_BYTES)
            request = json.loads(payload.decode("utf-8"))
        except Exception:
            self._reply(incoming, {"status": "failed",
                                   "code": "invalid_admin_request"})
            return
        if (not isinstance(request, dict)
                or request.get("command") not in COMMANDS
                or not isinstance(request.get("arguments"), dict)):
            self._reply(incoming, {"status": "failed",
                                   "code": "invalid_admin_request"})
            return
        try:
            result = self._handler(
                {"command": request["command"],
                 "arguments": request["arguments"]})
        except Exception as error:
            code = _safe_code(error)
            self._reply(incoming, {"status": "failed", "code": code})
            return
        self._reply(incoming, result)

    @staticmethod
    def _reply(incoming, result: dict) -> None:
        try:
            encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            encoded = json.dumps({"status": "failed",
                                  "code": "admin_result_unencodable"}).encode()
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = json.dumps({"status": "failed",
                                  "code": "admin_result_too_large"}).encode()
        try:
            incoming.send_bytes(encoded)
        except (OSError, EOFError):
            pass


def _safe_code(error: BaseException) -> str:
    code = str(error)
    if code and code.isascii() and len(code) <= 64 and " " not in code:
        return code
    return "admin_command_failed"


class LocalAdminClient:
    """A CLI-side client; it never retries a possibly executed command."""

    def __init__(self, data_dir: Path, *, timeout: float = 10):
        self._key = load_or_create_admin_key(data_dir)
        self.address = pipe_address(data_dir)
        self._timeout = timeout

    def call(self, command: str, **arguments: Any) -> dict:
        if command not in COMMANDS:
            raise LocalAdminError("invalid_admin_command")
        try:
            outgoing = connection.Client(self.address, family="AF_PIPE",
                                         authkey=self._key)
        except (OSError, EOFError, ValueError) as error:
            raise LocalAdminError("host_not_running") from error
        try:
            with outgoing:
                outgoing.send_bytes(json.dumps(
                    {"command": command, "arguments": arguments},
                    ensure_ascii=False).encode("utf-8"))
                payload = outgoing.recv_bytes(MAX_RESPONSE_BYTES)
        except (OSError, EOFError, ValueError) as error:
            # The command may already have executed; the caller must not retry
            # blindly and should inspect state instead.
            raise LocalAdminError("admin_result_unknown") from error
        try:
            result = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise LocalAdminError("invalid_admin_response") from error
        if not isinstance(result, dict):
            raise LocalAdminError("invalid_admin_response")
        return result