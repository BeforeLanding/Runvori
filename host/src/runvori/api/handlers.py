"""Request routing and business mapping for the Runvori v1 HTTPS API.

Every data operation is a mailbox command executed by the Host's SQLite owner
thread; this module never opens the database or the upstream connection.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Callable
from urllib.parse import parse_qs

from runvori.api.auth import RateLimiter, error_body
from runvori.application.command_mailbox import (
    CommandMailboxError, HostCommandMailbox,
)
from runvori.application.device_reads import DeviceReadError, DeviceReadGateway
from runvori.application.host_device_access import (
    HostDeviceAccess, HostDeviceAccessError,
)
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.pairing_journal import PairingError


PROTOCOL_VERSION = "1"
MAX_BODY_BYTES = 64 * 1024
MAX_PAGE_BYTES = 256 * 1024
MAX_INPUT_BYTES = 16 * 1024
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_WAIT_MS = 25000
HOST_VERSION = "0.0.2"

STATUS_BY_CODE = {
    "unauthorized": 401,
    "host_unavailable": 503,
    "protocol_incompatible": 409,
    "invalid_request": 400,
    "invalid_cursor": 400,
    "cursor_ahead": 409,
    "session_not_found": 404,
    "run_not_found": 404,
    "approval_not_found": 404,
    "operation_not_found": 404,
    "approval_expired": 410,
    "approval_details_insufficient": 409,
    "resource_changed": 409,
    "decision_conflict": 409,
    "idempotency_conflict": 409,
    "active_run_exists": 409,
    "thread_unavailable": 409,
    "turn_not_bound": 409,
    "active_slot_mismatch": 409,
    "execution_unavailable": 503,
    "command_result_unknown": 503,
    "dispatch_signal_unavailable": 503,
    "host_stopped": 503,
    "request_cancelled": 409,
    "payload_too_large": 413,
    "rate_limited": 429,
    "pairing_unavailable": 409,
    "internal_error": 500,
}


class ApiError(Exception):
    """A stable error code; the message comes from the shared error table."""

    def __init__(self, code: str, *, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status or STATUS_BY_CODE.get(code, 500)


def parse_bearer(header: str | None) -> bytes:
    """Recover an opaque 32-byte device token from an Authorization header."""
    if not isinstance(header, str) or not header.startswith("Bearer "):
        raise ApiError("unauthorized")
    encoded = header[len("Bearer "):].strip()
    try:
        token = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ApiError("unauthorized") from error
    if len(token) != 32:
        raise ApiError("unauthorized")
    return token


def _query_int(query: dict, name: str, default: int | None = None) -> int | None:
    values = query.get(name)
    if values is None:
        return default
    if len(values) != 1:
        raise ApiError("invalid_cursor")
    try:
        return int(values[0])
    except (TypeError, ValueError) as error:
        raise ApiError("invalid_cursor") from error


def _body_int(body: dict, name: str, *, minimum: int) -> int:
    value = body.get(name)
    if type(value) is not int or value < minimum:
        raise ApiError("invalid_request")
    return value


def _body_str(body: dict, name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise ApiError("invalid_request")
    return value


def _call(operation: Callable[[], dict]) -> dict:
    """Translate mailbox/journal refusals into protocol error codes."""
    try:
        return operation()
    except (CommandMailboxError, DeviceReadError, EventJournalError) as error:
        code = str(error)
        raise ApiError(code if code in STATUS_BY_CODE else "internal_error") \
            from error


class ApiHandlers:
    """Pure request-to-command mapping; the HTTP layer owns sockets only."""

    def __init__(self, *, host_name: str, device_access: HostDeviceAccess,
                 mailbox: HostCommandMailbox, reads: DeviceReadGateway,
                 limiter: RateLimiter, now: Callable[[], str]):
        self.host_name = host_name
        self.device_access = device_access
        self.mailbox = mailbox
        self.reads = reads
        self.limiter = limiter
        self.now = now

    # -- pairing ---------------------------------------------------------
    def exchange(self, body: dict, *, source: str) -> tuple[int, dict]:
        allowed, retry_after = self.limiter.allow(source)
        if not allowed:
            return 429, error_body("rate_limited"), {"Retry-After":
                                                     str(retry_after)}
        encoded = body.get("pairing_secret")
        if not isinstance(encoded, str) or not encoded:
            raise ApiError("invalid_request")
        try:
            secret = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ApiError("pairing_unavailable") from error
        try:
            device_id, token = self.device_access.exchange(secret)
        except PairingError as error:
            raise ApiError("pairing_unavailable") from error
        except (HostDeviceAccessError, OSError) as error:
            raise ApiError("host_unavailable") from error
        return 200, {
            "device_id": device_id,
            "device_token": base64.b64encode(token).decode("ascii"),
            "host_id": self.device_access.host_id,
            "protocol": PROTOCOL_VERSION,
            "server_time": self.now(),
        }, {}

    def authenticate(self, header: str | None) -> tuple[str, bytes]:
        """Device identity used for idempotency is derived here, never sent."""
        token = parse_bearer(header)
        device_id = self.device_access.authenticate(token)
        if device_id is None:
            raise ApiError("unauthorized")
        return device_id, token

    def require_protocol(self, value: str | None) -> None:
        if value != PROTOCOL_VERSION:
            raise ApiError("protocol_incompatible")

    # -- routing ---------------------------------------------------------
    def dispatch(self, device_id: str, token: bytes, method: str, path: str,
                 query_text: str, body: dict) -> tuple[int, dict, dict]:
        query = parse_qs(query_text, keep_blank_values=True)
        parts = [part for part in path.strip("/").split("/")]
        if path == "/v1/host":
            return self._host(method)
        if len(parts) == 2 and parts[0] == "v1" and parts[1] == "sessions":
            return self._sessions(method, token, query)
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "sessions":
            session_id, action = parts[2], parts[3]
            if action == "snapshot":
                return self._snapshot(method, token, session_id, query)
            if action == "events":
                return self._events(method, token, session_id, query)
            if action == "runs":
                return self._start_run(method, device_id, session_id, body)
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "runs":
            return self._run_detail(method, token, parts[2])
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "runs" \
                and parts[3] == "results":
            return self._run_results(method, token, parts[2])
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "runs" \
                and parts[3] == "commands":
            return self._run_commands(method, token, parts[2], query)
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "runs" \
                and parts[3] == "interrupt":
            return self._interrupt(method, device_id, parts[2], body)
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "runs" \
                and parts[3] == "approvals":
            return self._run_approvals(method, parts[2], query)
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "approvals":
            return self._approval(method, parts[2])
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "approvals" \
                and parts[3] == "decision":
            return self._decide(method, device_id, parts[2], body)
        if len(parts) == 3 and parts[0] == "v1" and parts[1] == "operations":
            return self._operation(method, device_id, parts[2], query)
        raise ApiError("invalid_request", status=404)

    # -- endpoints -------------------------------------------------------
    def _run_detail(self, method: str, token: bytes,
                    run_id: str) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        view = _call(lambda: self.reads.run(
            token=token, run_id=run_id))
        view["server_time"] = self.now()
        return 200, view, {}

    def _run_results(self, method: str, token: bytes,
                     run_id: str) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        view = _call(lambda: self.reads.run_results(
            token=token, run_id=run_id))
        view["server_time"] = self.now()
        return 200, view, {}

    def _run_commands(self, method: str, token: bytes, run_id: str,
                      query: dict) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        limit = _query_int(query, "limit", DEFAULT_LIMIT)
        if limit is None or not 1 <= limit <= MAX_LIMIT:
            raise ApiError("invalid_cursor")
        cursor_values = query.get("cursor")
        cursor = None
        if cursor_values is not None:
            if len(cursor_values) != 1 or not cursor_values[0]:
                raise ApiError("invalid_cursor")
            cursor = cursor_values[0]
        page = _call(lambda: self.reads.run_commands(
            token=token, run_id=run_id, after_item_id=cursor, limit=limit))
        page["server_time"] = self.now()
        return 200, page, {}

    def _host(self, method: str) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        view = _call(self.mailbox.get_host_view)
        return 200, {
            "host_id": view["host_id"],
            "host_name": self.host_name,
            "host_version": HOST_VERSION,
            "protocol": PROTOCOL_VERSION,
            "protocol_min": PROTOCOL_VERSION,
            "codex_compatible": True,
            "execution_available": True,
            "epoch": view["epoch"],
            "active_run_id": view["active_run_id"],
            "server_time": self.now(),
        }, {}

    def _sessions(self, method: str, token: bytes,
                  query: dict) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        limit = _query_int(query, "limit", DEFAULT_LIMIT)
        if limit is None or not 1 <= limit <= MAX_LIMIT:
            raise ApiError("invalid_cursor")
        cursor_values = query.get("cursor")
        cursor = None
        if cursor_values is not None:
            if len(cursor_values) != 1 or not cursor_values[0]:
                raise ApiError("invalid_cursor")
            cursor = cursor_values[0]
        page = _call(lambda: self.reads.sessions(
            token=token, after_session_id=cursor, limit=limit))
        return 200, {**page,
                     "server_time": self.now()}, {}

    def _snapshot(self, method: str, token: bytes, session_id: str,
                  query: dict) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        limit = _query_int(query, "limit", DEFAULT_LIMIT)
        if limit is None or not 1 <= limit <= MAX_LIMIT:
            raise ApiError("invalid_cursor")
        snapshot = _call(lambda: self.reads.snapshot(
            token=token, session_id=session_id, limit=limit))
        snapshot["host_id"] = self.device_access.host_id
        snapshot["server_time"] = self.now()
        return 200, snapshot, {}

    def _events(self, method: str, token: bytes, session_id: str,
                query: dict) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        limit = _query_int(query, "limit", DEFAULT_LIMIT)
        if limit is None or not 1 <= limit <= MAX_LIMIT:
            raise ApiError("invalid_cursor")
        after = _query_int(query, "after_seq")
        before = _query_int(query, "before_seq")
        wait = _query_int(query, "wait_ms", 0)
        if after is not None and before is not None:
            raise ApiError("invalid_cursor")
        if wait is None or not 0 <= wait <= MAX_WAIT_MS:
            raise ApiError("invalid_cursor")
        page = _call(lambda: self.reads.events(
            token=token, session_id=session_id, after_seq=after,
            before_seq=before, limit=limit, wait_ms=wait))
        return 200, page, {}

    def _start_run(self, method: str, device_id: str, session_id: str,
                   body: dict) -> tuple[int, dict, dict]:
        if method != "POST":
            raise ApiError("invalid_request", status=405)
        request_id = _body_str(body, "request_id")
        expected = _body_int(body, "expected_session_version", minimum=0)
        input_text = _body_str(body, "input")
        if len(input_text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ApiError("payload_too_large")
        result = _call(lambda: self.mailbox.start_run(
            session_id=session_id, actor_id=device_id,
            request_id=request_id, expected_session_version=expected,
            input_text=input_text))
        return _accepted(result)

    def _interrupt(self, method: str, device_id: str, run_id: str,
                   body: dict) -> tuple[int, dict, dict]:
        if method != "POST":
            raise ApiError("invalid_request", status=405)
        request_id = _body_str(body, "request_id")
        expected = _body_int(body, "expected_version", minimum=1)
        result = _call(lambda: self.mailbox.interrupt_run(
            run_id=run_id, actor_id=device_id, request_id=request_id,
            expected_version=expected))
        if result.get("status") == "terminal":
            return 200, result, {}
        return _accepted(result)

    def _approval(self, method: str, approval_id: str):
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        view = _call(lambda: self.mailbox.get_approval_view(
            approval_id=approval_id))
        return 200, view, {}

    def _run_approvals(self, method: str, run_id: str,
                       query: dict) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        status_values = query.get("status")
        status = None
        if status_values is not None:
            if len(status_values) != 1 or status_values[0] not in {
                    "pending", "decision_recorded", "dispatching", "resolved",
                    "expired", "unknown"}:
                raise ApiError("invalid_request")
            status = status_values[0]
        page = _call(lambda: self.mailbox.list_run_approvals(
            run_id=run_id, status=status))
        return 200, page, {}

    def _decide(self, method: str, device_id: str, approval_id: str,
                body: dict) -> tuple[int, dict, dict]:
        if method != "POST":
            raise ApiError("invalid_request", status=405)
        request_id = _body_str(body, "request_id")
        expected = _body_int(body, "expected_version", minimum=1)
        decision = _body_str(body, "decision")
        if decision not in {"allow_once", "deny"}:
            raise ApiError("invalid_request")
        result = _call(lambda: self.mailbox.decide_approval(
            approval_id=approval_id, actor_id=device_id,
            request_id=request_id, expected_version=expected,
            decision=decision))
        return _accepted(result)

    def _operation(self, method: str, device_id: str, request_id: str,
                   query: dict) -> tuple[int, dict, dict]:
        if method != "GET":
            raise ApiError("invalid_request", status=405)
        kinds = query.get("kind", ["run"])
        if len(kinds) != 1 or kinds[0] not in {"run", "interrupt", "approval"}:
            raise ApiError("invalid_request")
        operation = _call(lambda: self.mailbox.get_operation(
            kind=kinds[0], actor_id=device_id, request_id=request_id))
        return 200, operation, {}


def _accepted(result: dict) -> tuple[int, dict, dict]:
    """202 means persisted and queued; it never means upstream confirmed."""
    if result.get("status") in {"accepted", "dispatching"}:
        return 202, result, {}
    return 200, result, {}