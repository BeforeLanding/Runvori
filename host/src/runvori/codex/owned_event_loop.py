"""Serial, bounded-poll event loop for one Host-owned app-server connection."""

import math
import threading
from datetime import datetime, timezone

from runvori.codex.owned_approval_queue import (
    OwnedApprovalQueue, dispatch_owned_approval_once,
)
from runvori.codex.owned_event_pump import pump_owned_event_once
from runvori.codex.owned_thread_start import (
    OwnedThreadStartQueue, start_owned_session_thread_once,
)
from runvori.codex.upstream_lifecycle import OwnedUpstream


class OwnedEventLoopError(ValueError):
    """Safe validation refusal without upstream or database contents."""


def run_owned_event_loop(owner: OwnedUpstream, *, stop: threading.Event,
                         poll_timeout: float = 0.25,
                         max_handoffs: int | None = None,
                         approvals: OwnedApprovalQueue | None = None,
                         thread_starts: OwnedThreadStartQueue | None = None) -> dict:
    """Consume events until stopped, bounded for a caller, or recovered.

    The caller must be the sole stdio consumer. Queued approval and local
    thread-start identifiers are dispatched by this same thread, at most one
    of each per loop iteration.
    A recovery result ends the loop because the old connection no longer
    belongs to the current epoch.
    """
    if (not isinstance(owner, OwnedUpstream)
            or not isinstance(stop, threading.Event)
            or not isinstance(poll_timeout, (int, float))
            or isinstance(poll_timeout, bool)
            or not math.isfinite(poll_timeout) or poll_timeout <= 0
            or (approvals is not None and
                not isinstance(approvals, OwnedApprovalQueue))
            or (thread_starts is not None and
                not isinstance(thread_starts, OwnedThreadStartQueue))
            or (max_handoffs is not None and
                (type(max_handoffs) is not int or max_handoffs < 1))):
        raise OwnedEventLoopError("invalid_event_loop_request")

    owner.journals.assert_owned_upstream(owner, epoch=owner.epoch)
    counts = {"approval_request": 0, "request_closed": 0,
              "turn_terminal": 0, "ignored_notification": 0,
              "approval_dispatched": 0, "approval_skipped": 0,
              "thread_start_confirmed": 0, "thread_start_rejected": 0}
    handoffs = 0
    while not stop.is_set():
        recorded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        local_start = None if thread_starts is None else thread_starts.take_nowait()
        if local_start is not None:
            started = start_owned_session_thread_once(
                owner, session_id=local_start[0], actor_id=local_start[1],
                request_id=local_start[2], recorded_at=recorded_at)
            if started["kind"] == "upstream_recovered":
                return {"reason": "upstream_recovered", "handoffs": handoffs,
                        "counts": counts, "cause": started["cause"],
                        "epoch": started["recovery"]["epoch"]}
            counts[started["kind"]] += 1
        queued = None if approvals is None else approvals.take_nowait()
        if queued is not None:
            dispatched = dispatch_owned_approval_once(
                owner, actor_id=queued[0], request_id=queued[1],
                recorded_at=recorded_at)
            if dispatched["kind"] == "upstream_recovered":
                return {"reason": "upstream_recovered", "handoffs": handoffs,
                        "counts": counts, "cause": dispatched["cause"],
                        "epoch": dispatched["recovery"]["epoch"]}
            counts[dispatched["kind"]] += 1
        result = pump_owned_event_once(
            owner, timeout=poll_timeout, recorded_at=recorded_at)
        if result is None:
            continue
        if result["kind"] == "upstream_recovered":
            return {"reason": "upstream_recovered", "handoffs": handoffs,
                    "counts": counts, "cause": result["cause"],
                    "epoch": result["recovery"]["epoch"]}
        counts[result["kind"]] += 1
        handoffs += 1
        if max_handoffs is not None and handoffs >= max_handoffs:
            return {"reason": "handoff_limit", "handoffs": handoffs,
                    "counts": counts}
    return {"reason": "stopped", "handoffs": handoffs, "counts": counts}
