"""One-shot HTTPS pairing loopback fixture; never a deployable Host server."""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import ssl
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from probe_tls_identity import (PinMismatch, ProbeError, ROOT, certificate_der,
                                generate_certificate, open_pinned, spki_sha256)

sys.path.insert(0, str(ROOT / "host/src"))
from runvori.infrastructure.pairing_journal import PairingError, PairingJournal


HOST_ID = "host_" + "1" * 32
EXCHANGE_PATH = "/v1/pairings/exchange"


class PairingProbeServer(HTTPServer):
    def __init__(self, certificate: Path, key: Path, database: Path):
        self.database = database
        self.request_count = 0
        self.drop_next_response = False
        super().__init__(("127.0.0.1", 0), PairingProbeHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certificate), str(key))
        self.socket = context.wrap_socket(self.socket, server_side=True)


class PairingProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return  # The request body and credentials must not enter test logs.

    def do_POST(self):
        self.server.request_count += 1
        self.connection.settimeout(5)
        if self.path != EXCHANGE_PATH or self.headers.get("Content-Type") != "application/json":
            self._reply(404, {"code": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 1 <= length <= 4096:
                raise ValueError("size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or not isinstance(body.get("pairing_secret"), str):
                raise ValueError("shape")
            secret = base64.b64decode(body["pairing_secret"], validate=True)
        except (ValueError, TypeError, binascii.Error, TimeoutError):
            self._reply(400, {"code": "invalid_request"})
            return
        with PairingJournal(self.server.database) as journal:
            try:
                device_id, token = journal.exchange(secret, host_id=HOST_ID)
            except PairingError:
                self._reply(409, {"code": "pairing_unavailable"})
                return
        if self.server.drop_next_response:
            self.server.drop_next_response = False
            self.close_connection = True
            return  # Commit happened; deliberately lose this response.
        self._reply(200, {"device_id": device_id,
                          "device_token": base64.b64encode(token).decode("ascii"),
                          "host_id": HOST_ID})

    def _reply(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True


def post_pinned(port: int, certificate: Path, pin: str, secret: bytes) -> tuple[int, dict]:
    body = json.dumps({"pairing_secret": base64.b64encode(secret).decode("ascii"),
                       "device_name": "test-phone"}, separators=(",", ":")).encode("utf-8")
    request = (f"POST {EXCHANGE_PATH} HTTP/1.1\r\nHost: localhost\r\n"
               "Content-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode("ascii") + body
    with open_pinned(port, certificate, pin) as secure:
        secure.sendall(request)
        response = http.client.HTTPResponse(secure)
        response.begin()
        return response.status, json.loads(response.read())


def run_probe() -> dict[str, str]:
    (ROOT / ".dev").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="https-pairing-", dir=ROOT / ".dev") as temp:
        folder = Path(temp)
        certificate, key = generate_certificate(folder, "pairing")
        pin = spki_sha256(certificate_der(certificate))
        database = folder / "pairing.db"
        with PairingJournal(database) as journal:
            secret, _expiry = journal.issue_local_secret(host_id=HOST_ID)

        server = PairingProbeServer(certificate, key, database)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                                  daemon=True)
        worker.start()
        port = server.server_address[1]
        try:
            try:
                post_pinned(port, certificate, "0" * 64, secret)
            except PinMismatch:
                pass
            else:
                raise ProbeError("wrong_pin_sent_request")
            if server.request_count != 0:
                raise ProbeError("request_sent_before_pin_check")

            server.drop_next_response = True
            try:
                post_pinned(port, certificate, pin, secret)
            except http.client.RemoteDisconnected:
                pass
            else:
                raise ProbeError("response_drop_not_observed")
            if server.request_count != 1:
                raise ProbeError("first_exchange_not_received")

            status, replay = post_pinned(port, certificate, pin, secret)
            if status != 409 or replay != {"code": "pairing_unavailable"}:
                raise ProbeError("secret_replayed_after_lost_response")

            with PairingJournal(database) as journal:
                journal.revoke_local_device()
                replacement, _expiry = journal.issue_local_secret(host_id=HOST_ID)
            status, paired = post_pinned(port, certificate, pin, replacement)
            if status != 200 or paired.get("host_id") != HOST_ID:
                raise ProbeError("local_repairing_failed")
            with PairingJournal(database) as journal:
                token = base64.b64decode(paired["device_token"], validate=True)
                if not journal.authenticates(token, host_id=HOST_ID):
                    raise ProbeError("issued_token_not_active")
            if server.request_count != 3:
                raise ProbeError("unexpected_request_count")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            if worker.is_alive():
                raise ProbeError("https_probe_shutdown_timeout")

    return {"status": "passed", "wrong_pin": "no_http_request",
            "lost_response": "committed", "replay": "rejected",
            "local_repairing": "accepted", "transport": "https_loopback"}


if __name__ == "__main__":
    try:
        print(json.dumps(run_probe(), sort_keys=True))
    except (OSError, ValueError, ProbeError, ssl.SSLError,
            http.client.HTTPException) as error:
        code = str(error) if isinstance(error, ProbeError) else type(error).__name__
        print(json.dumps({"status": "failed", "code": code}))
        raise SystemExit(1)
