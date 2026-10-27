"""Compute the public-key pin shown in pairing information.

The pin comes from the certificate file the Host will actually serve, so a
misconfigured or swapped certificate cannot be presented as a different
identity. There is no fallback that skips verification.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


class TlsIdentityError(RuntimeError):
    """A safe refusal code without certificate contents."""


def openssl_path() -> str:
    executable = shutil.which("openssl")
    if not executable:
        raise TlsIdentityError("openssl_unavailable")
    return executable


def spki_sha256(certificate_pem: Path, *, timeout: float = 15) -> str:
    """SHA-256 of the SubjectPublicKeyInfo DER, as lowercase hex."""
    if not isinstance(certificate_pem, Path) or not certificate_pem.is_file():
        raise TlsIdentityError("tls_certificate_not_found")
    executable = openssl_path()
    public_pem = _openssl(
        executable, ["x509", "-in", str(certificate_pem), "-pubkey", "-noout"],
        timeout)
    public_der = _openssl(
        executable, ["pkey", "-pubin", "-outform", "DER"],
        timeout, input_bytes=public_pem)
    if not public_der:
        raise TlsIdentityError("tls_identity_unreadable")
    return hashlib.sha256(public_der).hexdigest()


def _openssl(executable: str, arguments: list[str], timeout: float,
             input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            [executable, *arguments], input=input_bytes,
            capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise TlsIdentityError("tls_identity_probe_failed") from error
    if result.returncode != 0 or not result.stdout:
        raise TlsIdentityError("tls_identity_probe_failed")
    return result.stdout