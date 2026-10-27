"""Serial, bounded-poll event loop for one Host-owned app-server connection."""

import math
import threading
from datetime import datetime, timezone

from runvori.application.command_mailbox import HostCommandMailbox
from runvori.codex.owned_approval_queue import (
    OwnedApprovalQueue, dispatch_owned_approval_once,
)
from runvori.codex.owned_event_pump import pump_owned_event_once
from runvori.codex.owned_interrupt_queue import (
    OwnedInterruptQueue, dispatch_owned_interrupt_once,
)
from runvori.codex.owned_run_start import OwnedRunStartQueue, start_owned_run_once
from runvori.codex.owned_thread_start import (
    OwnedThreadStartQueue, start_owned_session_thread_once,
)
from runvori.codex.upstream_lifecycle import OwnedUpstream


class OwnedEventLoopError(ValueError):
    """Safe validation refusal without upstream or database contents."""


MAX_EVENTS_PER_ITERATION = 64


def run_owned_event_loop(owner: OwnedUpstream, *, stop: threading.Event,
                         poll_timeout: float = 0.25,
                         max_handoffs: int | None = None,
                         approvals: OwnedApprovalQueue | None = None,
                         thread_starts: OwnedThreadStartQueue | None = None,
                         run_starts: OwnedRunStartQueue | None = None,
                         interrupts: OwnedInterruptQueue | None = None,
                         commands: HostCommandMailbox | None = None) -> dict:
    """Release the mailbox on terminal exit, retaining it at a handoff limit."""
    result = None
    try:
        result = _run_owned_event_loop(
            owner, stop=stop, poll_timeout=poll_timeout,
            max_handoffs=max_handoffs, approvals=approvals,
            thread_starts=thread_starts, run_starts=run_starts,
            interrupts=interrupts, commands=commands)
        return result
    finally:
        if (isinstance(commands, HostCommandMailbox)
                and (result is None or result["reason"] != "handoff_limit")):
            commands.close()


def _run_owned_event_loop(owner: OwnedUpstream, *, stop: threading.Event,
                         poll_timeout: float = 0.25,
                         max_handoffs: int | None = None,
                         approvals: OwnedApprovalQueue | None = None,
                         thread_starts: OwnedThreadStartQueue | None = None,
                         run_starts: OwnedRunStartQueue | None = None,
                         interrupts: OwnedInterruptQueue | None = None,
                         commands: HostCommandMailbox | None = None) -> dict:
    """Consume events until stopped, bounded for a caller, or recovered.

    The caller must be the sole stdio consumer. Queued approval, interrupt,
    local thread-start, and accepted run-start signals are dispatched by this
    same thread, at most one of each per loop iteration.
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
            or (run_starts is not None and
                not isinstance(run_starts, OwnedRunStartQueue))
            or (interrupts is not None and
                not isinstance(interrupts, OwnedInterruptQueue))
            or (commands is not None and
                not isinstance(commands, HostCommandMailbox))
            or (max_handoffs is not None and
                (type(max_handoffs) is not int or max_handoffs < 1))):
        raise OwnedEventLoopError("invalid_event_loop_request")

    owner.journals.assert_owned_upstream(owner, epoch=owner.epoch)
    counts = {"approval_request": 0, "request_closed": 0,
              "turn_terminal": 0, "ignored_notification": 0,
              "item_recorded": 0, "approval_details_updated": 0,
              "approval_dispatched": 0, "approval_skipped": 0,
              "thread_start_confirmed": 0, "thread_start_rejected": 0,
              "run_started": 0, "run_start_rejected": 0,
              "interrupt_dispatched": 0, "interrupt_uncertain": 0,
              "interrupt_skipped": 0, "command_served": 0,
              "timeline_recorded": 0, "timeline_ignored": 0}
    handoffs = 0
    while not stop.is_set():
        recorded_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if commands is not None and commands.serve_once(
                owner.journals, recorded_at=recorded_at,
                run_starts=run_starts, interrupts=interrupts,
                approvals=approvals, thread_starts=thread_starts):
            counts["command_served"] += 1
        local_start = None if thread_starts is None else thread_starts.take_nowait()
        if local_start is not None:
            started = start_owned_session_thread_once(
                owner, session_id=local_start[0], actor_id=local_start[1],
                request_id=local_start[2], recorded_at=recorded_at)
            if commands is not None:
                commands.notify_events()
            if started["kind"] == "upstream_recovered":
                return {"reason": "upstream_recovered", "handoffs": handoffs,
                        "counts": counts, "cause": started["cause"],
                        "epoch": started["recovery"]["epoch"]}
            counts[started["kind"]] += 1
        run_start = None if run_starts is None else run_starts.take_nowait()
        if run_start is not None:
            started_run = start_owned_run_once(
                owner, session_id=run_start[0], run_id=run_start[1],
                actor_id=run_start[2], request_id=run_start[3],
                expected_session_version=run_start[4],
                input_text=run_start[5], recorded_at=recorded_at)
            if commands is not None:
                commands.notify_events()
            if started_run["kind"] == "upstream_recovered":
                return {"reason": "upstream_recovered", "handoffs": handoffs,
                        "counts": counts, "cause": started_run["cause"],
                        "epoch": started_run["recovery"]["epoch"]}
            counts[started_run["kind"]] += 1
        interrupt = None if interrupts is None else interrupts.take_nowait()
        if interrupt is not None:
            dispatched_interrupt = dispatch_owned_interrupt_once(
                owner, actor_id=interrupt[0], request_id=interrupt[1],
                recorded_at=recorded_at)
            if commands is not None:
                commands.notify_events()
            if dispatched_interrupt["kind"] == "upstream_recovered":
                return {"reason": "upstream_recovered", "handoffs": handoffs,
                        "counts": counts, "cause": dispatched_interrupt["cause"],
                        "epoch": dispatched_interrupt["recovery"]["epoch"]}
            counts[dispatched_interrupt["kind"]] += 1
        queued = None if approvals is None else approvals.take_nowait()
        if queued is not None:
            dispatched = dispatch_owned_approval_once(
                owner, actor_id=queued[0], request_id=queued[1],
                recorded_at=recorded_at)
            if commands is not None:
                commands.notify_events()
            if dispatched["kind"] == "upstream_recovered":
                return {"reason": "upstream_recovered", "handoffs": handoffs,
                        "counts": counts, "cause": dispatched["cause"],
                        "epoch": dispatched["recovery"]["epoch"]}
            counts[dispatched["kind"]] += 1
        result = pump_owned_event_once(
            owner, timeout=poll_timeout, recorded_at=recorded_at)
        if result is None:
            continue
        if commands is not None:
            commands.notify_events()
        if result["kind"] == "upstream_recovered":
            return {"reason": "upstream_recovered", "handoffs": handoffs,
                    "counts": counts, "cause": result["cause"],
                    "epoch": result["recovery"]["epoch"]}
        counts[result["kind"]] += 1
        handoffs += 1
        if max_handoffs is not None and handoffs >= max_handoffs:
            return {"reason": "handoff_limit", "handoffs": handoffs,
                    "counts": counts}
        if max_handoffs is None:
            # Streamed output arrives far faster than one message per
            # iteration. Drain what is already buffered so the bounded stdio
            # queue cannot overflow, then return to commands and approvals.
            for _ in range(MAX_EVENTS_PER_ITERATION):
                if stop.is_set():
                    break
                drained = pump_owned_event_once(
                    owner, timeout=0.001,
                    recorded_at=datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"))
                if drained is None:
                    break
                if commands is not None:
                    commands.notify_events()
                if drained["kind"] == "upstream_recovered":
                    return {"reason": "upstream_recovered",
                            "handoffs": handoffs, "counts": counts,
                            "cause": drained["cause"],
                            "epoch": drained["recovery"]["epoch"]}
                counts[drained["kind"]] += 1
                handoffs += 1
    return {"reason": "stopped", "handoffs": handoffs, "counts": counts}
