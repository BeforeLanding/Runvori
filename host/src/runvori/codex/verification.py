"""Conservative evidence for one pre-registered required command."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping


VERIFICATION_PRIORITY = ("failed", "running", "unknown", "not_run", "pending", "passed")


class RequiredCommandEvidence:
    def __init__(self, *, thread_id: str, turn_id: str, cwd: Path,
                 matches_command: Callable[[object], bool]):
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.cwd = cwd.resolve()
        self.matches_command = matches_command
        self.started: dict[str, int] = {}
        self.completed: dict[str, tuple[int | None, int]] = {}
        self.approved_ids: set[str] = set()
        self.ambiguous = False

    def note_approved(self, item_id: str) -> None:
        self.approved_ids.add(item_id)

    def observe(self, method: str, params: object) -> None:
        if method not in ("item/started", "item/completed") or not isinstance(params, dict):
            return
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") != "commandExecution":
            return
        observed_id = item.get("id")
        if not self.matches_command(item.get("command")):
            if isinstance(observed_id, str) and (observed_id in self.started
                                                 or observed_id in self.completed):
                self.ambiguous = True
            return
        cwd = item.get("cwd")
        if not isinstance(cwd, str):
            self.ambiguous = True
            return
        try:
            cwd_matches = Path(cwd).resolve() == self.cwd
        except (OSError, ValueError):
            self.ambiguous = True
            return
        if not cwd_matches:
            if isinstance(observed_id, str) and (observed_id in self.started
                                                 or observed_id in self.completed):
                self.ambiguous = True
            return
        item_id = observed_id
        if (params.get("threadId") != self.thread_id
                or params.get("turnId") != self.turn_id
                or not isinstance(item_id, str) or not item_id):
            self.ambiguous = True
            return
        if method == "item/started":
            started = params.get("startedAtMs")
            if item_id in self.started or type(started) is not int or started < 0:
                self.ambiguous = True
            else:
                self.started[item_id] = started
        else:
            completed = params.get("completedAtMs")
            exit_code = item.get("exitCode")
            completed_status = item.get("status")
            if (item_id not in self.started or item_id in self.completed
                    or type(completed) is not int
                    or completed < 0 or type(exit_code) is not int
                    or completed_status not in ("completed", "failed")
                    or (completed_status == "failed" and exit_code == 0)):
                self.ambiguous = True
            else:
                self.completed[item_id] = (exit_code, completed)

    def status(self, turn_status: str) -> str:
        if self.ambiguous or len(self.started) > 1 or len(self.completed) > 1:
            return "unknown"
        if not self.started and not self.completed:
            if self.approved_ids:
                return "unknown"
            return "not_run" if turn_status in ("completed", "failed", "interrupted") else "pending"
        if not self.completed:
            return "unknown" if turn_status in ("completed", "failed", "interrupted") else "running"
        if len(self.started) != 1 or self.started.keys() != self.completed.keys():
            return "unknown"
        if self.approved_ids and self.approved_ids != self.started.keys():
            return "unknown"
        item_id = next(iter(self.started))
        exit_code, completed_at = self.completed[item_id]
        if completed_at < self.started[item_id]:
            return "unknown"
        return "passed" if exit_code == 0 else "failed"


class RequiredVerificationSet:
    """Aggregate registered items without counting one upstream item twice."""

    def __init__(self, requirements: Mapping[str, RequiredCommandEvidence]):
        if any(not isinstance(key, str) or not key for key in requirements):
            raise ValueError("invalid_verification_key")
        self.requirements = dict(requirements)

    def observe(self, method: str, params: object) -> None:
        for requirement in self.requirements.values():
            requirement.observe(method, params)

    def note_approved(self, key: str, item_id: str) -> None:
        self.requirements[key].note_approved(item_id)

    def snapshot(self, turn_status: str) -> dict:
        statuses = {key: item.status(turn_status)
                    for key, item in self.requirements.items()}
        seen: dict[str, str] = {}
        duplicates: set[str] = set()
        for key, item in self.requirements.items():
            for item_id in item.started.keys() | item.completed.keys() | item.approved_ids:
                previous = seen.setdefault(item_id, key)
                if previous != key:
                    duplicates.update((previous, key))
        for key in duplicates:
            statuses[key] = "unknown"
        summary = (next((state for state in VERIFICATION_PRIORITY
                        if state in statuses.values()), "not_run"))
        return {"items": statuses, "summary": summary}
