"""Bounded local thread creation signals for the Host stdio owner."""

import queue

from runvori.codex.thread_start import ThreadStartError, create_session_thread_once
from runvori.codex.upstream_lifecycle import (
    OwnedUpstream, isolate_and_recover_upstream,
)
from runvori.infrastructure.event_journal import EventJournalError


class OwnedThreadStartError(ValueError):
    """Safe failure without workspace or upstream payload content."""


class OwnedThreadStartQueue:
    """Local signals only; the durable create intent is recorded on consume."""

    def __init__(self, capacity: int = 16):
        if type(capacity) is not int or capacity < 1:
            raise OwnedThreadStartError("invalid_thread_start_queue_capacity")
        self._items: queue.Queue[tuple[str, str, str]] = queue.Queue(
            maxsize=capacity)

    def submit(self, session_id: str, actor_id: str, request_id: str) -> None:
        if (actor_id != "local"
                or any(not isinstance(value, str) or not value
                       for value in (session_id, actor_id, request_id))):
            raise OwnedThreadStartError("invalid_thread_start_queue_item")
        try:
            self._items.put_nowait((session_id, actor_id, request_id))
        except queue.Full:
            raise OwnedThreadStartError("thread_start_queue_full") from None

    def take_nowait(self) -> tuple[str, str, str] | None:
        try:
            return self._items.get_nowait()
        except queue.Empty:
            return None


def start_owned_session_thread_once(
        owner: OwnedUpstream, *, session_id: str, actor_id: str,
        request_id: str, recorded_at: str) -> dict:
    """Create once on the owner thread and recover a lost upstream result."""
    if not isinstance(owner, OwnedUpstream) or owner.client is None:
        raise OwnedThreadStartError("unowned_upstream_connection")
    if (actor_id != "local" or not isinstance(session_id, str)
            or not session_id or not isinstance(request_id, str)
            or not request_id or not isinstance(recorded_at, str)
            or not recorded_at):
        raise OwnedThreadStartError("invalid_local_thread_start")
    journals = owner.journals
    journals.assert_owned_upstream(owner, epoch=owner.epoch)
    try:
        result = create_session_thread_once(
            owner.client, journals, session_id=session_id,
            actor_id=actor_id, request_id=request_id, epoch=owner.epoch)
    except EventJournalError as error:
        if (str(error) in {"thread_start_conflict", "session_has_runs",
                           "session_thread_already_bound",
                           "session_workspace_not_registered",
                           "session_not_found"}
                and not owner.client._closed):
            return {"kind": "thread_start_rejected", "cause": str(error)}
        return _recover(owner, recorded_at)
    except ThreadStartError as error:
        if str(error) in {"invalid_local_workspace",
                          "invalid_thread_start_request"} and not owner.client._closed:
            return {"kind": "thread_start_rejected", "cause": str(error)}
        return _recover(owner, recorded_at)
    except Exception:
        return _recover(owner, recorded_at)
    if owner.client._closed:
        return _recover(owner, recorded_at)
    if result["status"] == "confirmed":
        return {"kind": "thread_start_confirmed",
                "thread_id": result["thread_id"]}
    if result["status"] == "unknown":
        return {"kind": "thread_start_rejected", "cause": "dispatch_unknown"}
    return {"kind": "thread_start_rejected", "cause": "dispatch_unavailable"}


def _recover(owner: OwnedUpstream, recorded_at: str) -> dict:
    try:
        recovery = isolate_and_recover_upstream(owner, recorded_at=recorded_at)
    except Exception as error:
        raise OwnedThreadStartError("upstream_recovery_failed") from error
    return {"kind": "upstream_recovered", "cause": "thread_start_fault",
            "recovery": recovery}
