"""Explicitly gated M0 Codex execution probe; see CONTRIBUTING.md."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host/src"))

from runvori.cli import run_metadata, verify_contracts  # noqa: E402
from runvori.codex.execution_probe import (  # noqa: E402
    prepare_fixture, run_probe, write_report,
)
from runvori.codex.stdio import TransportError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--fixture", type=Path, default=ROOT / ".dev" / "execution-fixture")
    parser.add_argument("--account-home", type=Path)
    parser.add_argument("--approval", choices=["accept", "decline", "defer"])
    parser.add_argument("--scenario", choices=[
        "read_only", "write_file", "interrupt", "interrupt_pending",
        "interrupt_on_approval", "file_change_decline", "file_change_accept",
        "file_change_cut_after_accept", "interrupt_after_turn_completed",
        "interrupt_after_command_completed",
        "interrupt_after_command_completed_delayed"],
                        default="read_only")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare_fixture(ROOT, args.fixture)
            result = {"status": "prepared"}
        else:
            if args.account_home is None or args.approval is None:
                raise TransportError("account_home_and_approval_required")
            executable = shutil.which(args.codex)
            if executable is None:
                raise TransportError("codex_not_found")
            verify_contracts(ROOT / "contracts/codex")
            home = args.account_home.resolve(strict=True)
            # The configured account home is intentional; never copy auth data.
            env = dict(os.environ, CODEX_HOME=str(home))
            for key in list(env):
                if any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
                    del env[key]
            run_metadata(executable, env, home)
            report = run_probe(root=ROOT, fixture=args.fixture, account_home=home,
                               executable=executable, decision=args.approval,
                               scenario=args.scenario,
                               timeout=args.timeout)
            write_report(ROOT, report)
            result = {"status": report["status"], "scenario": report["scenario"],
                      "turn_status": report["turn_status"],
                      "approval_count": report["approval_count"], "resume": report["resume"],
                      "file_change_approval_count": report["file_change_approval_count"],
                      "command_approval_count": report["command_approval_count"],
                      "accepted_count": sum(check["decision"] == "accept"
                                            for check in report["approval_checks"]),
                      "file_match": report["file_match"],
                      "accepted_command_exit_zero": report["accepted_command_exit_zero"],
                      "accepted_command_exit_kind": report["accepted_command_exit_kind"],
                      "interrupt_rpc": report["interrupt_rpc"],
                      "interrupt_trigger": report["interrupt_trigger"],
                      "interrupt_marker_seen": report["interrupt_marker_seen"],
                      "interrupt_delay_ms": report["interrupt_delay_ms"],
                      "pending_request_resolved": report["pending_request_resolved"],
                      "pending_request_allowlisted": report["pending_request_allowlisted"],
                      "accepted_file_change_status": report["accepted_file_change_status"],
                      "file_change_item_allowlisted": report["file_change_item_allowlisted"],
                      "fixture_only_expected_file": report["fixture_only_expected_file"],
                      "fixture_entries_within_allowlist": report["fixture_entries_within_allowlist"],
                      "cutoff": report["cutoff"],
                      "managed_process_close_returned": report["managed_process_close_returned"],
                      "report": "workspace/.dev/execution-probe-report.json"}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, TransportError) as error:
        code = str(error) if isinstance(error, TransportError) else type(error).__name__
        result = {"status": "failed", "code": code}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("prepared", "observed") else 1


if __name__ == "__main__":
    sys.exit(main())
