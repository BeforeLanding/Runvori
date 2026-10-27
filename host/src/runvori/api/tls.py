"""TLS material for the product listener; never a verification bypass.

Certificates are supplied by the user and identified by their SPKI digest.
Nothing here disables chain verification or trusts an unknown certificate.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path

from runvori.infrastructure.tls_identity import (
    TlsIdentityError, spki_sha256,
)


class TlsConfigError(ValueError):
    """A safe refusal code without certificate or key contents."""


@dataclass(frozen=True)
class TlsMaterial:
    certificate: Path
    key: Path
    spki_sha256: str


def trusted_path(value: Path, *, kind: str) -> Path:
    """Reject symlinks/junctions so a swapped file cannot pass as identity."""
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise TlsConfigError(f"{kind}_not_found")
    for segment in (path, *path.parents):
        if segment.is_symlink() or (hasattr(segment, "is_junction")
                                    and segment.is_junction()):
            raise TlsConfigError(f"unsafe_{kind}")
    return path


def load_tls_material(certificate: Path, key: Path) -> TlsMaterial:
    certificate = trusted_path(certificate, kind="tls_certificate")
    key = trusted_path(key, kind="tls_key")
    try:
        pin = spki_sha256(certificate)
    except TlsIdentityError as error:
        raise TlsConfigError(str(error)) from error
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(certificate), str(key))
    except (ssl.SSLError, OSError) as error:
        raise TlsConfigError("tls_material_invalid") from error
    return TlsMaterial(certificate=certificate, key=key, spki_sha256=pin)