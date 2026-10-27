"""Exact-version gate at the Host startup boundary."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from runvori.codex.stdio import TransportError
from runvori.codex.version_probe import (
    PINNED_CODEX, read_codex_version, require_codex_version,
)
from runvori.host import (
    HostConfig, HostRuntime, HostStartupError, build_config, main,
)


ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_app_server.py")


def _config(data_dir: Path, **overrides) -> HostConfig:
    values = {
        "data_dir": Path(data_dir),
        "upstream_command": [sys.executable, str(FAKE), "thread_bootstrap_good"],
        "upstream_cwd": ROOT,
        "upstream_env": dict(os.environ),
        "poll_timeout": 0.05,
        "now": lambda: "2026-10-26T00:00:00Z",
    }
    values.update(overrides)
    return HostConfig(**values)


class VersionProbeTests(unittest.TestCase):
    def test_exact_version_is_accepted(self):
        with patch("runvori.codex.version_probe.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = (PINNED_CODEX + "\n").encode()
            self.assertEqual(
                require_codex_version("codex", PINNED_CODEX, {}, ROOT),
                PINNED_CODEX)
            run.assert_called_once()

    def test_mismatch_is_refused(self):
        with patch("runvori.codex.version_probe.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"codex-cli 999.0.0\n"
            with self.assertRaisesRegex(TransportError,
                                        "codex_version_mismatch"):
                require_codex_version("codex", PINNED_CODEX, {}, ROOT)

    def test_probe_failures_map_to_stable_codes(self):
        cases = [
            (FileNotFoundError(), "codex_not_found"),
            (subprocess.TimeoutExpired(["codex"], 1),
             "codex_version_probe_timeout"),
            (OSError("not executable"), "codex_not_executable"),
        ]
        for error, code in cases:
            with self.subTest(code=code):
                with patch("runvori.codex.version_probe.subprocess.run",
                           side_effect=error):
                    with self.assertRaisesRegex(TransportError, code):
                        read_codex_version("codex", {}, ROOT)

    def test_nonzero_exit_and_malformed_stdout_are_refused(self):
        cases = [(1, b"codex-cli 0.164.0-alpha.11.1\n"),
                 (0, b"first\nsecond\n"),
                 (0, b"\xff\n")]
        for returncode, stdout in cases:
            with self.subTest(returncode=returncode, stdout=stdout):
                with patch("runvori.codex.version_probe.subprocess.run") as run:
                    run.return_value.returncode = returncode
                    run.return_value.stdout = stdout
                    with self.assertRaisesRegex(
                            TransportError, "codex_version_probe_failed"):
                        read_codex_version("codex", {}, ROOT)


class HostVersionGateTests(unittest.TestCase):
    def test_mismatch_is_refused_before_any_state_is_created(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            data_dir = Path(temp)
            config = _config(
                data_dir, expected_upstream_version=PINNED_CODEX,
                version_reader=lambda *_: "codex-cli 999.0.0")
            with self.assertRaisesRegex(HostStartupError,
                                        "codex_version_mismatch"):
                HostRuntime(config).start()
            self.assertEqual(list(data_dir.iterdir()), [])

    def test_relative_pinned_executable_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            config = _config(
                Path(temp), upstream_command=["codex", "app-server"],
                expected_upstream_version=PINNED_CODEX,
                version_reader=lambda *_: PINNED_CODEX)
            with self.assertRaisesRegex(HostStartupError,
                                        "codex_path_not_absolute"):
                HostRuntime(config).start()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_probe_error_code_is_preserved_without_state(self):
        def fail(*_):
            raise TransportError("codex_version_probe_timeout")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            config = _config(
                Path(temp), expected_upstream_version=PINNED_CODEX,
                version_reader=fail)
            with self.assertRaisesRegex(HostStartupError,
                                        "codex_version_probe_timeout"):
                HostRuntime(config).start()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_matching_pinned_version_starts_with_fake_upstream(self):
        calls = []

        def reader(executable, env, cwd):
            calls.append((executable, env, cwd))
            return PINNED_CODEX

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            config = _config(
                Path(temp), expected_upstream_version=PINNED_CODEX,
                version_reader=reader)
            runtime = HostRuntime(config)
            runtime.start()
            try:
                self.assertEqual(calls, [(
                    sys.executable, dict(os.environ), ROOT)])
                self.assertEqual(runtime.epoch, 1)
            finally:
                runtime.stop()

    def test_no_pin_does_not_probe(self):
        def fail(*_):
            raise AssertionError("probe must not run")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            runtime = HostRuntime(_config(Path(temp), version_reader=fail))
            runtime.start()
            runtime.stop()

    def test_build_config_pins_an_absolute_executable(self):
        executable = ROOT / "codex.exe"
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with patch("runvori.host.shutil.which",
                       return_value=str(executable)) as which:
                config = build_config(Path(temp), "codex-from-path")
            which.assert_called_once_with("codex-from-path")
            self.assertEqual(config.upstream_command[0], str(executable))
            self.assertEqual(config.upstream_command[1:],
                             ["app-server", "--listen", "stdio://"])
            self.assertEqual(config.expected_upstream_version, PINNED_CODEX)

    def test_build_config_missing_executable_is_refused(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with patch("runvori.host.shutil.which", return_value=None):
                with self.assertRaisesRegex(HostStartupError,
                                            "codex_not_found"):
                    build_config(Path(temp), "missing")

    def test_main_uses_runvori_codex_environment_default(self):
        seen = []

        def refuse(data_dir, codex, **_options):
            seen.append((data_dir, codex))
            raise HostStartupError("codex_not_found")

        with tempfile.TemporaryDirectory(dir=ROOT / ".dev") as temp:
            with patch("runvori.host.build_config", side_effect=refuse), \
                    patch.dict(os.environ, {"RUNVORI_CODEX": "C:/codex.exe"}):
                with redirect_stdout(io.StringIO()):
                    code = main(["--data-dir", temp])
            self.assertEqual(code, 2)
            self.assertEqual(seen, [(Path(temp), "C:/codex.exe")])


if __name__ == "__main__":
    unittest.main()
