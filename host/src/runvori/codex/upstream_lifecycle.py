"""Single in-process app-server ownership and bounded exit recovery."""

import os
from pathlib import Path

from runvori.codex.stdio import StdioClient


class UpstreamLifecycleError(RuntimeError):
    """Safe lifecycle refusal without process output or workspace paths."""


class OwnedUpstream:
    """One connection launched while a specific locked Host owns an epoch."""

    def __init__(self, journals, epoch: int):
        self.journals = journals
        self.epoch = epoch
        self.client: StdioClient | None = None


def launch_owned_upstream(journals, *, epoch: int, command: list[str],
                          cwd: Path, env: dict[str, str]) -> OwnedUpstream:
    """Reserve the only connection slot before spawning and initializing."""
    if os.name != "nt":
        raise UpstreamLifecycleError("windows_job_required")
    owner = OwnedUpstream(journals, epoch)
    journals.reserve_upstream_owner(owner, epoch=epoch)
    client = None
    try:
        client = StdioClient(command, cwd=cwd, env=env)
        owner.client = client
        client.initialize()
        return owner
    except BaseException:
        if client is None:
            journals.release_upstream_owner(owner)
            raise
        try:
            client.close()
            if (client._job is None or client._job.handle is not None
                    or client.process.poll() is None):
                raise UpstreamLifecycleError("upstream_isolation_unconfirmed")
        except Exception as error:
            journals.freeze_writes_for_upstream_exit(owner, epoch=epoch)
            raise UpstreamLifecycleError("upstream_isolation_unconfirmed") from error
        journals.release_upstream_owner(owner)
        raise


def isolate_and_recover_upstream(owner: OwnedUpstream, *,
                                 recorded_at: str) -> dict:
    """Freeze writes, close the owned Windows Job, then recover its epoch."""
    if not isinstance(owner, OwnedUpstream):
        raise UpstreamLifecycleError("unowned_upstream_connection")
    client = owner.client
    if client is None or client._job is None:
        raise UpstreamLifecycleError("unowned_upstream_connection")
    journals = owner.journals
    journals.freeze_writes_for_upstream_exit(owner, epoch=owner.epoch)
    try:
        if not client._closed:
            client.close()
    except Exception as error:
        raise UpstreamLifecycleError("upstream_isolation_unconfirmed") from error
    if client._job.handle is not None or client.process.poll() is None:
        raise UpstreamLifecycleError("upstream_isolation_unconfirmed")
    db = journals.events.connection
    slot = db.execute("SELECT run_id FROM active_slot WHERE singleton=1").fetchone()
    run_id = None if slot is None else slot["run_id"]
    version = None
    if run_id is not None:
        row = db.execute("SELECT version FROM runs WHERE run_id=?",
                         (run_id,)).fetchone()
        if row is None:
            raise UpstreamLifecycleError("active_run_missing")
        version = row["version"]
    result = journals.recover_host_after_isolation(
        old_process_isolated=True, old_process_epoch=owner.epoch,
        expected_run_id=run_id, expected_run_version=version,
        reason="upstream_exit", recorded_at=recorded_at)
    journals.release_upstream_owner(owner)
    return result
