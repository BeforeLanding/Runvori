"""Bounded, sequential JSONL probe transport; never retries an RPC."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path


class TransportError(RuntimeError):
    """Safe diagnostic: do not include upstream payloads or stderr."""


class RpcError(TransportError):
    pass


class StdioClient:
    """One caller/one outstanding request; errors require closing the connection.

    Windows targets start behind a gate and belong to a kill-on-close job.
    This is process lifecycle containment, not an execution sandbox.
    """

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str],
                 timeout: float = 10, max_line: int = 1_048_576,
                 max_messages: int = 256):
        if timeout <= 0 or max_line < 1 or max_messages < 1:
            raise ValueError("invalid transport limits")
        self.timeout = timeout
        self.max_line = max_line
        self._incoming: queue.Queue = queue.Queue(maxsize=max_messages)
        self.notifications: deque[dict] = deque(maxlen=max_messages)
        self._events: deque[dict] = deque(maxlen=max_messages)
        self._pending_server_requests: dict[str | int, str] = {}
        self._stop = threading.Event()
        self._fatal = threading.Event()
        self._broken = False
        self._next_id = 0
        self._initialized = False
        self._closed = False
        self._job = None
        if os.name == "nt":
            from runvori.codex.windows_job import WindowsJob
            self._job = WindowsJob()
            command = [sys.executable, "-u", str(Path(__file__).with_name("bootstrap.py")), *command]
        try:
            self.process = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **({} if os.name == "nt" else {"start_new_session": True}),
            )
            if self._job is not None:
                self._job.assign(self.process)
                self.process.stdin.write(b"\x00")
                self.process.stdin.flush()
        except BaseException:
            if self._job is not None:
                self._job.close()
            if hasattr(self, "process"):
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=5)
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    stream.close()
            raise
        self._threads = [threading.Thread(target=self._read, daemon=True),
                         threading.Thread(target=self._drain_stderr, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _offer(self, value: object) -> None:
        try:
            self._incoming.put_nowait(value)
        except queue.Full:
            self._fatal.set()

    def _read(self) -> None:
        try:
            while not self._stop.is_set():
                line = self.process.stdout.readline(self.max_line + 1)
                if not line:
                    self._offer(TransportError("upstream_closed"))
                    return
                if len(line) > self.max_line:
                    self._offer(TransportError("message_too_large"))
                    return
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    self._offer(TransportError("invalid_json_message"))
                    return
                self._offer(message)
                if self._fatal.is_set():
                    return
        except (OSError, ValueError):
            if not self._stop.is_set():
                self._offer(TransportError("upstream_read_failed"))

    def _drain_stderr(self) -> None:
        # Discard, rather than retaining potential credentials or personal paths.
        try:
            while self.process.stderr.read(4096):
                if self._stop.is_set():
                    return
        except (OSError, ValueError):
            pass

    def _send(self, message: dict) -> None:
        if self._closed or self._broken:
            raise TransportError("connection_unusable")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        if len(data) > self.max_line:
            raise TransportError("request_too_large")
        done = threading.Event()
        failed = []

        def write() -> None:
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (OSError, ValueError):
                failed.append(True)
            finally:
                done.set()

        writer = threading.Thread(target=write, daemon=True)
        self._threads.append(writer)
        writer.start()
        if not done.wait(self.timeout):
            self._broken = True
            raise TransportError("upstream_write_timeout")
        if failed:
            self._broken = True
            raise TransportError("upstream_write_failed") from None

    def initialize(self) -> dict:
        if self._initialized:
            raise TransportError("already_initialized")
        result = self.request("initialize", {
            "clientInfo": {"name": "runvori_probe", "version": "0.0.1"},
            "capabilities": {"experimentalApi": False},
        })
        self._send({"method": "initialized"})
        self._initialized = True
        return result

    def request(self, method: str, params: dict) -> dict:
        if method != "initialize" and not self._initialized:
            raise TransportError("not_initialized")
        self._next_id += 1
        request_id = self._next_id
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if self._fatal.is_set():
                    raise TransportError("message_queue_overflow")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("rpc_timeout")
                try:
                    message = self._incoming.get(timeout=remaining)
                except queue.Empty:
                    raise TransportError("rpc_timeout") from None
                if isinstance(message, Exception):
                    raise message
                if "method" in message:
                    if "id" in message:
                        self._record_server_request(message)
                        continue
                    self._record_notification(message)
                    continue
                if type(message.get("id")) is not int or message["id"] != request_id:
                    raise TransportError("unexpected_response_id")
                if ("error" in message) == ("result" in message):
                    raise TransportError("invalid_rpc_response")
                if "error" in message:
                    raise RpcError("upstream_rpc_error")
                if not isinstance(message["result"], dict):
                    raise TransportError("invalid_rpc_result")
                return message["result"]
        except TransportError:
            self._broken = True
            raise

    def _record_server_request(self, message: dict) -> None:
        if message.get("method") not in (
            "item/commandExecution/requestApproval", "item/fileChange/requestApproval"
        ):
            raise TransportError("unsupported_server_request")
        request_id = message.get("id")
        if (type(request_id) not in (str, int) or request_id in self._pending_server_requests
                or not isinstance(message.get("params"), dict)):
            raise TransportError("invalid_server_request")
        if len(self._pending_server_requests) == self._incoming.maxsize:
            raise TransportError("server_request_buffer_overflow")
        if len(self._events) == self._events.maxlen:
            raise TransportError("event_buffer_overflow")
        self._pending_server_requests[request_id] = message["method"]
        self._events.append(message)

    def _record_notification(self, message: dict) -> None:
        if message.get("method") == "serverRequest/resolved":
            params = message.get("params")
            if not isinstance(params, dict) or type(params.get("requestId")) not in (str, int):
                raise TransportError("invalid_request_resolved_notification")
            # Request cleanup revokes the opportunity to reply; it does not
            # prove that any prior decision took effect or a command succeeded.
            self._pending_server_requests.pop(params["requestId"], None)
        if len(self.notifications) == self.notifications.maxlen:
            raise TransportError("notification_buffer_overflow")
        if len(self._events) == self._events.maxlen:
            raise TransportError("event_buffer_overflow")
        self.notifications.append(message)
        self._events.append(message)

    def next_event(self, timeout: float) -> dict | None:
        """Read a notification or approval request; None means no event yet."""
        if self._closed or self._broken or not self._initialized:
            raise TransportError("connection_unusable")
        if timeout <= 0:
            raise ValueError("invalid_event_timeout")
        if self._fatal.is_set():
            self._broken = True
            raise TransportError("message_queue_overflow")
        if self._events:
            return self._take_event()
        try:
            message = self._incoming.get(timeout=timeout)
            if self._fatal.is_set():
                raise TransportError("message_queue_overflow")
            if isinstance(message, Exception):
                raise message
            if "method" not in message:
                raise TransportError("unexpected_response_id")
            if "id" in message:
                self._record_server_request(message)
            else:
                self._record_notification(message)
            return self._take_event()
        except queue.Empty:
            return None
        except TransportError:
            self._broken = True
            raise

    def _take_event(self) -> dict:
        event = self._events.popleft()
        if "id" not in event and self.notifications and self.notifications[0] is event:
            self.notifications.popleft()
        return event

    def respond_approval(self, request_id: str | int, decision: str) -> None:
        """Explicit one-shot answer for a recorded approval; never auto-approves."""
        if self._closed or self._broken or not self._initialized:
            raise TransportError("connection_unusable")
        if decision not in ("accept", "decline"):
            raise ValueError("unsupported_approval_decision")
        if type(request_id) not in (str, int) or request_id not in self._pending_server_requests:
            raise TransportError("approval_request_not_pending")
        # Consume before writing: a timeout leaves the outcome unknown, not retryable.
        del self._pending_server_requests[request_id]
        self._send({"id": request_id, "result": {"decision": decision}})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        # EOF gives cooperative peers a chance to clean up their own children.
        # A blocked writer owns the stdin lock; kill first in that case.
        if not any(t.is_alive() for t in self._threads[2:]):
            try:
                self.process.stdin.close()
                self.process.wait(timeout=0.5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        if self._job is not None:
            # Close even when the launcher/target already exited: descendants
            # may still own stdout/stderr. Do not rediscover them by PID.
            self._job.close()
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        for thread in self._threads:
            thread.join(timeout=1)
        # A descendant holding inherited pipes can outlive the parent. Do not
        # block closing a buffered stream while its reader owns the lock.
        if any(t.is_alive() for t in self._threads):
            raise TransportError("pipe_cleanup_incomplete")
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def __enter__(self) -> StdioClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
