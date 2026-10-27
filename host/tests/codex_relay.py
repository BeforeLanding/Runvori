"""Test-only JSONL relay that can hold one Host/Codex cut deterministically.

The relay forwards bytes unchanged.  A gate forwards the selected message to
the real Codex process, flushes, writes a marker, and only then stops
forwarding Codex-to-Host data.  It is test apparatus, not a product path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


MODES = {
    "passthrough",
    "hold_turn_start",
    "hold_after_approval",
    "hold_after_interrupt",
}
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
}


def mark(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(text, encoding="utf-8")
    os.replace(staging, path)


def request_id(value):
    if type(value) is int or isinstance(value, str):
        return (type(value).__name__, value)
    return None


class Relay:
    def __init__(self, codex: Path, mode: str, marker_dir: Path):
        self.mode = mode
        self.marker_dir = marker_dir
        self.lock = threading.Lock()
        self.frozen = False
        self.triggered = False
        self.pending_approvals: set[tuple[str, object]] = set()
        self.child = subprocess.Popen(
            [str(codex), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)
        mark(marker_dir / "cut.peer", str(os.getpid()))
        mark(marker_dir / "codex.pid", str(self.child.pid))

    def _trigger(self, raw: bytes, codex_input, kind: str,
                 request_key=None) -> None:
        with self.lock:
            if self.triggered:
                return
            codex_input.write(raw)
            codex_input.flush()
            if kind == "approval":
                with open(self.marker_dir / "answers", "a",
                          encoding="utf-8") as answers:
                    answers.write("answer_forwarded\n")
            mark(self.marker_dir / "cut", kind)
            self.frozen = True
            self.triggered = True

    def host_to_codex(self) -> None:
        assert self.child.stdin is not None
        for raw in sys.stdin.buffer:
            message = None
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                pass
            if isinstance(message, dict) and not self.triggered:
                method = message.get("method")
                if self.mode == "hold_turn_start" and method == "turn/start":
                    self._trigger(raw, self.child.stdin, "turn_start")
                    continue
                if (self.mode == "hold_after_interrupt"
                        and method == "turn/interrupt"):
                    self._trigger(raw, self.child.stdin, "interrupt")
                    continue
                if self.mode == "hold_after_approval" and method is None:
                    key = request_id(message.get("id"))
                    with self.lock:
                        pending = key in self.pending_approvals
                        if pending:
                            self.pending_approvals.discard(key)
                    if pending:
                        self._trigger(raw, self.child.stdin, "approval")
                        continue
            with self.lock:
                self.child.stdin.write(raw)
                self.child.stdin.flush()
        try:
            self.child.stdin.close()
        except OSError:
            pass

    def codex_to_host(self) -> None:
        assert self.child.stdout is not None
        for raw in self.child.stdout:
            message = None
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                pass
            if isinstance(message, dict):
                method = message.get("method")
                if method in APPROVAL_METHODS:
                    key = request_id(message.get("id"))
                    if key is not None:
                        with self.lock:
                            self.pending_approvals.add(key)
            with self.lock:
                if self.frozen:
                    continue
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
        try:
            sys.stdout.buffer.flush()
        except OSError:
            pass

    def run(self) -> int:
        reader = threading.Thread(target=self.codex_to_host, daemon=True)
        reader.start()
        self.host_to_codex()
        try:
            return self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
            return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--marker-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        args.marker_dir.mkdir(parents=True, exist_ok=True)
        return Relay(args.codex, args.mode, args.marker_dir).run()
    except Exception as error:
        mark(args.marker_dir / "relay_error", type(error).__name__)
        return 126


if __name__ == "__main__":
    sys.exit(main())
