"""Verify one persisted session/thread binding against app-server resume."""

from typing import Any


class ThreadResumeError(ValueError):
    """A safe failure code without upstream thread contents."""


def resume_session_thread(
        client: Any, journals: Any, *, session_id: str, epoch: int) -> dict:
    """Resume a known thread once after Host recovery; never replace it."""
    thread_id = journals.get_bound_session_thread(session_id, epoch=epoch)
    is_fresh = getattr(client, "is_fresh_thread", None)
    if callable(is_fresh) and is_fresh(thread_id):
        # The current app-server created this thread in this connection. A
        # brand-new thread has no rollout yet, so resume would fail before the
        # first turn can start.
        return {"session_id": session_id, "thread_id": thread_id,
                "epoch": epoch}
    try:
        result = client.request("thread/resume", {"threadId": thread_id})
        thread = result.get("thread") if isinstance(result, dict) else None
        resumed_id = thread.get("id") if isinstance(thread, dict) else None
        if resumed_id != thread_id:
            raise ThreadResumeError("thread_resume_identity_mismatch")
    except Exception as error:
        try:
            client.close()
        except Exception as close_error:
            raise ThreadResumeError("upstream_close_failed") from close_error
        raise ThreadResumeError("thread_resume_unconfirmed") from error
    return {"session_id": session_id, "thread_id": thread_id, "epoch": epoch}
