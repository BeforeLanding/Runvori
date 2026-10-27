"""One-shot creation of a Codex thread for a trusted local Host session."""

import hashlib
import json
from pathlib import Path
from typing import Any


class ThreadStartError(ValueError):
    """A safe refusal without workspace or upstream payload content."""


def _local_workspace(workspace: Path) -> str:
    try:
        path = Path(workspace)
    except (TypeError, ValueError) as error:
        raise ThreadStartError("invalid_local_workspace") from error
    if not path.is_absolute() or not path.is_dir():
        raise ThreadStartError("invalid_local_workspace")
    for segment in (path, *path.parents):
        if (segment.is_symlink()
                or (hasattr(segment, "is_junction") and segment.is_junction())):
            raise ThreadStartError("invalid_local_workspace")
    try:
        return str(path.resolve(strict=True))
    except (OSError, RuntimeError) as error:
        raise ThreadStartError("invalid_local_workspace") from error


def create_session_thread_once(
        client: Any, journals: Any, *, session_id: str,
        actor_id: str, request_id: str, epoch: int) -> dict:
    """Persist the create intent and claim one thread/start send opportunity."""
    registration = journals.get_session_workspace(session_id, epoch=epoch)
    cwd = _local_workspace(Path(registration["root"]))
    sandbox = registration["sandbox"]
    params = {"cwd": cwd, "approvalPolicy": "on-request",
              "approvalsReviewer": "user", "sandbox": sandbox,
              "ephemeral": False}
    try:
        encoded = json.dumps(
            {"session_id": session_id, "actor_id": actor_id,
             "request_id": request_id, "params": params},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ThreadStartError("invalid_thread_start_request") from error
    digest = hashlib.sha256(encoded).digest()
    accepted = journals.reserve_thread_start(
        session_id=session_id, actor_id=actor_id,
        request_id=request_id, request_digest=digest, epoch=epoch)
    if accepted["status"] != "accepted":
        return accepted
    journals.claim_thread_start(
        session_id=session_id, actor_id=actor_id,
        request_id=request_id, epoch=epoch)
    try:
        result = client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ThreadStartError("invalid_thread_start_response")
        mark_fresh = getattr(client, "mark_fresh_thread", None)
        if callable(mark_fresh):
            mark_fresh(thread_id)
        return journals.confirm_thread_start(
            session_id=session_id, actor_id=actor_id,
            request_id=request_id, epoch=epoch, thread_id=thread_id)
    except Exception:
        close_error = None
        try:
            client.close()
        except Exception as error:
            close_error = error
        unknown = journals.mark_thread_start_unknown(
            session_id=session_id, actor_id=actor_id,
            request_id=request_id, epoch=epoch)
        if close_error is not None:
            raise ThreadStartError("upstream_close_failed") from close_error
        return unknown
