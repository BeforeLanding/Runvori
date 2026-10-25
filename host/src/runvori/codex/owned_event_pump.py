"""One bounded event handoff on the Host-owned app-server connection."""

import math

from runvori.codex.event_handoff import route_observed_event
from runvori.codex.stdio import TransportError
from runvori.codex.upstream_lifecycle import (
    OwnedUpstream, isolate_and_recover_upstream,
)


class OwnedEventPumpError(RuntimeError):
    """Safe failure code without upstream message or storage contents."""


def pump_owned_event_once(owner: OwnedUpstream, *, timeout: float,
                          recorded_at: str) -> dict | None:
    """Persist one event; isolate and recover if observation fails."""
    if not isinstance(owner, OwnedUpstream) or owner.client is None:
        raise OwnedEventPumpError("unowned_upstream_connection")
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or not math.isfinite(timeout) or timeout <= 0
            or not isinstance(recorded_at, str) or not recorded_at):
        raise OwnedEventPumpError("invalid_event_pump_request")
    journals = owner.journals
    journals.assert_owned_upstream(owner, epoch=owner.epoch)
    try:
        message = owner.client.next_event(timeout)
        if message is None:
            return None
        return route_observed_event(
            journals, message, epoch=owner.epoch, recorded_at=recorded_at)
    except Exception as error:
        cause = "transport_fault" if isinstance(error, TransportError) \
            else "handoff_fault"
        try:
            recovery = isolate_and_recover_upstream(
                owner, recorded_at=recorded_at)
        except Exception as recovery_error:
            raise OwnedEventPumpError("upstream_recovery_failed") from recovery_error
        return {"kind": "upstream_recovered", "cause": cause,
                "recovery": recovery}
