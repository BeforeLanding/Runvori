"""Print the exact command text persisted by a kept acceptance run.

Local diagnostic only. The acceptance report intentionally keeps only a
sanitized wrapper classification and byte length; when a real verification
fails, ERR-038 requires inspecting the original Host text in a disposable
workspace before deciding whether a new wrapper shape is justified.

The output contains raw command and cwd text. Never paste it into a report,
an instrumentation argument or committed evidence. Only folders inside the
repository's `.dev` directory are accepted so this cannot be pointed at a
real data directory.

Usage:
    python scripts/inspect_kept_commands.py .dev/device-acceptance-XXXXXXXX
    python scripts/inspect_kept_commands.py <folder>/data --run <run_id>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host/src"))

from runvori.codex import command_match


def data_dir_of(folder: Path) -> Path:
    if (folder / "data" / "host.db").is_file():
        return folder / "data"
    return folder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="kept acceptance folder or its data dir")
    parser.add_argument("--run", default="", help="run id (default: latest)")
    args = parser.parse_args()
    folder = Path(args.folder).resolve()
    disposable = (ROOT / ".dev").resolve()
    if disposable not in folder.parents and folder != disposable:
        print("refusing: only folders inside .dev are inspectable",
              file=sys.stderr)
        return 2
    data = data_dir_of(folder)
    database = data / "host.db"
    if not database.is_file():
        print("host.db not found", file=sys.stderr)
        return 2
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        if args.run:
            run = connection.execute(
                "SELECT run_id, session_id FROM runs WHERE run_id=?",
                (args.run,)).fetchone()
        else:
            run = connection.execute(
                "SELECT r.run_id, r.session_id FROM runs AS r "
                "JOIN run_verifications AS v ON v.run_id=r.run_id "
                "ORDER BY r.rowid DESC LIMIT 1").fetchone()
        if run is None:
            print("no run found", file=sys.stderr)
            return 1
        print(f"run={run['run_id']} session={run['session_id']}")
        requirements = connection.execute(
            "SELECT requirement_key, command, cwd FROM run_verifications "
            "WHERE run_id=? ORDER BY requirement_key",
            (run["run_id"],)).fetchall()
        for requirement in requirements:
            print(f"requirement[{requirement['requirement_key']}]="
                  f"{requirement['command']!r} cwd={requirement['cwd']!r}")
        commands = connection.execute(
            "SELECT item_id, command, command_truncated, cwd, exit_code "
            "FROM run_commands WHERE run_id=? "
            "ORDER BY COALESCE(started_event_seq, completed_event_seq, 0), "
            "item_id", (run["run_id"],)).fetchall()
        print(f"commands={len(commands)}")
        for command in commands:
            text = "" if command["command"] is None else command["command"]
            matched = [requirement["requirement_key"]
                       for requirement in requirements
                       if command_match.matches(
                           text, requirement["command"])]
            print(json.dumps({
                "item_id": command["item_id"],
                "truncated": bool(command["command_truncated"]),
                "exit_code": command["exit_code"],
                "length": len(text.encode("utf-8")),
                "matches": matched,
                "cwd": command["cwd"],
                "command": text,
            }, ensure_ascii=False))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
