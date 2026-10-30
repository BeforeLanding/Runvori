"""Bounded handoff of one observed app-server event to the Host journal."""

from typing import Any

from runvori.codex.approval_events import (
    record_approval_request_event, record_resolved_notification,
)
from runvori.codex.item_events import (
    DELTA_METHODS, ITEM_METHODS, record_delta, record_item_event,
)
from runvori.codex.turn_events import record_turn_completed_notification


APPROVAL_METHODS = frozenset({
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
})


class EventHandoffError(ValueError):
    """A safe routing rejection code without upstream payload content."""


def route_observed_event(journals: Any, message: dict, *,
                         epoch: int, recorded_at: str) -> dict:
    """Persist one recognized fact or return an ignored-notification marker."""
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        raise EventHandoffError("invalid_upstream_event")
    method = message["method"]
    if method in APPROVAL_METHODS:
        return {"kind": "approval_request", "record":
                record_approval_request_event(
                    journals, message, epoch=epoch, recorded_at=recorded_at)}
    if method == "serverRequest/resolved":
        return {"kind": "request_closed", "record":
                record_resolved_notification(
                    journals, message, epoch=epoch, recorded_at=recorded_at)}
    if method == "turn/completed":
        return {"kind": "turn_terminal", "record":
                record_turn_completed_notification(
                    journals, message, epoch=epoch, recorded_at=recorded_at)}
    if method in ITEM_METHODS:
        return record_item_event(
            journals, message, epoch=epoch, recorded_at=recorded_at)
    if method in DELTA_METHODS:
        return record_delta(
            journals, message, epoch=epoch, recorded_at=recorded_at)
    if "id" in message:
        raise EventHandoffError("unsupported_server_request")
    if "result" in message or "error" in message:
        raise EventHandoffError("invalid_upstream_event")
    return {"kind": "ignored_notification"}


def process_next_event(client: Any, journals: Any, *, epoch: int,
                       recorded_at: str, timeout: float) -> dict | None:
    """Consume once; close upstream if reading or persistence fails."""
    try:
        message = client.next_event(timeout)
        if message is None:
            return None
        return route_observed_event(
            journals, message, epoch=epoch, recorded_at=recorded_at)
    except Exception:
        client.close()
        raise
