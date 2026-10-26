"""Bounded run-start signals consumed by the Host stdio owner."""

import queue

from runvori.codex.run_dispatch import RunDispatchError, start_run_once
from runvori.codex.thread_resume import ThreadResumeError
from runvori.codex.upstream_lifecycle import (
    OwnedUpstream, isolate_and_recover_upstream,
)
from runvori.infrastructure.event_journal import (
    EventJournalError, MAX_INPUT_BYTES, MAX_SEQ,
)


class OwnedRunStartError(ValueError):
    """Safe refusal without prompt or upstream content."""


class OwnedRunStartQueue:
    """Carries an original accepted request, never a thread ID or cwd."""

    def __init__(self, capacity: int = 16):
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise OwnedRunStartError("invalid_run_start_queue_capacity")
        self._items: queue.Queue[tuple[str, str, str, str, int, str]] = (
            queue.Queue(maxsize=capacity))

    def submit(self, *, session_id: str, run_id: str, actor_id: str,
               request_id: str, expected_session_version: int,
               input_text: str) -> None:
        if (any(not isinstance(value, str) or not value
                for value in (session_id, run_id, actor_id, request_id))
                or type(expected_session_version) is not int
                or not 0 <= expected_session_version < MAX_SEQ
                or not isinstance(input_text, str) or not input_text):
            raise OwnedRunStartError("invalid_run_start_queue_item")
        try:
            if len(input_text.encode("utf-8")) > MAX_INPUT_BYTES:
                raise OwnedRunStartError("run_input_too_large")
        except UnicodeError as error:
            raise OwnedRunStartError("invalid_run_start_queue_item") from error
        try:
            self._items.put_nowait((
                session_id, run_id, actor_id, request_id,
                expected_session_version, input_text))
        except queue.Full:
            raise OwnedRunStartError("run_start_queue_full") from None

    def take_nowait(self) -> tuple[str, str, str, str, int, str] | None:
        try:
            return self._items.get_nowait()
        except queue.Empty:
            return None


def start_owned_run_once(
        owner: OwnedUpstream, *, session_id: str, run_id: str,
        actor_id: str, request_id: str, expected_session_version: int,
        input_text: str, recorded_at: str) -> dict:
    """Resume the bound thread and start one already accepted run."""
    if not isinstance(owner, OwnedUpstream) or owner.client is None:
        raise OwnedRunStartError("unowned_upstream_connection")
    journals = owner.journals
    journals.assert_owned_upstream(owner, epoch=owner.epoch)
    try:
        result = start_run_once(
            owner.client, journals, session_id=session_id, run_id=run_id,
            actor_id=actor_id, request_id=request_id,
            expected_session_version=expected_session_version,
            input_text=input_text, epoch=owner.epoch, recorded_at=recorded_at)
    except RunDispatchError as error:
        if str(error) == "dispatch_unavailable" and not owner.client._closed:
            return {"kind": "run_start_rejected", "cause": str(error)}
        return _recover(owner, recorded_at)
    except EventJournalError as error:
        if (str(error) in {"idempotency_conflict", "operation_not_found",
                           "session_thread_not_bound"}
                and not owner.client._closed):
            return {"kind": "run_start_rejected", "cause": str(error)}
        return _recover(owner, recorded_at)
    except ThreadResumeError:
        return _recover(owner, recorded_at)
    except Exception:
        return _recover(owner, recorded_at)
    if result["status"] == "unknown" or owner.client._closed:
        return _recover(owner, recorded_at)
    if result["status"] != "confirmed":
        return {"kind": "run_start_rejected", "cause": "dispatch_unavailable"}
    return {"kind": "run_started"}


def _recover(owner: OwnedUpstream, recorded_at: str) -> dict:
    try:
        recovery = isolate_and_recover_upstream(owner, recorded_at=recorded_at)
    except Exception as error:
        raise OwnedRunStartError("upstream_recovery_failed") from error
    return {"kind": "upstream_recovered", "cause": "run_start_fault",
            "recovery": recovery}
