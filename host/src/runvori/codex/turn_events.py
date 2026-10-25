"""Map an observed app-server turn terminal to a bound Host run."""

from typing import Any


class TurnEventError(ValueError):
    """A safe rejection code without upstream payload content."""


def record_turn_completed_notification(
        journals: Any, message: dict, *, epoch: int,
        recorded_at: str) -> dict:
    """Persist the turn's reported terminal status, not a tool outcome."""
    if (not isinstance(message, dict)
            or message.get("method") != "turn/completed"
            or "id" in message or "result" in message or "error" in message
            or not isinstance(message.get("params"), dict)):
        raise TurnEventError("invalid_turn_completed_notification")
    params = message["params"]
    turn = params.get("turn")
    thread_id = params.get("threadId")
    status = turn.get("status") if isinstance(turn, dict) else None
    if (not isinstance(thread_id, str) or not thread_id
            or not isinstance(turn, dict)
            or not isinstance(turn.get("id"), str) or not turn["id"]
            or not isinstance(status, str)
            or status not in {"completed", "failed", "interrupted"}):
        raise TurnEventError("invalid_turn_completed_notification")
    return journals.record_upstream_turn_terminal(
        thread_id=thread_id, turn_id=turn["id"], epoch=epoch,
        terminal=status, recorded_at=recorded_at)
