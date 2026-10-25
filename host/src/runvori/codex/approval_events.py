"""Strict identity mapping for observed app-server approval messages.

This is an adapter slice, not a background event pump or review-content mapper.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any


MAX_REQUEST_ID_BYTES = 128


class ApprovalEventError(ValueError):
    """A safe rejection code without upstream payload content."""


def upstream_request_key(request_id: str | int) -> str:
    """Keep JSON-RPC string and integer identifiers in separate namespaces."""
    if type(request_id) is int and -(2**63) <= request_id < 2**63:
        return f"int:{request_id}"
    if type(request_id) is str and request_id:
        try:
            if len(request_id.encode("utf-8")) <= MAX_REQUEST_ID_BYTES:
                return f"str:{request_id}"
        except UnicodeError:
            pass
    raise ApprovalEventError("invalid_upstream_request_id")


def decode_upstream_request_key(key: str) -> str | int:
    """Recover only canonical typed JSON-RPC identifiers from the journal."""
    if not isinstance(key, str):
        raise ApprovalEventError("invalid_upstream_request_id")
    if key.startswith("str:"):
        value = key[4:]
    elif key.startswith("int:"):
        try:
            value = int(key[4:])
        except ValueError as error:
            raise ApprovalEventError("invalid_upstream_request_id") from error
    else:
        raise ApprovalEventError("invalid_upstream_request_id")
    if upstream_request_key(value) != key:
        raise ApprovalEventError("invalid_upstream_request_id")
    return value


def record_approval_request_event(
        journals: Any, message: dict, *, epoch: int,
        recorded_at: str) -> dict:
    """Persist an identified command/file request before any user decision."""
    if (not isinstance(message, dict) or "id" not in message
            or "result" in message or "error" in message
            or not isinstance(message.get("params"), dict)):
        raise ApprovalEventError("invalid_approval_request_event")
    kind = {
        "item/commandExecution/requestApproval": "command",
        "item/fileChange/requestApproval": "file_change",
    }.get(message.get("method"))
    params = message["params"]
    thread_id, turn_id = params.get("threadId"), params.get("turnId")
    if (kind is None or not isinstance(thread_id, str) or not thread_id
            or not isinstance(turn_id, str) or not turn_id):
        raise ApprovalEventError("invalid_approval_request_event")
    request_key = upstream_request_key(message["id"])
    try:
        request_bytes = json.dumps(
            {"method": message["method"], "id": request_key, "params": params},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ApprovalEventError("invalid_approval_request_event") from error
    request_digest = hashlib.sha256(request_bytes).digest()
    return journals.record_upstream_approval_request(
        thread_id=thread_id, turn_id=turn_id, epoch=epoch,
        upstream_request_id=request_key, kind=kind,
        approval_id="apr_" + secrets.token_urlsafe(18),
        request_digest=request_digest,
        recorded_at=recorded_at)


def record_resolved_notification(
        journals: Any, message: dict, *, epoch: int,
        recorded_at: str) -> dict:
    """Apply one matched request-end fact; no tool effect is inferred."""
    if (not isinstance(message, dict)
            or message.get("method") != "serverRequest/resolved"
            or "id" in message
            or not isinstance(message.get("params"), dict)):
        raise ApprovalEventError("invalid_request_resolved_notification")
    params = message["params"]
    thread_id = params.get("threadId")
    if not isinstance(thread_id, str) or not thread_id:
        raise ApprovalEventError("invalid_request_resolved_notification")
    request_key = upstream_request_key(params.get("requestId"))
    return journals.record_upstream_request_closed(
        thread_id=thread_id, epoch=epoch,
        upstream_request_id=request_key, recorded_at=recorded_at)
