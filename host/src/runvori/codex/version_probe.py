"""Exact-version gate for the app-server executable the Host will launch."""

from __future__ import annotations

import subprocess
from pathlib import Path

from runvori.codex.stdio import TransportError

PINNED_CODEX = "codex-cli 0.164.0-alpha.11.1"


def read_codex_version(executable: str, env: dict[str, str], cwd: Path,
                       *, timeout: float = 15) -> str:
    """Run one ``--version`` probe without exposing stderr or paths."""
    if (not isinstance(executable, str) or not executable
            or not isinstance(env, dict)
            or not isinstance(cwd, Path)
            or not isinstance(timeout, (int, float))
            or isinstance(timeout, bool) or timeout <= 0):
        raise TransportError("codex_version_probe_failed")
    try:
        result = subprocess.run(
            [executable, "--version"], env=env, cwd=cwd,
            capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise TransportError("codex_not_found") from None
    except subprocess.TimeoutExpired:
        raise TransportError("codex_version_probe_timeout") from None
    except OSError:
        raise TransportError("codex_not_executable") from None
    if result.returncode != 0:
        raise TransportError("codex_version_probe_failed")
    try:
        text = result.stdout.decode("utf-8")
    except (AttributeError, UnicodeDecodeError):
        raise TransportError("codex_version_probe_failed") from None
    lines = text.strip().splitlines()
    if len(lines) != 1 or not lines[0]:
        raise TransportError("codex_version_probe_failed")
    return lines[0]


def require_codex_version(executable: str, expected: str, env: dict[str, str],
                          cwd: Path, *, timeout: float = 15) -> str:
    """Return the version only when it exactly matches the pinned baseline."""
    if not isinstance(expected, str) or not expected:
        raise TransportError("codex_version_mismatch")
    actual = read_codex_version(executable, env, cwd, timeout=timeout)
    if actual != expected:
        raise TransportError("codex_version_mismatch")
    return actual
