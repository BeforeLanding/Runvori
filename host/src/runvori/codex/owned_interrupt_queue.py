"""Bounded interrupt identifiers consumed by the Host stdio owner thread."""

import queue

from runvori.codex.interrupt_dispatch import (
    InterruptDispatchError, send_interrupt_once,
)
from runvori.codex.upstream_lifecycle import (
    OwnedUpstream, isolate_and_recover_upstream,
)
from runvori.infrastructure.event_journal import EventJournalError


class OwnedInterruptQueueError(ValueError):
    """Safe queue or dispatch failure without run contents."""


class OwnedInterruptQueue:
    """Carries durable operation keys, never a caller-supplied turn identity."""

    def __init__(self, capacity: int = 64):
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise OwnedInterruptQueueError("invalid_interrupt_queue_capacity")
        self._items: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=capacity)

    def submit(self, actor_id: str, request_id: str) -> None:
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id):
            raise OwnedInterruptQueueError("invalid_interrupt_queue_item")
        try:
            self._items.put_nowait((actor_id, request_id))
        except queue.Full:
            raise OwnedInterruptQueueError("interrupt_queue_full") from None

    def take_nowait(self) -> tuple[str, str] | None:
        try:
            return self._items.get_nowait()
        except queue.Empty:
            return None


def dispatch_owned_interrupt_once(owner: OwnedUpstream, *, actor_id: str,
                                  request_id: str, recorded_at: str) -> dict:
    """Send from the owned connection; recover after a broken transport."""
    if not isinstance(owner, OwnedUpstream) or owner.client is None:
        raise OwnedInterruptQueueError("unowned_upstream_connection")
    journals = owner.journals
    journals.assert_owned_upstream(owner, epoch=owner.epoch)
    try:
        result = send_interrupt_once(
            owner.client, journals, actor_id=actor_id, request_id=request_id,
            epoch=owner.epoch, recorded_at=recorded_at)
    except InterruptDispatchError as error:
        if str(error) == "dispatch_unavailable" and not owner.client._closed:
            return {"kind": "interrupt_skipped", "cause": str(error)}
        return _recover(owner, recorded_at)
    except EventJournalError as error:
        if (str(error) in {"dispatch_unavailable", "operation_not_found",
                           "interrupt_turn_unavailable"}
                and not owner.client._closed):
            return {"kind": "interrupt_skipped", "cause": str(error)}
        return _recover(owner, recorded_at)
    except Exception:
        return _recover(owner, recorded_at)
    if owner.client._closed:
        return _recover(owner, recorded_at)
    if result["status"] == "unknown":
        return {"kind": "interrupt_uncertain"}
    return {"kind": "interrupt_dispatched"}


def _recover(owner: OwnedUpstream, recorded_at: str) -> dict:
    try:
        recovery = isolate_and_recover_upstream(owner, recorded_at=recorded_at)
    except Exception as error:
        raise OwnedInterruptQueueError("upstream_recovery_failed") from error
    return {"kind": "upstream_recovered", "cause": "interrupt_dispatch_fault",
            "recovery": recovery}
