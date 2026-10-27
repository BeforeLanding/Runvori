"""Shared fixtures for approval tests that must offer a reviewable request.

Approval allow is gated on complete details, and a command review is complete
only when its cwd resolves inside the registered workspace. Tests therefore
register the temporary data directory as a workspace before creating sessions.
"""

from __future__ import annotations

from pathlib import Path


def reviewable_command(cwd: Path, *, command: str = "python --version",
                       item_id: str = "item_demo",
                       reason: str | None = None) -> dict:
    """A command review whose cwd sits inside the test workspace."""
    return {
        "kind": "command",
        "command": command,
        "cwd": str(cwd),
        "reason": reason,
        "item_id": item_id,
        "requested_at": "2026-10-25T00:00:04Z",
        "upstream_approval_id": None,
        "command_kind": "command",
        "changes": [],
        "review_truncated": False,
    }


def seed_workspace_session(startup, epoch: int, *,
                           session_id: str = "session",
                           workspace_id: str = "workspace") -> dict:
    """Register the data directory as a workspace and create one session."""
    registration = startup.register_workspace(
        workspace_id, startup.data_dir, sandbox="read-only", epoch=epoch)
    startup.create_session_in_workspace(
        session_id, workspace_id, {}, epoch=epoch)
    return registration