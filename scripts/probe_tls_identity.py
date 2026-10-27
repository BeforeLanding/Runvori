"""M0 loopback TLS identity probe; no pairing secret or product network listener."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPENSSL = shutil.which("openssl")


class ProbeError(RuntimeError):
    pass


class PinMismatch(ProbeError):
    pass


def openssl(*arguments: str, input_bytes: bytes | None = None) -> bytes:
    if OPENSSL is None:
        raise ProbeError("openssl_unavailable")
    result = subprocess.run([OPENSSL, *arguments], input=input_bytes,
                            capture_output=True, timeout=20, check=False)
    if result.returncode != 0:
        raise ProbeError("openssl_command_failed")
    return result.stdout


def spki_sha256(certificate_der: bytes) -> str:
    public_pem = openssl("x509", "-inform", "DER", "-pubkey", "-noout",
                         input_bytes=certificate_der)
    public_der = openssl("pkey", "-pubin", "-outform", "DER", input_bytes=public_pem)
    return hashlib.sha256(public_der).hexdigest()


def certificate_der(certificate_pem: Path) -> bytes:
    return openssl("x509", "-in", str(certificate_pem), "-outform", "DER")


def generate_certificate(folder: Path, name: str,
                         extra_sans: list[str] | None = None) -> tuple[Path, Path]:
    """A throw-away certificate for a loopback Host.

    ``extra_sans`` lets a device acceptance run name the address the device
    actually dials (the emulator reaches the host through `10.0.2.2`); the
    client refuses a certificate that does not name its endpoint, so the SAN
    has to be real rather than bypassed.
    """
    certificate = folder / f"{name}.pem"
    key = folder / f"{name}.key"
    sans = ["DNS:localhost", *(extra_sans or [])]
    openssl("req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-days", "2", "-keyout", str(key), "-out", str(certificate),
            "-subj", f"/CN=Runvori Probe {name}",
            "-addext", "subjectAltName=" + ",".join(sans))
    return certificate, key


def connect_pinned(port: int, trust_bundle: Path, expected_pin: str,
                   *, hostname: str = "localhost") -> None:
    with open_pinned(port, trust_bundle, expected_pin, hostname=hostname) as secure:
        secure.sendall(b"probe")
        # Closing straight after sendall can reset the connection before the
        # peer reads, which looks like a missing pin acceptance. Wait for the
        # peer's close so the probe records delivery, not socket teardown luck.
        secure.settimeout(5)
        try:
            while secure.recv(32):
                pass
        except (TimeoutError, ssl.SSLError):
            raise ProbeError("probe_ack_timeout") from None


def open_pinned(port: int, trust_bundle: Path, expected_pin: str,
                *, hostname: str = "localhost") -> ssl.SSLSocket:
    context = ssl.create_default_context(cafile=str(trust_bundle))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ProbeError("tls_verification_disabled")
    try:
        raw_socket = socket.create_connection(("127.0.0.1", port), timeout=5)
    except PermissionError as error:
        raise ProbeError("loopback_unavailable") from error
    try:
        secure = context.wrap_socket(raw_socket, server_hostname=hostname)
    except BaseException:
        raw_socket.close()
        raise
    try:
        actual_pin = spki_sha256(secure.getpeercert(binary_form=True))
        if not hmac.compare_digest(actual_pin, expected_pin):
            raise PinMismatch("spki_pin_mismatch")
        return secure
    except BaseException:
        secure.close()
        raise


def exchange(certificate: Path, key: Path, action) -> bytes:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(key))
    received: list[bytes] = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(6)

        def server() -> None:
            try:
                connection, _address = listener.accept()
                with connection:
                    connection.settimeout(5)
                    with context.wrap_socket(connection, server_side=True) as secure:
                        received.append(secure.recv(32))
            except (OSError, ssl.SSLError):
                received.append(b"")

        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        try:
            action(listener.getsockname()[1])
        finally:
            worker.join(timeout=6)
            if worker.is_alive():
                raise ProbeError("tls_server_timeout")
    return received[0] if received else b""


def run_probe() -> dict[str, str]:
    (ROOT / ".dev").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tls-probe-", dir=ROOT / ".dev") as temp:
        folder = Path(temp)
        first, first_key = generate_certificate(folder, "first")
        rotated, rotated_key = generate_certificate(folder, "rotated")
        bundle = folder / "trusted.pem"
        bundle.write_bytes(first.read_bytes() + rotated.read_bytes())
        first_pin = spki_sha256(certificate_der(first))
        rotated_pin = spki_sha256(certificate_der(rotated))
        if first_pin == rotated_pin:
            raise ProbeError("rotation_key_not_changed")

        accepted = exchange(first, first_key,
                            lambda port: connect_pinned(port, bundle, first_pin))
        if accepted != b"probe":
            raise ProbeError("matching_identity_not_accepted")

        rotation_received: list[bytes] = []
        def reject_rotation(port: int) -> None:
            try:
                connect_pinned(port, bundle, first_pin)
            except PinMismatch:
                return
            raise ProbeError("rotated_key_accepted_without_repairing")
        rotation_received.append(exchange(rotated, rotated_key, reject_rotation))
        if rotation_received != [b""]:
            raise ProbeError("application_data_sent_before_pin_check")

        def reject_hostname(port: int) -> None:
            try:
                connect_pinned(port, bundle, first_pin, hostname="wrong.example")
            except ssl.SSLCertVerificationError:
                return
            raise ProbeError("wrong_hostname_accepted")
        if exchange(first, first_key, reject_hostname) != b"":
            raise ProbeError("application_data_sent_after_hostname_failure")

        def reject_untrusted(port: int) -> None:
            try:
                connect_pinned(port, rotated, first_pin)
            except ssl.SSLCertVerificationError:
                return
            raise ProbeError("untrusted_certificate_accepted")
        if exchange(first, first_key, reject_untrusted) != b"":
            raise ProbeError("application_data_sent_after_chain_failure")

        repaired = exchange(rotated, rotated_key,
                            lambda port: connect_pinned(port, bundle, rotated_pin))
        if repaired != b"probe":
            raise ProbeError("explicit_new_pin_not_accepted")

    return {"status": "passed", "chain_and_hostname": "required",
            "matching_pin": "accepted", "rotated_pin": "rejected_before_data",
            "wrong_hostname": "rejected", "untrusted_cert": "rejected",
            "explicit_new_pin": "accepted"}


if __name__ == "__main__":
    try:
        print(json.dumps(run_probe(), sort_keys=True))
    except (OSError, subprocess.SubprocessError, ProbeError, ssl.SSLError) as error:
        code = str(error) if isinstance(error, ProbeError) else type(error).__name__
        print(json.dumps({"status": "failed", "code": code}))
        raise SystemExit(1)
