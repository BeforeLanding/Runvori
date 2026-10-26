"""Local-only, no-inference validation commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from runvori.application.local_device_admin import (
    LocalDeviceAdminError, revoke_device_offline,
)
from runvori.codex.stdio import StdioClient, TransportError
from runvori.infrastructure.instance_lock import InstanceLockUnavailable

PINNED_CODEX = "codex-cli 0.164.0-alpha.11.1"
ROOT = Path(__file__).resolve().parents[3]
SELECTED = (
    "v1/InitializeParams.json", "v1/InitializeResponse.json",
    "v2/ThreadStartParams.json", "v2/ThreadStartResponse.json",
    "v2/ThreadResumeParams.json", "v2/TurnStartParams.json",
    "v2/TurnInterruptParams.json", "CommandExecutionRequestApprovalParams.json",
    "v2/ItemStartedNotification.json", "v2/ItemCompletedNotification.json",
    "CommandExecutionRequestApprovalResponse.json", "FileChangeRequestApprovalParams.json",
    "FileChangeRequestApprovalResponse.json", "ServerRequest.json",
    "v2/ServerRequestResolvedNotification.json", "v2/TurnCompletedNotification.json",
)


def run_metadata(executable: str, env: dict[str, str], home: Path) -> str:
    result = subprocess.run([executable, "--version"], env=env, cwd=home,
                            capture_output=True, timeout=15, check=True)
    version = result.stdout.decode("utf-8").strip()
    if version != PINNED_CODEX:
        raise TransportError("codex_version_mismatch")
    return version


def verify_contracts(directory: Path) -> int:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict)
            or manifest.get("codex_version") != PINNED_CODEX
            or not isinstance(manifest.get("files"), dict)
            or set(manifest["files"]) != set(SELECTED)):
        raise TransportError("schema_manifest_mismatch")
    for name, expected in manifest["files"].items():
        content = (directory / name).read_bytes()
        json.loads(content)
        if hashlib.sha256(content).hexdigest() != expected:
            raise TransportError("schema_hash_mismatch")
    return len(manifest["files"])


def execute(args: argparse.Namespace) -> dict:
    root = args.workspace.resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project.get("project", {}).get("name") != "runvori-host":
        raise TransportError("not_a_runvori_workspace")
    contracts = root / "contracts/codex"
    if args.command == "verify-contracts":
        return {"status": "passed", "schema_count": verify_contracts(contracts)}
    if args.command == "revoke-device":
        if args.data_dir is None:
            raise LocalDeviceAdminError("host_data_dir_required")
        return revoke_device_offline(args.data_dir)
    executable = shutil.which(args.codex)
    if not executable:
        raise TransportError("codex_not_found")
    scratch = root / ".dev"
    scratch.mkdir(exist_ok=True)
    # New home for each run: no real authentication or user configuration read.
    with tempfile.TemporaryDirectory(prefix="probe-", dir=scratch) as temporary:
        home = Path(temporary)
        env = dict(os.environ, CODEX_HOME=str(home))
        # Never forward model secrets accidentally inherited from the caller.
        for key in list(env):
            if any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
                del env[key]
        version = run_metadata(executable, env, home)
        if args.command == "export-schema":
            output = home / "schema"
            subprocess.run([executable, "app-server", "generate-json-schema", "--out", str(output)],
                           env=env, cwd=home, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=True)
            # Resolve every selected file before changing the compatibility baseline.
            selected = {name: (output / name).read_bytes() for name in SELECTED}
            if contracts.exists() and not args.replace:
                raise TransportError("schema_exists_use_replace_after_review")
            for name, content in selected.items():
                json.loads(content)
                target = contracts / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            manifest = {"codex_version": version, "files": {
                name: hashlib.sha256(content).hexdigest() for name, content in selected.items()}}
            (contracts / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            return {"status": "passed", "schema_count": verify_contracts(contracts)}
        verify_contracts(contracts)
        with StdioClient([executable, "app-server", "--listen", "stdio://"],
                         cwd=home, env=env, timeout=args.timeout) as client:
            result = client.initialize()
            required = {"codexHome", "userAgent", "platformFamily", "platformOs"}
            if not required.issubset(result) or not all(isinstance(result[k], str) for k in required):
                raise TransportError("initialize_shape_mismatch")
            if Path(result["codexHome"]).resolve() != home.resolve():
                raise TransportError("codex_home_not_isolated")
            listing = client.request("thread/list", {"limit": 1})
            if not isinstance(listing.get("data"), list):
                raise TransportError("thread_list_shape_mismatch")
        return {"status": "passed", "codex_version": version,
                "python_version": platform.python_version(),
                "platform": platform.system(), "sqlite_version": sqlite3.sqlite_version,
                "initialize": "passed", "initialized_then_thread_list": "passed",
                "isolated_home": True, "inference_started": False,
                "process_containment": "windows_job" if os.name == "nt" else "posix_group",
                "production_process_supervision": "not_validated"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[
        "doctor", "export-schema", "verify-contracts", "revoke-device"])
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--replace", action="store_true", help="explicitly replace schema baseline")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--data-dir", type=Path,
                        help="existing absolute Host data directory for offline revocation")
    args = parser.parse_args()
    try:
        result = execute(args)
    except (OSError, ValueError, KeyError, sqlite3.Error,
            subprocess.SubprocessError, TransportError,
            InstanceLockUnavailable) as error:
        # Do not echo exception messages from filesystem/subprocess: may contain secrets.
        code = (str(error) if isinstance(
            error, (TransportError, LocalDeviceAdminError,
                    InstanceLockUnavailable)) else type(error).__name__)
        print(json.dumps({"status": "failed", "code": code}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
