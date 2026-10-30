r"""Conservative equivalence for required-command evidence.

The Windows CLI wraps tool commands in its bundled PowerShell. Verification
must still be evidence-based: only the exact command text or a complete
wrapper shape already observed in real runs is accepted. Everything else is
unmatched, so an unrecognized wrapper leaves the requirement ``unknown``
instead of passing by a loose contains check.

Observed shapes (ERR-007, ERR-016, ERR-038) are:

    "<...>/pwsh.exe" -Command '<exact>'
    "<...>/pwsh.exe" -NoProfile -Command '<exact>'
    "<...>/pwsh.exe" -Command "<exact, quotes escaped>"
    "C:\\windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '<exact>'
    C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command <exact>

The fourth shape is the exact string the real app-server sent in DEV-008
verification (ERR-038): the executable is double-quoted and its backslashes
appear doubled in the serialized command text. Windows collapses repeated
separators, so the quoted form is checked after resolving the executable to
the same fixed system path; only the single-quoted inner command is accepted.

The fifth shape comes from the DEV-008 execution array rather than the Host
field text; it is kept only as a conservative equivalent for an unquoted,
shell-metacharacter-free command.

No other flags, aliases, extra arguments or shell constructs are accepted.
"""

from __future__ import annotations

import re
from pathlib import PureWindowsPath

EXACT = "exact"
PWSH_WRAPPER = "pwsh_wrapper"
SYSTEM_POWERSHELL_WRAPPER = "system_powershell_wrapper"
UNRECOGNIZED = "unrecognized"

# The system Windows PowerShell invocation observed from the real CLI. It has
# no space in the path, which lets its wrapper be split at the first space.
_SYSTEM_POWERSHELL_PARTS = (
    "windows", "system32", "windowspowershell", "v1.0", "powershell.exe")
_SIMPLE_COMMAND = re.compile(r"[A-Za-z0-9 ._:/=\\-]+")


def _control_free(value: str) -> bool:
    return not any(ord(character) < 0x20 or ord(character) == 0x7f
                   for character in value)


def _known_pwsh(executable: str) -> bool:
    """True only for the CLI's bundled runtime, not any pwsh on PATH."""
    try:
        path = PureWindowsPath(executable)
    except (TypeError, ValueError):
        return False
    if path.name.lower() != "pwsh.exe" or not path.is_absolute():
        return False
    parts = [part.lower() for part in path.parts]
    if any(part in ("", "..") for part in parts):
        return False
    if str(path.drive).startswith("\\\\") or path.anchor.startswith("\\\\"):
        return False
    for index in range(len(parts) - 6):
        if parts[index] != ".cache":
            continue
        if (parts[index + 1] == "codex-runtimes"
                and parts[index + 2] not in ("", "..")
                and parts[index + 3] == "dependencies"
                and parts[index + 4] == "native"
                and parts[index + 5] == "powershell"
                and parts[index + 6] == "pwsh.exe"
                and index + 7 == len(parts)):
            return True
    return False


def _split_wrapper(value: str) -> tuple[str, str] | None:
    if not value.startswith('"'):
        return None
    end = value.find('"', 1)
    if end <= 1 or end + 2 > len(value) or value[end + 1] != " ":
        return None
    return value[1:end], value[end + 2:]


def _known_system_powershell(executable: str) -> bool:
    """True only for the Windows system PowerShell path observed in a real run."""
    try:
        path = PureWindowsPath(executable)
    except (TypeError, ValueError):
        return False
    if path.name.lower() != "powershell.exe" or not path.is_absolute():
        return False
    if path.anchor.startswith("\\\\"):
        return False
    parts = [part.lower() for part in path.parts]
    if len(parts) != 6 or parts[0] != "c:\\":
        return False
    return tuple(parts[1:]) == _SYSTEM_POWERSHELL_PARTS


def _split_system_powershell(value: str) -> tuple[str, str] | None:
    end = value.find(" ")
    if end <= 0:
        return None
    return value[:end], value[end + 1:]


def _wrapper_inner(value: str, expected: str) -> tuple[str, str] | None:
    """Return (classification kind, matched exact text) for a known wrapper."""
    if not _control_free(expected):
        return None
    split = _split_wrapper(value)
    if split is not None:
        executable, rest = split
        if not rest or not _control_free(rest):
            return None
        if _known_system_powershell(executable):
            # DEV-008 app-server serialization: the system PowerShell is
            # double-quoted (with doubled backslashes) and the script is
            # single-quoted, so the body is literal. Only that exact form is
            # accepted; no flags, double quotes or extra arguments.
            if ("'" not in expected
                    and rest == "-Command '" + expected + "'"):
                return SYSTEM_POWERSHELL_WRAPPER, expected
            return None
        if not _known_pwsh(executable):
            return None
        # Single-quoted forms: PowerShell treats the body literally. A quote
        # in the expected command would need '' escaping, which was never
        # observed.
        if "'" not in expected:
            for prefix in ("-Command '", "-NoProfile -Command '"):
                if (rest.startswith(prefix) and rest.endswith("'")
                        and len(rest) >= len(prefix) + 1):
                    if rest[len(prefix):-1] == expected:
                        return PWSH_WRAPPER, expected
        # Double-quoted form: $ and backticks would be expanded by PowerShell;
        # a trailing backslash would escape the closing quote. Reject those
        # instead of pretending the executed text is identical.
        if (not any(character in expected for character in ("$", "`"))
                and not expected.endswith("\\")):
            escaped = expected.replace('"', '\\"')
            prefix = '-Command "'
            if (rest.startswith(prefix) and rest.endswith('"')
                    and len(rest) >= len(prefix) + 1
                    and rest[len(prefix):-1] == escaped):
                return PWSH_WRAPPER, expected
        return None

    # The observed system PowerShell form has no quotes around the executable
    # or the command. Accept it only when the exact inner text is a simple
    # command: any shell metacharacter would change what PowerShell executes.
    split = _split_system_powershell(value)
    if split is None:
        return None
    executable, rest = split
    if (not _known_system_powershell(executable)
            or not _SIMPLE_COMMAND.fullmatch(expected)
            or rest != "-Command " + expected):
        return None
    return SYSTEM_POWERSHELL_WRAPPER, expected


def matches(value: object, expected: object) -> bool:
    """Exact text or one complete observed wrapper around it."""
    if (not isinstance(value, str) or not value
            or not isinstance(expected, str) or not expected
            or not _control_free(value)):
        return False
    if value == expected:
        return True
    return _wrapper_inner(value, expected) is not None


def classify(value: object, expected: object) -> dict:
    """Sanitized classification for acceptance reports; never the text."""
    if (not isinstance(value, str) or not value
            or not isinstance(expected, str) or not expected):
        return {"kind": UNRECOGNIZED, "inner_exact": False, "length": None}
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError:
        length = None
    if value == expected:
        return {"kind": EXACT, "inner_exact": True, "length": length}
    wrapper = _wrapper_inner(value, expected)
    if wrapper is not None:
        return {"kind": wrapper[0], "inner_exact": True, "length": length}
    return {"kind": UNRECOGNIZED, "inner_exact": False, "length": length}
