"""Product Host startup composition: lock, recovery, upstream, device access."""

import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from runvori.application.host_device_access import HostDeviceAccessError
from runvori.codex.upstream_lifecycle import OwnedUpstream, UpstreamLifecycleError
from runvori.host import (
    EXIT_STARTUP_REFUSED, EXIT_UPSTREAM_RECOVERED, HostConfig,
    HostLifecycleError, HostRuntime, HostStartupError, main,
)
from runvori.infrastructure.locked_journal import LockedHostJournals
from runvori.infrastructure.process_identity import capture_process_identity


ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_app_server.py")


class HostRuntimeTests(unittest.TestCase):
    def _config(self, data_dir: Path, mode: str = "thread_bootstrap_good"):
        return HostConfig(
            data_dir=Path(data_dir),
            upstream_command=[sys.executable, str(FAKE), mode],
            upstream_cwd=ROOT,
            upstream_env=dict(os.environ),
            poll_timeout=0.05,
            now=lambda: "2026-10-26T00:00:00Z")

    def _start(self, data_dir: Path, mode: str = "thread_bootstrap_good"):
        runtime = HostRuntime(self._config(data_dir, mode))
        runtime.start()
        self.addCleanup(self._stop_quietly, runtime)
        return runtime

    @staticmethod
    def _stop_quietly(runtime):
        try:
            runtime.stop()
        except Exception:
            pass

    def test_first_start_composes_device_access_and_clean_stop_releases_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            runtime = HostRuntime(self._config(data_dir))
            report = runtime.start()
            self.assertEqual(report["status"], "running")
            self.assertEqual(report["epoch"], 1)
            self.assertTrue(report["host_id"].startswith("host_"))
            self.assertTrue((data_dir / "pairing.db").is_file())
            device = runtime.device_access
            secret, ttl = device.issue_local_secret()
            self.assertTrue(secret)
            self.assertGreater(ttl, 0)
            self.assertEqual(runtime.journals.events.connection.execute(
                "SELECT state FROM upstream_epochs WHERE process_epoch=1"
            ).fetchone()[0], "recorded")

            stopped = runtime.stop()
            self.assertEqual(stopped["epoch"], 2)
            with self.assertRaisesRegex(HostDeviceAccessError, "host_unavailable"):
                device.issue_local_secret()
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(db.execute(
                    "SELECT state FROM upstream_epochs WHERE process_epoch=2"
                ).fetchone()[0], "unlaunched")

            replacement = self._start(data_dir)
            self.assertEqual(replacement.epoch, 3)
            self._stop_quietly(replacement)

    def test_second_runtime_cannot_take_the_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            runtime = self._start(Path(temp))
            try:
                second = HostRuntime(self._config(Path(temp)))
                with self.assertRaisesRegex(HostStartupError,
                                            "instance_lock_unavailable"):
                    second.start()
                self.assertIsNone(runtime.owner.client.process.poll())
            finally:
                self._stop_quietly(runtime)

    def test_same_runtime_cannot_start_twice(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            runtime = self._start(Path(temp))
            with self.assertRaisesRegex(HostStartupError, "host_already_started"):
                runtime.start()
            self._stop_quietly(runtime)

    def test_legacy_epoch_without_process_record_is_refused_without_side_effects(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as seed:
                epoch = seed.recover_host_after_isolation(
                    old_process_epoch=0, expected_run_id=None,
                    expected_run_version=None, reason="host_restart",
                    recorded_at="2026-10-26T00:00:00Z")["epoch"]
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute("DROP TABLE upstream_epochs")
                db.execute("PRAGMA user_version=12")
                db.commit()
            runtime = HostRuntime(self._config(data_dir))
            with self.assertRaisesRegex(HostStartupError,
                                        "legacy_upstream_unverified"):
                runtime.start()
            self.assertFalse((data_dir / "pairing.db").exists())
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                self.assertEqual(db.execute(
                    "SELECT epoch FROM journal_meta WHERE singleton=1"
                ).fetchone()[0], epoch)
                self.assertEqual(db.execute(
                    "SELECT state FROM upstream_epochs WHERE process_epoch=?",
                    (epoch,)).fetchone()[0], "legacy_unknown")
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM upstream_processes").fetchone()[0], 0)
            # The failed start released the lock, but the refusal is stable.
            second = HostRuntime(self._config(data_dir))
            with self.assertRaisesRegex(HostStartupError,
                                        "legacy_upstream_unverified"):
                second.start()

    def test_v12_recorded_dead_process_migrates_and_starts(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as seed:
                epoch = seed.recover_host_after_isolation(
                    old_process_epoch=0, expected_run_id=None,
                    expected_run_version=None, reason="host_restart",
                    recorded_at="2026-10-26T00:00:00Z")["epoch"]
                owner = OwnedUpstream(seed, epoch)
                seed.reserve_upstream_owner(owner, epoch=epoch)
                live = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"])
                try:
                    identity = capture_process_identity(live)
                    seed.record_upstream_process(
                        owner, epoch=epoch, pid=identity["pid"],
                        created_at=identity["created_at"],
                        recorded_at="2026-10-26T00:00:01Z")
                finally:
                    live.kill()
                    live.wait(timeout=5)
                    seed.release_upstream_owner(owner)
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute("DROP TABLE upstream_epochs")
                db.execute("PRAGMA user_version=12")
                db.commit()
            runtime = self._start(data_dir)
            self.assertEqual(runtime.epoch, epoch + 1)
            self._stop_quietly(runtime)

    def test_inconsistent_epoch_state_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            with LockedHostJournals(data_dir) as seed:
                epoch = seed.recover_host_after_isolation(
                    old_process_epoch=0, expected_run_id=None,
                    expected_run_version=None, reason="host_restart",
                    recorded_at="2026-10-26T00:00:00Z")["epoch"]
            with closing(sqlite3.connect(data_dir / "host.db")) as db:
                db.execute(
                    "INSERT INTO upstream_processes VALUES (?, 1, 1, 'now')",
                    (epoch,))
                db.commit()
            runtime = HostRuntime(self._config(data_dir))
            with self.assertRaisesRegex(HostStartupError,
                                        "upstream_epoch_state_invalid"):
                runtime.start()
            self.assertFalse((data_dir / "pairing.db").exists())

    def test_launch_failure_releases_the_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            runtime = HostRuntime(self._config(data_dir))
            with patch("runvori.host.launch_owned_upstream",
                       side_effect=UpstreamLifecycleError(
                           "upstream_initialize_failed")):
                with self.assertRaisesRegex(HostStartupError,
                                            "upstream_initialize_failed"):
                    runtime.start()
            replacement = self._start(data_dir)
            self.assertEqual(replacement.epoch, 2)
            self._stop_quietly(replacement)

    def test_run_and_stop_require_the_owner_thread(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            runtime = self._start(Path(temp))
            failures = []

            def call():
                try:
                    runtime.stop()
                except Exception as error:
                    failures.append(error)

            thread = threading.Thread(target=call)
            thread.start()
            thread.join()
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], HostLifecycleError)
            self.assertIn("host_thread_mismatch", str(failures[0]))
            self._stop_quietly(runtime)

    def test_upstream_fault_ends_the_host_after_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            runtime = self._start(Path(temp), mode="exit_after_initialized")
            stop = threading.Event()
            result = runtime.run(stop)
            self.assertEqual(result["reason"], "upstream_recovered")
            self.assertEqual(result["cause"], "transport_fault")
            self.assertIsNone(runtime.owner)
            retained = runtime.epoch
            runtime.stop()
            with closing(sqlite3.connect(Path(temp) / "host.db")) as db:
                self.assertEqual(db.execute(
                    "SELECT epoch FROM journal_meta WHERE singleton=1"
                ).fetchone()[0], retained)
                self.assertEqual(db.execute(
                    "SELECT state FROM upstream_epochs WHERE process_epoch=?",
                    (retained,)).fetchone()[0], "unlaunched")
            replacement = self._start(Path(temp))
            self.assertEqual(replacement.epoch, retained + 1)
            self._stop_quietly(replacement)

    def test_invalid_config_is_refused(self):
        config = HostConfig(data_dir=Path("."), upstream_command=[],
                            upstream_cwd=Path("."), upstream_env={})
        with self.assertRaisesRegex(HostStartupError, "invalid_host_config"):
            HostRuntime(config).start()

    def test_cli_entry_maps_startup_refusal_and_upstream_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            output = io.StringIO()
            with patch("runvori.host.build_config",
                       side_effect=HostStartupError("legacy_upstream_unverified")):
                with redirect_stdout(output):
                    code = main(["--data-dir", str(data_dir)])
            self.assertEqual(code, EXIT_STARTUP_REFUSED)
            self.assertIn("legacy_upstream_unverified", output.getvalue())

            output = io.StringIO()
            config = self._config(data_dir, "exit_after_initialized")
            with patch("runvori.host.build_config", return_value=config), \
                    patch("runvori.host.signal.signal"):
                with redirect_stdout(output):
                    code = main(["--data-dir", str(data_dir)])
            self.assertEqual(code, EXIT_UPSTREAM_RECOVERED)
            self.assertIn("upstream_recovered", output.getvalue())


if __name__ == "__main__":
    unittest.main()
