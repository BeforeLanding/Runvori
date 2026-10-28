"""Bounded standard-library HTTPS listener for the Runvori v1 API.

One process, one bounded worker pool, TLS mandatory, and no direct database
access: every request is mapped to a mailbox command executed by the Host's
SQLite owner thread. Request logging carries no path, body or credential.
"""

from __future__ import annotations

import json
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from runvori.api.auth import RateLimiter, error_body
from runvori.api.handlers import (
    MAX_BODY_BYTES, MAX_PAGE_BYTES, ApiError, ApiHandlers, PROTOCOL_VERSION,
)
from runvori.api.tls import TlsMaterial
from runvori.application.command_mailbox import HostCommandMailbox
from runvori.application.host_device_access import HostDeviceAccess


MAX_CONNECTIONS = 16
CONNECTION_TIMEOUT = 30


class ApiServerError(ValueError):
    """A safe refusal code without request content or credentials."""


@dataclass(frozen=True)
class ApiConfig:
    listen_host: str
    listen_port: int
    tls: TlsMaterial
    host_name: str = "Runvori Host"
    clock: callable | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ApiServer:
    """Own one listening socket; start() and stop() are explicit."""

    def __init__(self, config: ApiConfig, device_access: HostDeviceAccess,
                 mailbox: HostCommandMailbox, *,
                 rate_limiter: RateLimiter | None = None,
                 handlers: ApiHandlers | None = None):
        if (not isinstance(config, ApiConfig)
                or not isinstance(device_access, HostDeviceAccess)
                or not isinstance(mailbox, HostCommandMailbox)):
            raise ApiServerError("invalid_api_config")
        self.config = config
        self.device_access = device_access
        self.mailbox = mailbox
        self.reads = device_access.reads
        self.handlers = handlers or ApiHandlers(
            host_name=config.host_name, device_access=device_access,
            mailbox=mailbox, reads=device_access.reads,
            limiter=rate_limiter or RateLimiter(),
            now=config.clock or utc_now)
        self._server: _ThreadedHttps | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> dict:
        if self._server is not None:
            raise ApiServerError("api_already_started")
        try:
            httpd = _ThreadedHttps(
                (self.config.listen_host, self.config.listen_port),
                _Handler, self)
        except OSError as error:
            raise ApiServerError("api_bind_failed") from error
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(self.config.tls.certificate),
                                    str(self.config.tls.key))
            # The listening socket stays plain: each accepted connection is
            # wrapped on its own (see _ThreadedHttps.get_request), so one
            # slow handshake cannot stall the accept loop or another client.
            httpd.ssl_context = context
            httpd.request_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        except (ssl.SSLError, OSError) as error:
            httpd.server_close()
            raise ApiServerError("api_tls_failed") from error
        self._server = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
            name="runvori-api", daemon=True)
        self._thread.start()
        host, port = httpd.server_address[:2]
        return {"listen_host": host, "listen_port": port,
                "spki_sha256": self.config.tls.spki_sha256}

    @property
    def running(self) -> bool:
        return self._server is not None and not self._stopped.is_set()

    def stop(self) -> None:
        """Stop accepting, cancel in-flight reads, then drain handlers."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        httpd, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self.reads.cancel_all()
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=10)


class _ThreadedHttps(ThreadingHTTPServer):
    """TLS terminates in the accept loop, never in a worker thread.

    ``ThreadingHTTPServer`` hands the *accepted socket* to a worker and wraps
    it there. With TLS that lets a worker begin reading a stream while the
    accept loop is still returning the next connection, and two handshakes can
    interleave on one socket pair; the client then sees EOF and the server
    logs ``SSLV3_ALERT_CERTIFICATE_UNKNOWN``. Performing the handshake in
    ``get_request`` (the accept thread) keeps one TLS stream per connection,
    and a failed handshake is dropped there instead of becoming a half-open
    request thread.
    """

    daemon_threads = True
    allow_reuse_address = True
    request_slots: threading.BoundedSemaphore | None = None

    ssl_context: ssl.SSLContext | None = None

    def __init__(self, address, handler, api):
        self.api = api
        super().__init__(address, handler)

    def get_request(self):
        raw, address = super().get_request()
        try:
            raw.settimeout(CONNECTION_TIMEOUT)
            secure = self.ssl_context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            # A client that cannot complete the handshake saw no HTTP response
            # and wrote no request; closing here is the whole contract.
            try:
                raw.close()
            except OSError:
                pass
            raise
        return secure, address

    def process_request(self, request, client_address):
        # Bound concurrent connections instead of unbounded thread creation.
        if self.request_slots is not None and not self.request_slots.acquire(
                blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            if self.request_slots is not None:
                self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self.request_slots is not None:
                self.request_slots.release()

    def handle_error(self, request, client_address):
        """Never print a traceback that could contain request bytes."""
        return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RunvoriHost"
    sys_version = ""
    timeout = CONNECTION_TIMEOUT

    def log_message(self, _format, *args):
        """Never log credentials, bodies or local paths."""
        return

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        self.close_connection = True
        server: ApiServer = self.server.api
        parsed = urlsplit(self.path)
        extra_headers: dict = {}
        try:
            if parsed.path == "/v1/pairings/exchange":
                if method != "POST":
                    raise ApiError("invalid_request", status=405)
                status, body, extra_headers = server.handlers.exchange(
                    self._read_body(), source=self.client_address[0])
            else:
                server.handlers.require_protocol(
                    self.headers.get("X-Runvori-Protocol"))
                device_id, token = server.handlers.authenticate(
                    self.headers.get("Authorization"))
                request_body = self._read_body() if method == "POST" else {}
                status, body, extra_headers = server.handlers.dispatch(
                    device_id, token, method, parsed.path, parsed.query,
                    request_body)
        except ApiError as error:
            status, body = error.status, error_body(error.code)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            status, body = 500, error_body("internal_error")
        self._respond(status, body, extra_headers)

    def _read_body(self) -> dict:
        length = self.headers.get("Content-Length")
        if length is None:
            return {}
        try:
            size = int(length)
        except ValueError as error:
            raise ApiError("invalid_request") from error
        if size < 0:
            raise ApiError("invalid_request")
        if size > MAX_BODY_BYTES:
            raise ApiError("payload_too_large")
        raw = self.rfile.read(size) if size else b""
        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise ApiError("invalid_request") from error
        if not isinstance(decoded, dict):
            raise ApiError("invalid_request")
        return decoded

    def _respond(self, status: int, body,
                 extra_headers: dict | None = None) -> None:
        if isinstance(body, (bytes, bytearray)):
            encoded = bytes(body)
            content_type = "application/octet-stream"
            if len(encoded) > MAX_PAGE_BYTES:
                status = 500
                encoded = json.dumps(error_body("internal_error")).encode()
                content_type = "application/json"
        else:
            content_type = "application/json"
            try:
                encoded = json.dumps(body, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError):
                status = 500
                encoded = json.dumps(error_body("internal_error")).encode()
            if len(encoded) > MAX_PAGE_BYTES:
                status = 500
                encoded = json.dumps(error_body("internal_error")).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Runvori-Protocol", PROTOCOL_VERSION)
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            return
