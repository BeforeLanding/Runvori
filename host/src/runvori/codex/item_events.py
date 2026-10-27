"""Project observed item lifecycle notifications into the visible timeline.

Only the display-bearing item families are projected: agent messages and
command executions (plus the existing fileChange review fold). The projection
never guesses an owner: every write is located through the persisted
``(thread_id, turn_id)`` binding, so a caller-supplied identifier cannot
attach output to someone else's run.
"""

from __future__ import annotations

from typing import Any

from runvori.codex import approval_details
from runvori.infrastructure.event_journal import (
    MAX_COMMAND_TEXT_BYTES, MAX_TIMELINE_CHUNK_BYTES, EventJournalError,
)


ITEM_METHODS = frozenset({"item/started", "item/completed"})
DELTA_METHODS = {
    "item/agentMessage/delta": "message.delta",
    "item/commandExecution/outputDelta": "command.output",
}
TEXT_STREAMS = frozenset({"message.delta", "command.output"})
# Losing one display event must not tear down the upstream connection; the
# turn terminal is still authoritative. Storage faults still propagate.
TOLERATED = frozenset({
    "upstream_turn_not_found", "run_not_active", "session_mismatch",
    "timeline_chunk_too_large", "invalid_timeline_event",
})


class ItemEventError(ValueError):
    """A safe rejection code without item content."""


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value.encode("utf-8")) > 256:
            return None
    except UnicodeError:
        return None
    return value


def _bounded_text(value: Any, limit: int) -> tuple[str | None, bool]:
    if not isinstance(value, str) or not value:
        return None, False
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", "ignore"), True


def chunk_text(text: str, limit: int = MAX_TIMELINE_CHUNK_BYTES) -> list[str]:
    """Split at character boundaries so no chunk breaks a UTF-8 sequence."""
    if not isinstance(text, str) or type(limit) is not int or limit < 1:
        raise ItemEventError("invalid_timeline_text")
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for character in text:
        size = len(character.encode("utf-8"))
        if used + size > limit and current:
            chunks.append("".join(current))
            current = []
            used = 0
        current.append(character)
        used += size
    if current:
        chunks.append("".join(current))
    return chunks


def _identity(params: dict) -> tuple[str, str]:
    thread_id = params.get("threadId")
    turn_id = params.get("turnId")
    if (not isinstance(thread_id, str) or not thread_id
            or not isinstance(turn_id, str) or not turn_id):
        raise ItemEventError("invalid_item_notification")
    return thread_id, turn_id


def record_delta(journals: Any, message: dict, *, recorded_at: str) -> dict:
    """Store one streamed text delta as its own bounded timeline event."""
    if (not isinstance(message, dict)
            or message.get("method") not in DELTA_METHODS
            or "id" in message
            or not isinstance(message.get("params"), dict)):
        raise ItemEventError("invalid_delta_notification")
    params = message["params"]
    thread_id, turn_id = _identity(params)
    item_id = _identifier(params.get("itemId"))
    delta = params.get("delta")
    if item_id is None or not isinstance(delta, str) or not delta:
        raise ItemEventError("invalid_delta_notification")
    try:
        event = journals.events.append_timeline_bound(
            None, thread_id=thread_id, turn_id=turn_id,
            kind=DELTA_METHODS[message["method"]],
            payload={"item_id": item_id}, text=delta, recorded_at=recorded_at)
    except EventJournalError as error:
        if str(error) in TOLERATED:
            return {"kind": "timeline_ignored", "reason": str(error)}
        raise
    if isinstance(event, dict) and event.get("type"):
        return {"kind": "timeline_recorded", "event": event}
    return {"kind": "timeline_ignored", "reason": "unbound"}


def record_item_event(journals: Any, message: dict, *,
                      recorded_at: str) -> dict:
    """Store one item summary, fold file approvals and project the timeline."""
    if (not isinstance(message, dict)
            or message.get("method") not in ITEM_METHODS
            or "id" in message
            or not isinstance(message.get("params"), dict)):
        raise ItemEventError("invalid_item_notification")
    params = message["params"]
    item = params.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
        raise ItemEventError("invalid_item_notification")
    if item["type"] not in {"fileChange", "commandExecution", "agentMessage"}:
        # Other item kinds carry nothing this slice displays.
        return {"kind": "ignored_notification"}
    thread_id, turn_id = _identity(params)
    started = message["method"] == "item/started"
    if item["type"] == "fileChange":
        try:
            summary = approval_details.item_changes(item)
        except approval_details.ApprovalDetailError as error:
            raise ItemEventError(str(error)) from error
        return journals.record_approval_item(
            thread_id=thread_id, turn_id=turn_id, item_id=summary["item_id"],
            summary=summary, recorded_at=recorded_at)
    item_id = _identifier(item.get("id"))
    if item_id is None:
        raise ItemEventError("invalid_item_notification")
    if item["type"] == "commandExecution":
        return _command_item(journals, thread_id, turn_id, item_id, item,
                             started=started, params=params,
                             recorded_at=recorded_at)
    return _message_item(journals, thread_id, turn_id, item_id, item,
                         started=started, recorded_at=recorded_at)


def _append(journals: Any, thread_id: str, turn_id: str, *, kind: str,
            payload: dict, text: str | None, recorded_at: str) -> dict | None:
    """Append one timeline event; a tolerated refusal returns None."""
    try:
        event = journals.events.append_timeline_bound(
            None, thread_id=thread_id, turn_id=turn_id, kind=kind,
            payload=payload, text=text, recorded_at=recorded_at)
    except EventJournalError as error:
        if str(error) in TOLERATED:
            return None
        raise
    return event if isinstance(event, dict) and event.get("type") else None


def _command_item(journals: Any, thread_id: str, turn_id: str, item_id: str,
                  item: dict, *, started: bool, params: dict,
                  recorded_at: str) -> dict:
    started_at_ms = params.get("startedAtMs")
    if type(started_at_ms) is not int or started_at_ms < 0:
        started_at_ms = None
    if started:
        command, truncated = _bounded_text(item.get("command"),
                                           MAX_COMMAND_TEXT_BYTES)
        cwd = item.get("cwd") if isinstance(item.get("cwd"), str) else None
        status = item.get("status")
        event = _append(journals, thread_id, turn_id, kind="command.started",
                        payload={"item_id": item_id, "command": command,
                                 "command_truncated": truncated, "cwd": cwd,
                                 "status": status if isinstance(status, str)
                                 else None,
                                 "started_at_ms": started_at_ms},
                        text=None, recorded_at=recorded_at)
        return _recorded(event)
    exit_code = item.get("exitCode")
    duration = item.get("durationMs")
    status = item.get("status")
    completed_at_ms = params.get("completedAtMs")
    if type(completed_at_ms) is not int or completed_at_ms < 0:
        completed_at_ms = None
    command, truncated = _bounded_text(item.get("command"),
                                       MAX_COMMAND_TEXT_BYTES)
    cwd = item.get("cwd") if isinstance(item.get("cwd"), str) else None
    streamed = journals.events.item_has_text(
        thread_id=thread_id, turn_id=turn_id, item_id=item_id)
    output = item.get("aggregatedOutput")
    written = 0
    # Write aggregated output before the completion event so the command
    # evidence row can decide "complete/truncated/empty" in one transaction.
    if not streamed and isinstance(output, str) and output:
        for chunk in chunk_text(output):
            if _append(journals, thread_id, turn_id, kind="command.output",
                       payload={"item_id": item_id}, text=chunk,
                       recorded_at=recorded_at) is not None:
                written += len(chunk.encode("utf-8"))
    payload = {
        "item_id": item_id,
        "command": command,
        "command_truncated": truncated,
        "cwd": cwd,
        "status": status if isinstance(status, str) else None,
        "exit_code": exit_code if type(exit_code) is int else None,
        "duration_ms": duration if type(duration) is int else None,
        "started_at_ms": started_at_ms,
        "completed_at_ms": completed_at_ms,
        "output_omitted": not streamed and not isinstance(output, str),
    }
    event = _append(journals, thread_id, turn_id, kind="command.completed",
                    payload=payload, text=None, recorded_at=recorded_at)
    return {**_recorded(event), "output_bytes": written}


def _message_item(journals: Any, thread_id: str, turn_id: str, item_id: str,
                  item: dict, *, started: bool, recorded_at: str) -> dict:
    if started:
        event = _append(journals, thread_id, turn_id, kind="message.started",
                        payload={"item_id": item_id}, text=None,
                        recorded_at=recorded_at)
        return _recorded(event)
    streamed = journals.events.item_has_text(
        thread_id=thread_id, turn_id=turn_id, item_id=item_id)
    event = _append(journals, thread_id, turn_id, kind="message.completed",
                    payload={"item_id": item_id, "text_omitted": not streamed},
                    text=None, recorded_at=recorded_at)
    text = item.get("text")
    written = 0
    if not streamed and isinstance(text, str) and text:
        for chunk in chunk_text(text):
            if _append(journals, thread_id, turn_id, kind="message.delta",
                       payload={"item_id": item_id}, text=chunk,
                       recorded_at=recorded_at) is not None:
                written += len(chunk.encode("utf-8"))
    return {**_recorded(event), "text_bytes": written}


def _recorded(event: dict | None) -> dict:
    if event is None:
        return {"kind": "timeline_ignored", "reason": "unbound_or_inactive"}
    return {"kind": "timeline_recorded", "event": event}