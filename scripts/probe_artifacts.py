"""No-inference, disposable Git artifact size and binary probe."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host/src"))

from runvori.codex.execution_probe import (  # noqa: E402
    DIRTY_TRACKED_BEFORE, DIRTY_UNTRACKED_BEFORE, MARKER,
    _git_status_entries, fixture_path, prepare_dirty_fixture, prepare_fixture,
)
from runvori.codex.stdio import TransportError  # noqa: E402
from runvori.infrastructure.artifact_probe import capture_worktree_file  # noqa: E402

LARGE_BYTES = b"L\n" * 65536
BINARY_BYTES = b"\x00\xff" * 1024
EXPECTED_NAMES = {MARKER, ".git", "tracked.txt", "existing-untracked.txt",
                  "large.txt", "binary.bin"}
EXPECTED_STATUS = {b" M tracked.txt", b" M large.txt", b" M binary.bin",
                   b"?? existing-untracked.txt"}
RENAME_BYTES = b"runvori rename fixture\n"
RENAME_NAMES = {MARKER, ".git", "staged-new.txt", "worktree-new.txt"}


def _git_output(target: Path, args: list[str]) -> bytes:
    result = subprocess.run(["git", *args], cwd=target, capture_output=True,
                            timeout=15, check=False)
    if result.returncode:
        raise TransportError("artifact_git_query_failed")
    return result.stdout


def prepare_rename(fixture: Path) -> Path:
    target = prepare_fixture(ROOT, fixture)
    for name in ("staged-old.txt", "worktree-old.txt"):
        (target / name).write_bytes(RENAME_BYTES)
    for args in (
        ["add", "--", MARKER, "staged-old.txt", "worktree-old.txt"],
        ["-c", "user.name=RunvoriProbe", "-c", "user.email=probe@invalid.local",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "rename baseline"],
        ["mv", "--", "staged-old.txt", "staged-new.txt"],
    ):
        _git_output(target, args)
    (target / "worktree-old.txt").rename(target / "worktree-new.txt")
    validate_rename(target)
    return target


def validate_rename(fixture: Path) -> Path:
    target = fixture_path(ROOT, fixture)
    if (not target.is_dir() or {entry.name for entry in target.iterdir()} != RENAME_NAMES
            or any((target / name).is_symlink() for name in RENAME_NAMES)
            or not (target / ".git").is_dir()
            or (target / MARKER).read_text(encoding="utf-8") !=
            "Runvori disposable execution probe\n"
            or any((target / name).read_bytes() != RENAME_BYTES for name in
                   ("staged-new.txt", "worktree-new.txt"))):
        raise TransportError("invalid_rename_fixture")
    return target


def inspect_rename(fixture: Path) -> dict:
    target = validate_rename(fixture)
    staged = _git_output(target, ["diff", "--cached", "--name-status", "-z",
                                  "--find-renames=100%"])
    worktree = _git_output(target, ["diff", "--name-status", "-z",
                                    "--find-renames=100%"])
    untracked = _git_output(target, ["ls-files", "--others", "--exclude-standard", "-z"])
    if (staged != b"R100\0staged-old.txt\0staged-new.txt\0"
            or worktree != b"D\0worktree-old.txt\0"
            or untracked != b"worktree-new.txt\0"):
        raise TransportError("artifact_rename_evidence_unexpected")
    return {
        "status": "observed", "source": "git_worktree_snapshot",
        "attribution": "unknown", "staged_move": "git_rename_100",
        "unstaged_move": "deletion_plus_untracked",
        "unstaged_rename_confirmed": False,
    }


def prepare(fixture: Path) -> Path:
    target = prepare_dirty_fixture(ROOT, fixture)
    (target / "large.txt").write_bytes(b"large baseline\n")
    (target / "binary.bin").write_bytes(b"baseline\0")
    for command in (
        ["git", "add", "--", "large.txt", "binary.bin"],
        ["git", "-c", "user.name=RunvoriProbe",
         "-c", "user.email=probe@invalid.local", "-c", "commit.gpgsign=false",
         "commit", "--quiet", "-m", "artifact baseline"],
    ):
        result = subprocess.run(command, cwd=target, capture_output=True,
                                timeout=15, check=False)
        if result.returncode:
            raise TransportError("artifact_fixture_baseline_failed")
    (target / "large.txt").write_bytes(LARGE_BYTES)
    (target / "binary.bin").write_bytes(BINARY_BYTES)
    validate(target)
    return target


def validate(fixture: Path) -> Path:
    target = fixture_path(ROOT, fixture)
    if (not target.is_dir() or {entry.name for entry in target.iterdir()} != EXPECTED_NAMES
            or any((target / name).is_symlink() for name in EXPECTED_NAMES)
            or not (target / ".git").is_dir()
            or (target / MARKER).read_text(encoding="utf-8") !=
            "Runvori disposable execution probe\n"
            or (target / "tracked.txt").read_bytes() != DIRTY_TRACKED_BEFORE
            or (target / "existing-untracked.txt").read_bytes() != DIRTY_UNTRACKED_BEFORE
            or (target / "large.txt").read_bytes() != LARGE_BYTES
            or (target / "binary.bin").read_bytes() != BINARY_BYTES
            or _git_status_entries(target) != EXPECTED_STATUS):
        raise TransportError("invalid_artifact_fixture")
    return target


def inspect(fixture: Path) -> dict:
    target = validate(fixture)
    files = {name: capture_worktree_file(target, name)
             for name in ("tracked.txt", "large.txt", "binary.bin")}
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-textconv", "--numstat", "-z",
         "--", "large.txt", "binary.bin"],
        cwd=target, capture_output=True, timeout=15, check=False)
    if diff.returncode or not diff.stdout.endswith(b"\0"):
        raise TransportError("artifact_git_diff_failed")
    numstat = set(diff.stdout.split(b"\0")[:-1])
    if len(numstat) != 2 or not any(row.endswith(b"\tlarge.txt") for row in numstat):
        raise TransportError("artifact_git_diff_unexpected")
    binary_marker = b"-\t-\tbinary.bin" in numstat
    return {
        "status": "observed", "source": "git_worktree_snapshot",
        "attribution": "unknown", "preexisting_changes": True,
        "git_binary_marker": binary_marker,
        "large_text": {key: files["large.txt"][key] for key in (
            "size_bytes", "representation", "content_available", "truncated", "reason")},
        "binary": {key: files["binary.bin"][key] for key in (
            "size_bytes", "representation", "content_available", "truncated", "reason")},
        "small_text_content_available": files["tracked.txt"]["content_available"],
        "all_bounded_hashes_present": all(file["sha256"] for file in files.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "inspect",
                                           "prepare-rename", "inspect-rename"))
    parser.add_argument("--fixture", type=Path,
                        default=ROOT / ".dev" / "artifact-fixture")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare(args.fixture)
            result = {"status": "prepared"}
        elif args.action == "inspect":
            result = inspect(args.fixture)
        elif args.action == "prepare-rename":
            prepare_rename(args.fixture)
            result = {"status": "prepared"}
        else:
            result = inspect_rename(args.fixture)
    except (OSError, ValueError, subprocess.SubprocessError, TransportError) as error:
        result = {"status": "failed", "code": (str(error) if isinstance(error, TransportError)
                                                else type(error).__name__)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("prepared", "observed") else 1


if __name__ == "__main__":
    sys.exit(main())
