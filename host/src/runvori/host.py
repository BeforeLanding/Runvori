"""Minimal product Host: own one data directory, recover, then serve upstream.

This composes the instance lock, process-isolation gate, unified epoch
recovery, write gate, device-access objects, one owned Codex app-server, the
single-owner event loop, an optional Windows local administration pipe, and an
optional HTTPS v1 listener. Both network entry points are opt-in per config
and are stopped before the mailbox closes.

Threading: start(), run(), and stop() must run on the thread that opens the
SQLite connection. Other threads may only use the mailbox and the bounded
queues.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shutil
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from runvori.api.server import ApiConfig, ApiServer, ApiServerError
from runvori.api.tls import TlsConfigError, load_tls_material
from runvori.application.command_mailbox import (
    CommandMailboxError, HostCommandMailbox,
)
from runvori.application.host_device_access import (
    HostDeviceAccess, HostDeviceAccessError,
)
from runvori.application.local_admin_server import (
    LocalAdminError, LocalAdminServer, load_or_create_admin_key,
)
from runvori.codex.owned_approval_queue import OwnedApprovalQueue
from runvori.codex.owned_event_loop import run_owned_event_loop
from runvori.codex.owned_interrupt_queue import OwnedInterruptQueue
from runvori.codex.owned_run_start import OwnedRunStartQueue
from runvori.codex.owned_thread_start import OwnedThreadStartQueue
from runvori.codex.stdio import TransportError
from runvori.codex.upstream_lifecycle import (
    UpstreamLifecycleError, isolate_and_recover_upstream,
    launch_owned_upstream,
)
from runvori.codex.version_probe import PINNED_CODEX, read_codex_version
from runvori.infrastructure.event_journal import EventJournalError
from runvori.infrastructure.instance_lock import InstanceLockUnavailable
from runvori.infrastructure.locked_journal import LockedHostJournals


EXIT_CLEAN = 0
EXIT_UNEXPECTED = 1
EXIT_STARTUP_REFUSED = 2
EXIT_UPSTREAM_RECOVERED = 3
EXIT_STOP_REFUSED = 4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HostStartupError(RuntimeError):
    """A safe startup refusal; the code never contains paths or payloads."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class HostLifecycleError(RuntimeError):
    """A safe lifecycle refusal after a successful start."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HostConfig:
    data_dir: Path
    upstream_command: list[str]
    upstream_cwd: Path
    upstream_env: dict[str, str]
    poll_timeout: float = 0.25
    now: Callable[[], str] = utc_now
    expected_upstream_version: str | None = None
    version_reader: Callable[[str, dict[str, str], Path], str] | None = None
    # HTTPS is opt-in: without a listen address the Host runs as before.
    listen_host: str | None = None
    listen_port: int = 0
    tls_certificate: Path | None = None
    tls_key: Path | None = None
    host_name: str = "Runvori Host"
    admin_pipe: bool = False


class HostRuntime:
    """One locked Host instance; create a new object to start again."""

    def __init__(self, config: HostConfig):
        self.config = config
        self.journals: LockedHostJournals | None = None
        self.mailbox: HostCommandMailbox | None = None
        self.device_access: HostDeviceAccess | None = None
        self.approval_queue: OwnedApprovalQueue | None = None
        self.thread_starts: OwnedThreadStartQueue | None = None
        self.run_starts: OwnedRunStartQueue | None = None
        self.interrupts: OwnedInterruptQueue | None = None
        self.owner = None
        self.epoch: int | None = None
        self.stop_reason: str | None = None
        self.api: object | None = None
        self.local_admin: object | None = None
        self.api_report: dict | None = None
        self._owner_thread: int | None = None
        self._started = False
        self._stopped = False

    # -- startup ---------------------------------------------------------
    def start(self) -> dict:
        self._validate_config()
        if self._started:
            raise HostStartupError("host_already_started")
        self._verify_upstream_version()
        journals = None
        mailbox = None
        try:
            journals = LockedHostJournals(self.config.data_dir).open()
            state = journals.startup_state()
            if state["epoch_state"] == "legacy_unknown":
                raise HostStartupError("legacy_upstream_unverified")
            if state["epoch_state"] not in (None, "unlaunched", "recorded"):
                raise HostStartupError("upstream_epoch_state_invalid")
            if state["epoch_state"] is None and not state["first_start"]:
                raise HostStartupError("upstream_epoch_state_invalid")
            if state["first_start"] and state["epoch_state"] is not None:
                raise HostStartupError("upstream_epoch_state_invalid")
            active = state["active_run"]
            recovery = journals.recover_host_after_isolation(
                old_process_epoch=state["epoch"],
                expected_run_id=None if active is None else active["run_id"],
                expected_run_version=None if active is None else active["version"],
                reason="host_restart", recorded_at=self.config.now())
            epoch = recovery["epoch"]
            mailbox = HostCommandMailbox()
            approvals = OwnedApprovalQueue()
            thread_starts = OwnedThreadStartQueue()
            run_starts = OwnedRunStartQueue()
            interrupts = OwnedInterruptQueue()
            device = HostDeviceAccess(journals, mailbox)
            owner = launch_owned_upstream(
                journals, epoch=epoch, command=list(self.config.upstream_command),
                cwd=self.config.upstream_cwd, env=dict(self.config.upstream_env),
                recorded_at=self.config.now())
            api, local_admin, api_report = self._start_interfaces(
                device, mailbox, thread_starts)
        except BaseException as error:
            if isinstance(error, UpstreamLifecycleError) and str(error) == \
                    "upstream_isolation_unconfirmed":
                # Keep the lock and the closed write gate; only process exit
                # may release the Job. The next Host still fails closed.
                self.journals = journals
                if mailbox is not None:
                    mailbox.close()
                raise HostStartupError(str(error)) from error
            if mailbox is not None:
                mailbox.close()
            if journals is not None:
                journals.close()
            if isinstance(error, HostStartupError):
                raise
            raise HostStartupError(self._startup_code(error)) from error

        self.journals = journals
        self.mailbox = mailbox
        self.approval_queue = approvals
        self.thread_starts = thread_starts
        self.run_starts = run_starts
        self.interrupts = interrupts
        self.device_access = device
        self.owner = owner
        self.epoch = epoch
        self.api = api
        self.local_admin = local_admin
        self.api_report = api_report
        self._owner_thread = threading.get_ident()
        self._started = True
        return {
            "status": "running",
            "host_id": device.host_id,
            "epoch": epoch,
            "recovered_epoch": state["epoch"],
            "recovered_run": recovery["run_event"],
            "api": api_report,
            "local_admin": (None if local_admin is None
                            else {"pipe": local_admin.address}),
        }

    def _start_interfaces(self, device: HostDeviceAccess,
                          mailbox: HostCommandMailbox,
                          thread_starts: OwnedThreadStartQueue):
        """Start the local pipe and the HTTPS listener in that order.

        A failure closes whatever already started, so a half-open Host never
        accepts a client that cannot reach the database owner.
        """
        api = None
        local_admin = None
        api_report = None
        try:
            if self.config.admin_pipe:
                key = load_or_create_admin_key(self.config.data_dir)
                local_admin = LocalAdminServer(
                    self.config.data_dir,
                    lambda request: self._admin_command(
                        request, device, mailbox, thread_starts),
                    key=key)
            if self.config.listen_host is not None:
                if (self.config.tls_certificate is None
                        or self.config.tls_key is None):
                    raise HostStartupError("tls_material_required")
                material = load_tls_material(
                    self.config.tls_certificate, self.config.tls_key)
                api = ApiServer(
                    ApiConfig(
                        listen_host=self.config.listen_host,
                        listen_port=self.config.listen_port, tls=material,
                        host_name=self.config.host_name,
                        clock=self.config.now),
                    device, mailbox)
                api_report = api.start()
                api_report["host_name"] = self.config.host_name
        except BaseException:
            if api is not None:
                api.stop()
            if local_admin is not None:
                local_admin.close()
            raise
        return api, local_admin, api_report

    def _admin_command(self, request: dict, device: HostDeviceAccess,
                       mailbox: HostCommandMailbox,
                       thread_starts: OwnedThreadStartQueue) -> dict:
        """Trusted local commands; each one runs on the mailbox owner thread."""
        command = request["command"]
        arguments = request["arguments"]
        if command == "status":
            view = mailbox.get_host_view()
            return {"status": "running",
                    "host_id": device.host_id,
                    "epoch": view["epoch"],
                    "active_run_id": view["active_run_id"],
                    "api": self.api_report,
                    "host_name": self.config.host_name}
        if command == "issue-pairing":
            secret, expires_at = device.issue_local_secret()
            if self.api_report is None:
                raise HostStartupError("api_not_listening")
            return {"status": "issued",
                    "https_url": _https_url(self.api_report,
                                            self.config.listen_host),
                    "host_id": device.host_id,
                    "host_name": self.config.host_name,
                    "spki_sha256": self.api_report["spki_sha256"],
                    "pairing_secret": base64.b64encode(secret).decode("ascii"),
                    "expires_at": expires_at}
        if command == "revoke-device":
            device.revoke_local_device()
            return {"status": "revoked"}
        if command == "register-workspace":
            root = arguments.get("root")
            sandbox = arguments.get("sandbox", "read-only")
            workspace_id = arguments.get("workspace_id")
            if not isinstance(root, str) or not root:
                raise HostStartupError("invalid_workspace_root")
            if not isinstance(workspace_id, str) or not workspace_id:
                raise HostStartupError("invalid_workspace_id")
            return mailbox.register_workspace(
                workspace_id=workspace_id, root=root, sandbox=sandbox)
        if command == "create-session":
            session_id = arguments.get("session_id")
            workspace_id = arguments.get("workspace_id")
            title = arguments.get("title")
            if not isinstance(session_id, str) or not session_id:
                raise HostStartupError("invalid_session_id")
            if not isinstance(workspace_id, str) or not workspace_id:
                raise HostStartupError("invalid_workspace_id")
            if title is not None and not isinstance(title, str):
                raise HostStartupError("invalid_session_title")
            created = mailbox.create_session(
                session_id=session_id, workspace_id=workspace_id, title=title,
                create_thread=bool(arguments.get("create_thread")),
                request_id=arguments.get("request_id"))
            return created
        if command == "start-thread":
            session_id = arguments.get("session_id")
            request_id = arguments.get("request_id")
            if not isinstance(session_id, str) or not session_id:
                raise HostStartupError("invalid_session_id")
            if not isinstance(request_id, str) or not request_id:
                raise HostStartupError("invalid_thread_start_request")
            return mailbox.start_thread(
                session_id=session_id, actor_id="local",
                request_id=request_id)
        raise HostStartupError("invalid_admin_command")

    def _stop_interfaces(self) -> None:
        """Close network entry points before the database owner goes away."""
        local_admin, self.local_admin = self.local_admin, None
        api, self.api = self.api, None
        if local_admin is not None:
            local_admin.close()
        if api is not None:
            api.stop()

    def _validate_config(self) -> None:
        config = self.config
        if (not isinstance(config, HostConfig)
                or not isinstance(config.data_dir, Path)
                or not isinstance(config.upstream_cwd, Path)
                or not isinstance(config.upstream_command, (list, tuple))
                or not config.upstream_command
                or any(not isinstance(item, str) or not item
                       for item in config.upstream_command)
                or not isinstance(config.upstream_env, dict)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in config.upstream_env.items())
                or not isinstance(config.poll_timeout, (int, float))
                or isinstance(config.poll_timeout, bool)
                or not math.isfinite(config.poll_timeout)
                or config.poll_timeout <= 0
                or not callable(config.now)
                or (config.expected_upstream_version is not None
                    and (not isinstance(config.expected_upstream_version, str)
                         or not config.expected_upstream_version))
                or (config.version_reader is not None
                    and not callable(config.version_reader))):
            raise HostStartupError("invalid_host_config")

    def _verify_upstream_version(self) -> None:
        expected = self.config.expected_upstream_version
        if expected is None:
            return
        executable = self.config.upstream_command[0]
        if not Path(executable).is_absolute():
            raise HostStartupError("codex_path_not_absolute")
        reader = self.config.version_reader or read_codex_version
        try:
            actual = reader(
                executable, dict(self.config.upstream_env),
                self.config.upstream_cwd)
        except HostStartupError:
            raise
        except TransportError as error:
            code = str(error)
            raise HostStartupError(
                code if code else "codex_version_probe_failed") from error
        except Exception:
            raise HostStartupError("codex_version_probe_failed") from None
        if actual != expected:
            raise HostStartupError("codex_version_mismatch")

    @staticmethod
    def _startup_code(error: BaseException) -> str:
        if isinstance(error, (EventJournalError, InstanceLockUnavailable,
                              UpstreamLifecycleError, HostDeviceAccessError,
                              TransportError, ApiServerError, TlsConfigError,
                              LocalAdminError, CommandMailboxError)):
            code = str(error)
            if code:
                return code
        return "host_startup_failed"

    # -- serving ---------------------------------------------------------
    def run(self, stop: threading.Event) -> dict:
        self._require_owner_thread()
        if not self._started or self.owner is None:
            raise HostLifecycleError("host_not_started")
        if not isinstance(stop, threading.Event):
            raise HostLifecycleError("invalid_stop_signal")
        try:
            result = run_owned_event_loop(
                self.owner, stop=stop, poll_timeout=self.config.poll_timeout,
                approvals=self.approval_queue, thread_starts=self.thread_starts,
                run_starts=self.run_starts, interrupts=self.interrupts,
                commands=self.mailbox)
        finally:
            # Whatever ended the loop, stop accepting clients first so no new
            # request can reach a closing owner thread.
            self._stop_interfaces()
        self.stop_reason = result["reason"]
        if result["reason"] == "upstream_recovered":
            # The loop isolated the old connection and advanced the epoch.
            # This Host exits; an external supervisor decides when to restart.
            self.owner = None
            self.epoch = result.get("epoch", self.epoch)
        return result

    def stop(self) -> dict:
        if not self._started or self._stopped:
            return {"status": "stopped", "epoch": self.epoch}
        self._require_owner_thread()
        self._stop_interfaces()
        if self.mailbox is not None:
            self.mailbox.close()
        result = {"status": "stopped", "epoch": self.epoch}
        owner = self.owner
        if owner is not None:
            try:
                recovery = isolate_and_recover_upstream(
                    owner, recorded_at=self.config.now(), reason="host_shutdown")
            except Exception as error:
                # Do not close the journals or release the lock: the recorded
                # identity must keep the next startup fail-closed. A later
                # stop() may retry; it must not report success while the
                # owner is still unresolved.
                raise HostLifecycleError(
                    "upstream_isolation_unconfirmed") from error
            self.owner = None
            self.epoch = recovery["epoch"]
            result["epoch"] = recovery["epoch"]
            result["recovered_run"] = recovery["run_event"]
        self._stopped = True
        self._close_journals()
        return result

    def _close_journals(self) -> None:
        journals = self.journals
        self.journals = None
        self.device_access = None
        self.mailbox = None
        self._started = False
        if journals is not None:
            journals.close()

    def _require_owner_thread(self) -> None:
        if self._owner_thread is None or threading.get_ident() != self._owner_thread:
            raise HostLifecycleError("host_thread_mismatch")


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _https_url(report: dict, configured_host: str | None) -> str:
    """A reachable display URL; loopback binds are shown as localhost."""
    host = configured_host or report["listen_host"]
    if host in {"0.0.0.0", "127.0.0.1", "::1", "::"}:
        host = "localhost"
    return f"https://{host}:{report['listen_port']}/v1"


def build_config(data_dir: Path, codex: str, *,
                 listen: str | None = None,
                 tls_certificate: Path | None = None,
                 tls_key: Path | None = None,
                 host_name: str = "Runvori Host",
                 admin_pipe: bool = False) -> HostConfig:
    executable = shutil.which(codex)
    if not executable:
        raise HostStartupError("codex_not_found")
    resolved = Path(data_dir)
    if not resolved.is_absolute() or not resolved.is_dir():
        raise HostStartupError("invalid_host_data_dir")
    absolute_codex = Path(executable).resolve()
    listen_host, listen_port = parse_listen(listen)
    if (listen_host is None) != (tls_certificate is None):
        raise HostStartupError("tls_material_required")
    if (tls_certificate is None) != (tls_key is None):
        raise HostStartupError("tls_material_required")
    return HostConfig(
        data_dir=resolved,
        upstream_command=[
            str(absolute_codex), "app-server", "--listen", "stdio://"],
        upstream_cwd=resolved,
        upstream_env=dict(os.environ),
        expected_upstream_version=PINNED_CODEX,
        listen_host=listen_host,
        listen_port=listen_port,
        tls_certificate=tls_certificate,
        tls_key=tls_key,
        host_name=host_name,
        admin_pipe=admin_pipe,
    )


def parse_listen(value: str | None) -> tuple[str | None, int]:
    """``--listen host:port``; omitted means the Host stays loopback-free."""
    if value is None:
        return None, 0
    if not isinstance(value, str) or ":" not in value:
        raise HostStartupError("invalid_listen_address")
    host, _, port_text = value.rpartition(":")
    if not host:
        raise HostStartupError("invalid_listen_address")
    try:
        port = int(port_text)
    except ValueError as error:
        raise HostStartupError("invalid_listen_address") from error
    if not 0 <= port <= 65535:
        raise HostStartupError("invalid_listen_address")
    return host.strip("[]"), port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m runvori.host")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--codex", default=os.environ.get("RUNVORI_CODEX", "codex"))
    parser.add_argument("--listen", default=os.environ.get("RUNVORI_LISTEN"),
                        help="host:port for the HTTPS v1 API")
    parser.add_argument("--tls-cert", type=Path,
                        default=_env_path("RUNVORI_TLS_CERT"))
    parser.add_argument("--tls-key", type=Path,
                        default=_env_path("RUNVORI_TLS_KEY"))
    parser.add_argument("--host-name",
                        default=os.environ.get("RUNVORI_HOST_NAME",
                                               "Runvori Host"))
    parser.add_argument("--admin-pipe", action="store_true",
                        default=os.environ.get("RUNVORI_ADMIN_PIPE") == "1",
                        help="enable the local administration named pipe")
    args = parser.parse_args(argv)
    try:
        runtime = HostRuntime(build_config(
            args.data_dir, args.codex, listen=args.listen,
            tls_certificate=args.tls_cert, tls_key=args.tls_key,
            host_name=args.host_name, admin_pipe=args.admin_pipe))
        report = runtime.start()
    except HostStartupError as error:
        print(json.dumps({"status": "failed", "code": error.code}), flush=True)
        return EXIT_STARTUP_REFUSED
    except Exception:
        print(json.dumps({"status": "failed", "code": "host_startup_failed"}),
              flush=True)
        return EXIT_STARTUP_REFUSED
    print(json.dumps(report, ensure_ascii=False), flush=True)

    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), request_stop)
            except (OSError, ValueError):
                pass
    try:
        result = runtime.run(stop)
    except KeyboardInterrupt:
        stop.set()
        result = {"reason": "stopped"}
    except Exception:
        try:
            runtime.stop()
        except HostLifecycleError as error:
            print(json.dumps({"status": "failed", "code": error.code}),
                  flush=True)
            return EXIT_STOP_REFUSED
        print(json.dumps({"status": "failed", "code": "host_run_failed"}),
              flush=True)
        return EXIT_UNEXPECTED
    try:
        runtime.stop()
    except HostLifecycleError as error:
        print(json.dumps({"status": "failed", "code": error.code}), flush=True)
        return EXIT_STOP_REFUSED
    if result["reason"] == "upstream_recovered":
        print(json.dumps({"status": "upstream_recovered",
                          "epoch": result.get("epoch")}), flush=True)
        return EXIT_UPSTREAM_RECOVERED
    print(json.dumps({"status": "stopped"}), flush=True)
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
