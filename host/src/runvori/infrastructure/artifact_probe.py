"""Bounded local file capture for M0 Git-worktree artifact evidence."""

from __future__ import annotations

import codecs
import hashlib
from pathlib import Path


TEXT_LIMIT = 64 * 1024
SCAN_LIMIT = 4 * 1024 * 1024
CHUNK_SIZE = 64 * 1024


def capture_worktree_file(root: Path, relative: str, *,
                          text_limit: int = TEXT_LIMIT,
                          scan_limit: int = SCAN_LIMIT) -> dict:
    if (not isinstance(relative, str) or not relative
            or text_limit < 0 or scan_limit <= 0 or text_limit > scan_limit):
        raise ValueError("invalid_artifact_capture_request")
    root = root.resolve(strict=True)
    rel = Path(relative)
    if rel.is_absolute() or any(part in ("..", "") for part in rel.parts):
        raise ValueError("artifact_outside_workspace")
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("artifact_link_not_allowed")
    if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
        raise ValueError("artifact_outside_workspace")
    before = path.stat()
    result = {"path": rel.as_posix(), "source": "git_worktree_snapshot",
              "attribution": "unknown", "size_bytes": before.st_size,
              "sha256": None, "representation": "unknown",
              "content": None, "content_available": False,
              "truncated": False, "reason": None}
    if before.st_size > scan_limit:
        result.update(truncated=True, reason="scan_limit")
        return result
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    text_parts: list[str] = []
    text_valid = True
    nul_seen = False
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            total += len(chunk)
            if total > scan_limit:
                raise ValueError("artifact_changed_during_read")
            digest.update(chunk)
            nul_seen = nul_seen or b"\0" in chunk
            if text_valid:
                try:
                    decoded = decoder.decode(chunk)
                    if before.st_size <= text_limit:
                        text_parts.append(decoded)
                except UnicodeDecodeError:
                    text_valid = False
        if text_valid:
            try:
                tail = decoder.decode(b"", final=True)
                if before.st_size <= text_limit:
                    text_parts.append(tail)
            except UnicodeDecodeError:
                text_valid = False
    after = path.stat()
    if (total != before.st_size or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns):
        raise ValueError("artifact_changed_during_read")
    result["sha256"] = digest.hexdigest()
    if not text_valid or nul_seen:
        result.update(representation="binary_or_non_utf8", reason="binary_metadata_only")
    elif before.st_size > text_limit:
        result.update(representation="utf8_text", truncated=True,
                      reason="text_limit")
    else:
        result.update(representation="utf8_text", content="".join(text_parts),
                      content_available=True)
    return result
