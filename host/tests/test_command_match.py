"""Limited, fail-closed equivalence for required-command evidence."""

import unittest

from runvori.codex import command_match

EXE = (r"C:\Users\probe\.cache\codex-runtimes"
       r"\codex-primary-runtime\dependencies\native\powershell\pwsh.exe")
QUOTED = f'"{EXE}"'
SYSTEM_POWERSHELL = (
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
# Byte-for-byte string the real app-server sent for the DEV-008 `v` turn and
# returned again through `thread/read` (ERR-038): the executable is quoted and
# its backslashes are doubled in the serialized command text.
OBSERVED_SYSTEM_POWERSHELL = (
    r'"C:\\windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"')


class CommandMatchTests(unittest.TestCase):
    def test_exact_text_matches(self):
        self.assertTrue(command_match.matches("python --version",
                                              "python --version"))
        self.assertEqual(
            command_match.classify("python --version", "python --version"),
            {"kind": "exact", "inner_exact": True, "length": 16})

    def test_observed_wrapper_shapes_match(self):
        for value in (
                f"{QUOTED} -Command 'python --version'",
                f"{QUOTED} -NoProfile -Command 'python --version'",
                f'{QUOTED} -Command "python --version"'):
            with self.subTest(value=value):
                self.assertTrue(command_match.matches(
                    value, "python --version"))
                classification = command_match.classify(
                    value, "python --version")
                self.assertEqual(classification["kind"], "pwsh_wrapper")
                self.assertTrue(classification["inner_exact"])
                self.assertNotIn("python", str(classification))

    def test_observed_quoted_system_powershell_wrapper_matches(self):
        value = f"{OBSERVED_SYSTEM_POWERSHELL} -Command 'python --version'"
        # The acceptance report recorded exactly 92 UTF-8 bytes for the real
        # command field; this is that string.
        self.assertEqual(92, len(value.encode("utf-8")))
        self.assertTrue(command_match.matches(value, "python --version"))
        classification = command_match.classify(value, "python --version")
        self.assertEqual(classification["kind"], "system_powershell_wrapper")
        self.assertTrue(classification["inner_exact"])
        self.assertEqual(92, classification["length"])
        self.assertNotIn("python", str(classification))
        self.assertNotIn("C:\\", str(classification))
        # A doubly escaped path resolves to the same fixed executable.
        self.assertTrue(command_match.matches(
            value.replace("\\\\", "\\\\\\\\"), "python --version"))
        # Single backslashes inside the quotes are the same Windows path.
        self.assertTrue(command_match.matches(
            f'"{SYSTEM_POWERSHELL}" -Command \'python --version\'',
            "python --version"))

    def test_observed_system_powershell_unquoted_wrapper_matches(self):
        # Real DEV-008 verification ran this exact shape; the inner command is
        # unquoted and consists only of shell-safe characters.
        value = f"{SYSTEM_POWERSHELL} -Command python --version"
        self.assertTrue(command_match.matches(value, "python --version"))
        classification = command_match.classify(value, "python --version")
        self.assertEqual(classification["kind"], "system_powershell_wrapper")
        self.assertTrue(classification["inner_exact"])
        self.assertNotIn("python", str(classification))

    def test_escaped_double_quotes_match(self):
        expected = 'python -c "print(1)"'
        value = f'{QUOTED} -Command "python -c \\"print(1)\\""'
        self.assertTrue(command_match.matches(value, expected))

    def test_extra_commands_flags_and_shells_are_rejected(self):
        expected = "python --version"
        rejected = (
            f"{QUOTED} -Command 'python --version; calc'",
            f"{QUOTED} -Command 'python --version' extra",
            f"{QUOTED} -NoProfile -Command 'python --version' extra",
            f"{QUOTED} -Command 'python --version'; calc",
            f"{QUOTED} -File 'python --version'",
            f"{QUOTED} -EncodedCommand 'python --version'",
            f"{QUOTED} -c 'python --version'",
            f"cmd /c {QUOTED} -Command 'python --version'",
            f"{QUOTED} -Command python --version",
            f"{QUOTED}-Command 'python --version'",
            f"{QUOTED} -Command 'python --version'\n",
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(command_match.matches(value, expected))
                self.assertEqual(
                    command_match.classify(value, expected)["kind"],
                    "unrecognized")

    def test_other_executables_and_paths_are_rejected(self):
        expected = "python --version"
        rejected = (
            f'"pwsh.exe" -Command \'{expected}\'',
            f'"C:\\tools\\pwsh.exe" -Command \'{expected}\'',
            f'"\\\\server\\share\\pwsh.exe" -Command \'{expected}\'',
            f'"C:\\Users\\probe\\.cache\\codex-runtimes\\x\\pwsh.exe" '
            f"-Command '{expected}'",
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(command_match.matches(value, expected))

    def test_quoted_system_powershell_boundaries_are_rejected(self):
        expected = "python --version"
        quoted = OBSERVED_SYSTEM_POWERSHELL
        rejected = (
            f"{quoted} -NoProfile -Command '{expected}'",
            f"{quoted} -Command 'python --version; calc'",
            f"{quoted} -Command '{expected}' extra",
            f"{quoted} -Command \"{expected}\"",
            f"{quoted} -Command {expected}",
            f"{quoted} -Command '{expected}' ",
            f"{quoted} -File '{expected}'",
            f"{quoted} -EncodedCommand '{expected}'",
            f"{quoted} -Command ''{expected}''",
            # The fixed path must survive resolution; `..` is not the same
            # executable even if it reaches the same file.
            r'"C:\\windows\\System32\\..\\WindowsPowerShell\\v1.0'
            f'\\\\powershell.exe" -Command \'{expected}\'',
            r'"D:\\windows\\System32\\WindowsPowerShell\\v1.0'
            f'\\\\powershell.exe" -Command \'{expected}\'',
            r'"C:\\windows\\System32\\WindowsPowerShell\\v1.0'
            f'\\\\pwsh.exe" -Command \'{expected}\'',
            r'"C:\\windows\\System32\\WindowsPowerShell\\v1.0'
            f'\\\\powershell.exe.bak" -Command \'{expected}\'',
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(command_match.matches(value, expected), value)
                self.assertEqual(
                    command_match.classify(value, expected)["kind"],
                    "unrecognized")

    def test_system_powershell_boundaries_are_rejected(self):
        expected = "python --version"
        rejected = (
            f"{SYSTEM_POWERSHELL} -Command python --version; calc",
            f"{SYSTEM_POWERSHELL} -Command python --version extra",
            f"{SYSTEM_POWERSHELL} -NoProfile -Command python --version",
            f"{SYSTEM_POWERSHELL} -Command 'python --version'",
            f'{SYSTEM_POWERSHELL} -Command "python --version"',
            f"{SYSTEM_POWERSHELL} -Command python --version\n",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\pwsh.exe"
            f" -Command {expected}",
            r"D:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
            f" -Command {expected}",
            r"C:\tools\WindowsPowerShell\v1.0\powershell.exe"
            f" -Command {expected}",
            r"\\server\share\powershell.exe -Command " + expected,
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(command_match.matches(value, expected),
                                 value)
        self.assertFalse(command_match.matches(
            f"{SYSTEM_POWERSHELL} -Command python --version;",
            "python --version;"))
        self.assertFalse(command_match.matches(
            f"{SYSTEM_POWERSHELL} -Command python $env:X",
            "python $env:X"))

    def test_quotes_and_expansion_that_change_execution_are_rejected(self):
        self.assertFalse(command_match.matches(
            f"{QUOTED} -Command 'python -c ''x'''",
            "python -c 'x'"))
        self.assertFalse(command_match.matches(
            f'{QUOTED} -Command "python -c $env:String"',
            "python -c $env:String"))
        self.assertFalse(command_match.matches(
            f'{QUOTED} -Command "python -c `x`"',
            "python -c `x`"))
        self.assertFalse(command_match.matches(
            f'{QUOTED} -Command "python -c \\\\"',
            "python -c \\"))

    def test_invalid_values_do_not_match(self):
        for value in (None, "", 1, "python --version\x00"):
            with self.subTest(value=value):
                self.assertFalse(command_match.matches(
                    value, "python --version"))
        self.assertFalse(command_match.matches("python --version", None))


if __name__ == "__main__":
    unittest.main()
