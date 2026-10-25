"""One-shot durable turn/start handoff for an already reserved Host run."""

from typing import Any

from runvori.codex.thread_resume import resume_session_thread


class RunDispatchError(ValueError):
    """A safe refusal without prompt or upstream payload content."""


def start_run_once(
        client: Any, journals: Any, *, session_id: str, run_id: str,
        actor_id: str, request_id: str, expected_session_version: int,
        input_text: str, epoch: int,
        recorded_at: str) -> dict:
    """Verify the accepted request, claim once, and bind a recognized turn."""
    if type(epoch) is not int or epoch < 1:
        raise RunDispatchError("invalid_turn_target")
    if epoch != journals.approvals.current_epoch():
        raise RunDispatchError("stale_epoch")
    operation = journals.events.get_run_operation(actor_id, request_id)
    if operation["status"] != "accepted" or operation["resource_id"] != run_id:
        raise RunDispatchError("dispatch_unavailable")
    accepted = journals.events.reserve_run_request(
        session_id, run_id, actor_id=actor_id, request_id=request_id,
        expected_session_version=expected_session_version,
        input_text=input_text, recorded_at=recorded_at)
    if accepted["status"] != "accepted" or accepted["resource_id"] != run_id:
        raise RunDispatchError("dispatch_unavailable")
    thread_id = resume_session_thread(
        client, journals, session_id=session_id, epoch=epoch)["thread_id"]
    journals.events.claim_run_dispatch(
        actor_id, request_id, process_epoch=epoch, recorded_at=recorded_at)
    try:
        result = client.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": input_text}],
        })
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise RunDispatchError("invalid_turn_start_response")
        return journals.events.confirm_run_started(
            actor_id, request_id, process_epoch=epoch,
            thread_id=thread_id, turn_id=turn_id, recorded_at=recorded_at)
    except Exception:
        close_error = None
        try:
            client.close()
        except Exception as error:
            close_error = error
        unknown = journals.events.mark_run_dispatch_unknown(
            actor_id, request_id, recorded_at=recorded_at)
        if close_error is not None:
            raise RunDispatchError("upstream_close_failed") from close_error
        return unknown
