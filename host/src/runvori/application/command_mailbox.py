"""Bounded cross-thread commands executed by the Host SQLite owner."""

from __future__ import annotations

import queue
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from runvori.codex.owned_interrupt_queue import (
    OwnedInterruptQueue, OwnedInterruptQueueError,
)
from runvori.codex.owned_run_start import OwnedRunStartError, OwnedRunStartQueue
from runvori.infrastructure.event_journal import EventJournalError


class CommandMailboxError(ValueError):
    """Safe command refusal without input or database contents."""


@dataclass
class _Command:
    kind: str
    values: dict[str, Any]
    cancelled: threading.Event | None = None
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: BaseException | None = None


class HostCommandMailbox:
    """Accept external commands without transferring SQLite ownership."""

    def __init__(self, capacity: int = 64):
        if type(capacity) is not int or not 1 <= capacity <= 256:
            raise CommandMailboxError("invalid_mailbox_capacity")
        self._items: queue.Queue[_Command] = queue.Queue(maxsize=capacity)
        self._events_changed = threading.Condition()
        self._event_generation = 0
        self._closed = threading.Event()

    def close(self) -> None:
        """Stop accepting work and wake readers when the owner exits."""
        self._closed.set()
        self.notify_events()

    def notify_events(self) -> None:
        """Wake readers after a committed state change; readers still query."""
        with self._events_changed:
            self._event_generation += 1
            self._events_changed.notify_all()

    def cancel_wait(self, cancelled: threading.Event) -> None:
        """Wake an in-flight read after its trusted caller revokes access."""
        if not isinstance(cancelled, threading.Event):
            raise CommandMailboxError("invalid_wait_cancellation")
        cancelled.set()
        self.notify_events()

    def start_run(self, *, session_id: str, actor_id: str, request_id: str,
                  expected_session_version: int, input_text: str,
                  timeout: float = 5) -> dict:
        return self._submit("start_run", {
            "session_id": session_id, "actor_id": actor_id,
            "request_id": request_id,
            "expected_session_version": expected_session_version,
            "input_text": input_text}, timeout)

    def interrupt_run(self, *, run_id: str, actor_id: str, request_id: str,
                      expected_version: int, timeout: float = 5) -> dict:
        return self._submit("interrupt_run", {
            "run_id": run_id, "actor_id": actor_id,
            "request_id": request_id, "expected_version": expected_version},
            timeout)

    def get_operation(self, *, kind: str, actor_id: str, request_id: str,
                      timeout: float = 5) -> dict:
        if kind not in {"run", "interrupt"}:
            raise CommandMailboxError("invalid_operation_kind")
        return self._submit("get_operation", {
            "kind": kind, "actor_id": actor_id,
            "request_id": request_id}, timeout)

    def get_session_snapshot(self, *, session_id: str,
                             timeout: float = 5) -> dict:
        return self._submit("get_session_snapshot", {
            "session_id": session_id}, timeout)

    def get_host_identity(self, *, timeout: float = 5) -> dict:
        return self._submit("get_host_identity", {}, timeout)

    def get_session_view(self, *, session_id: str, limit: int = 50,
                         timeout: float = 5,
                         cancelled: threading.Event | None = None) -> dict:
        return self._submit("get_session_view", {
            "session_id": session_id, "limit": limit}, timeout,
            cancelled=cancelled)

    def get_session_events(self, *, session_id: str,
                           after_seq: int | None = None,
                           before_seq: int | None = None,
                           limit: int = 50, timeout: float = 5,
                           cancelled: threading.Event | None = None) -> dict:
        return self._submit("get_session_events", {
            "session_id": session_id, "after_seq": after_seq,
            "before_seq": before_seq, "limit": limit}, timeout,
            cancelled=cancelled)

    def wait_session_events(self, *, session_id: str,
                            after_seq: int = 0, limit: int = 50,
                            wait_ms: int = 0,
                            cancelled: threading.Event | None = None) -> dict:
        """Wait on the caller thread; each SQLite read stays with the owner."""
        if type(wait_ms) is not int or not 0 <= wait_ms <= 25000:
            raise CommandMailboxError("invalid_wait_ms")
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise CommandMailboxError("invalid_wait_cancellation")

        def require_active() -> None:
            if self._closed.is_set():
                raise CommandMailboxError("host_stopped")
            if cancelled is not None and cancelled.is_set():
                raise CommandMailboxError("request_cancelled")

        require_active()
        if wait_ms == 0:
            result = self.get_session_events(
                session_id=session_id, after_seq=after_seq, limit=limit,
                cancelled=cancelled)
            require_active()
            return result
        deadline = time.monotonic() + wait_ms / 1000
        with self._events_changed:
            observed = self._event_generation
        while True:
            require_active()
            remaining = max(0.0, deadline - time.monotonic())
            result = self.get_session_events(
                session_id=session_id, after_seq=after_seq, limit=limit,
                timeout=min(5.0, max(1.0, remaining + 1.0)),
                cancelled=cancelled)
            require_active()
            if result["events"] or time.monotonic() >= deadline:
                return result
            with self._events_changed:
                if self._event_generation == observed:
                    self._events_changed.wait(timeout=max(
                        0.0, min(0.25, deadline - time.monotonic())))
                observed = self._event_generation
            require_active()

    def _submit(self, kind: str, values: dict, timeout: float, *,
                cancelled: threading.Event | None = None) -> dict:
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or not 0 < timeout <= 30):
            raise CommandMailboxError("invalid_command_timeout")
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise CommandMailboxError("invalid_wait_cancellation")
        if cancelled is not None and cancelled.is_set():
            raise CommandMailboxError("request_cancelled")
        if self._closed.is_set():
            raise CommandMailboxError("host_stopped")
        command = _Command(kind, values, cancelled)
        try:
            self._items.put_nowait(command)
        except queue.Full:
            raise CommandMailboxError("command_mailbox_full") from None
        deadline = time.monotonic() + timeout
        while True:
            if self._closed.is_set():
                if kind in {"start_run", "interrupt_run"}:
                    raise CommandMailboxError("command_result_unknown")
                raise CommandMailboxError("host_stopped")
            if cancelled is not None and cancelled.is_set():
                raise CommandMailboxError("request_cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # The owner may still commit later. A caller must query the key.
                raise CommandMailboxError("command_result_unknown")
            if command.done.wait(min(remaining, 0.05)):
                break
        if self._closed.is_set():
            if kind in {"start_run", "interrupt_run"}:
                raise CommandMailboxError("command_result_unknown")
            raise CommandMailboxError("host_stopped")
        if cancelled is not None and cancelled.is_set():
            raise CommandMailboxError("request_cancelled")
        if command.error is not None:
            raise command.error
        if command.result is None:
            raise CommandMailboxError("command_result_unknown")
        return command.result

    def serve_once(self, journals, *, recorded_at: str,
                   run_starts: OwnedRunStartQueue | None,
                   interrupts: OwnedInterruptQueue | None) -> bool:
        """Execute at most one request on the caller's journal-owner thread."""
        try:
            command = self._items.get_nowait()
        except queue.Empty:
            return False
        try:
            if self._closed.is_set():
                raise CommandMailboxError("host_stopped")
            if command.cancelled is not None and command.cancelled.is_set():
                raise CommandMailboxError("request_cancelled")
            command.result = self._execute(
                command, journals, recorded_at=recorded_at,
                run_starts=run_starts, interrupts=interrupts)
            if command.cancelled is not None and command.cancelled.is_set():
                command.result = None
                raise CommandMailboxError("request_cancelled")
        except Exception as error:
            command.error = (error if isinstance(
                error, (CommandMailboxError, EventJournalError))
                else CommandMailboxError("command_result_unknown"))
        finally:
            command.done.set()
        return True

    def _execute(self, command: _Command, journals, *, recorded_at: str,
                 run_starts: OwnedRunStartQueue | None,
                 interrupts: OwnedInterruptQueue | None) -> dict:
        values = command.values
        if command.kind == "get_host_identity":
            return {"host_id": journals.events.get_host_id()}
        if command.kind == "get_operation":
            getter = (journals.events.get_run_operation if values["kind"] == "run"
                      else journals.events.get_interrupt_operation)
            return getter(values["actor_id"], values["request_id"])
        if command.kind == "get_session_snapshot":
            return journals.events.snapshot(values["session_id"])
        if command.kind == "get_session_view":
            return journals.events.snapshot_tail(
                values["session_id"], limit=values["limit"])
        if command.kind == "get_session_events":
            return journals.events.page(
                values["session_id"], after_seq=values["after_seq"],
                before_seq=values["before_seq"], limit=values["limit"])
        if command.kind == "start_run":
            if run_starts is None:
                raise CommandMailboxError("execution_unavailable")
            run_id = secrets.token_urlsafe(24)
            result = journals.events.reserve_run_request(
                values["session_id"], run_id,
                actor_id=values["actor_id"], request_id=values["request_id"],
                expected_session_version=values["expected_session_version"],
                input_text=values["input_text"], recorded_at=recorded_at)
            self.notify_events()
            if result["status"] == "accepted":
                try:
                    run_starts.submit(
                        session_id=values["session_id"],
                        run_id=result["resource_id"],
                        actor_id=values["actor_id"],
                        request_id=values["request_id"],
                        expected_session_version=values["expected_session_version"],
                        input_text=values["input_text"])
                except OwnedRunStartError as error:
                    # The commit survived; the original key can be queried and
                    # explicitly retried without creating a second operation.
                    raise CommandMailboxError(
                        "dispatch_signal_unavailable") from error
            return result
        if command.kind == "interrupt_run":
            if interrupts is None:
                raise CommandMailboxError("execution_unavailable")
            result = journals.events.reserve_interrupt_request(
                values["run_id"], actor_id=values["actor_id"],
                request_id=values["request_id"],
                expected_version=values["expected_version"],
                recorded_at=recorded_at)
            self.notify_events()
            if result["status"] == "accepted":
                try:
                    interrupts.submit(values["actor_id"], values["request_id"])
                except OwnedInterruptQueueError as error:
                    raise CommandMailboxError(
                        "dispatch_signal_unavailable") from error
            return result
        raise CommandMailboxError("invalid_command")
