"""Bounded approval identifiers consumed by the Host's stdio owner thread."""

import queue

from runvori.codex.approval_dispatch import ApprovalDispatchError, send_approval_once
from runvori.codex.upstream_lifecycle import (
    OwnedUpstream, isolate_and_recover_upstream,
)
from runvori.infrastructure.event_journal import EventJournalError


class OwnedApprovalQueueError(ValueError):
    """Safe queue or dispatch refusal without approval content."""


class OwnedApprovalQueue:
    """Carries identifiers only; decisions and upstream IDs stay in SQLite."""

    def __init__(self, capacity: int = 64):
        if type(capacity) is not int or capacity < 1:
            raise OwnedApprovalQueueError("invalid_approval_queue_capacity")
        self._items: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=capacity)

    def submit(self, actor_id: str, request_id: str) -> None:
        if (not isinstance(actor_id, str) or not actor_id
                or not isinstance(request_id, str) or not request_id):
            raise OwnedApprovalQueueError("invalid_approval_queue_item")
        try:
            self._items.put_nowait((actor_id, request_id))
        except queue.Full:
            raise OwnedApprovalQueueError("approval_queue_full") from None

    def take_nowait(self) -> tuple[str, str] | None:
        try:
            return self._items.get_nowait()
        except queue.Empty:
            return None


def dispatch_owned_approval_once(owner: OwnedUpstream, *, actor_id: str,
                                 request_id: str, recorded_at: str) -> dict:
    """Send one durable decision; isolate the owned connection on uncertainty."""
    if not isinstance(owner, OwnedUpstream) or owner.client is None:
        raise OwnedApprovalQueueError("unowned_upstream_connection")
    journals = owner.journals
    journals.assert_owned_upstream(owner, epoch=owner.epoch)
    try:
        result = send_approval_once(
            owner.client, journals, actor_id=actor_id, request_id=request_id,
            epoch=owner.epoch, recorded_at=recorded_at)
    except ApprovalDispatchError as error:
        if str(error) in {"dispatch_unavailable", "unmapped_approval_request"}:
            return {"kind": "approval_skipped", "cause": str(error)}
        return _recover(owner, recorded_at)
    except EventJournalError as error:
        if (str(error) in {"dispatch_unavailable", "operation_not_found"}
                and not owner.client._closed):
            return {"kind": "approval_skipped", "cause": str(error)}
        return _recover(owner, recorded_at)
    except Exception:
        return _recover(owner, recorded_at)
    if result["status"] == "unknown":
        return _recover(owner, recorded_at)
    return {"kind": "approval_dispatched"}


def _recover(owner: OwnedUpstream, recorded_at: str) -> dict:
    try:
        recovery = isolate_and_recover_upstream(owner, recorded_at=recorded_at)
    except Exception as error:
        raise OwnedApprovalQueueError("upstream_recovery_failed") from error
    return {"kind": "upstream_recovered", "cause": "approval_dispatch_fault",
            "recovery": recovery}
