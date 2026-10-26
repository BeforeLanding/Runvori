"""One-shot durable handoff of a run interrupt to app-server stdio."""

from typing import Any

from runvori.codex.stdio import RpcError


class InterruptDispatchError(ValueError):
    """Safe refusal without upstream response or run content."""


def send_interrupt_once(client: Any, journals: Any, *, actor_id: str,
                        request_id: str, epoch: int, recorded_at: str) -> dict:
    """Claim before sending; only a turn terminal confirms the final state."""
    operation = journals.events.get_interrupt_operation(actor_id, request_id)
    if operation["status"] != "accepted":
        raise InterruptDispatchError("dispatch_unavailable")
    target = journals.get_bound_interrupt_turn(
        operation["resource_id"], epoch=epoch)
    claimed = journals.events.claim_interrupt_dispatch(
        actor_id, request_id, process_epoch=epoch, recorded_at=recorded_at)
    try:
        client.request("turn/interrupt", {
            "threadId": target["thread_id"], "turnId": target["turn_id"]})
    except RpcError:
        # A matched RPC error keeps the stream usable. The terminal may already
        # be buffered, so retain uncertainty and let the event pump observe it.
        return journals.events.mark_interrupt_dispatch_unknown(
            actor_id, request_id, process_epoch=epoch,
            recorded_at=recorded_at)
    except Exception:
        close_error = None
        try:
            client.close()
        except Exception as error:
            close_error = error
        unknown = journals.events.mark_interrupt_dispatch_unknown(
            actor_id, request_id, process_epoch=epoch,
            recorded_at=recorded_at)
        if close_error is not None:
            raise InterruptDispatchError("upstream_close_failed") from close_error
        return unknown
    return claimed
