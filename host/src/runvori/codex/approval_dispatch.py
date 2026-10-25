"""One-shot handoff from a durable approval decision to app-server stdio."""

from typing import Any

from runvori.codex.approval_events import decode_upstream_request_key


class ApprovalDispatchError(ValueError):
    """A safe refusal without approval content."""


def send_approval_once(
        client: Any, journals: Any, *, actor_id: str, request_id: str,
        epoch: int, recorded_at: str) -> dict:
    """Claim before sending; a returned write is not upstream confirmation."""
    operation = journals.get_approval_operation(actor_id, request_id)
    if operation["status"] != "accepted":
        raise ApprovalDispatchError("dispatch_unavailable")
    approval = journals.approvals.get(operation["resource_id"])
    digest = approval["request_digest"]
    if (not isinstance(digest, bytes) or len(digest) != 32
            or approval["decision"] not in {"accept", "decline"}):
        raise ApprovalDispatchError("unmapped_approval_request")
    upstream_id = decode_upstream_request_key(approval["upstream_request_id"])
    claimed = journals.claim_approval_dispatch(
        actor_id, request_id, epoch=epoch, recorded_at=recorded_at)
    try:
        client.respond_approval(upstream_id, approval["decision"])
    except Exception:
        close_error = None
        try:
            client.close()
        except Exception as error:
            close_error = error
        unknown = journals.mark_approval_dispatch_unknown(
            actor_id, request_id, epoch=epoch, recorded_at=recorded_at)
        if close_error is not None:
            raise ApprovalDispatchError("upstream_close_failed") from close_error
        return unknown
    return claimed
