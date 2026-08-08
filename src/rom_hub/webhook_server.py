"""The socket half of the request receiver: one POST route, one worker.

**Why `http.server` and not FastAPI.** This is a sidecar that needs
exactly one route with no path parameters, no content negotiation, no
schema generation and no browsable docs page. FastAPI plus uvicorn is
Starlette plus anyio plus h11 plus click plus a settings stack -- a
dependency tree that would outweigh the entire rest of this project's
runtime requirements, added to a program whose stated security posture is
that plugins are subprocesses with no sockets and no token. The same trade
was made and written down once already for `libtorrent`
(docs/DESIGN.md, "The dependency decision, and why it went the other
way"), and it goes the same way here for the same reason: a
daemon-shaped dependency does not fit a command-shaped program. Validation
is `pydantic`, which is already a dependency, and that is where the
argument for a framework mostly lay.

**Why the handler answers before the work is done.** GG Requestz gives a
webhook receiver five seconds and logs a failure past it. An import is a
multi-gigabyte download followed by a chunked upload. So the handler
claims the request id, drops it on a bounded queue and answers `202
Accepted`; a single worker thread does the search and the import. There is
no configuration in which fulfilment happens inside the request.

**Why the URL is the only gate, and what that is worth.** GG Requestz
posts with `Content-Type: application/json` and nothing else -- no
signature, no bearer token, no shared-secret header -- and that side is
merged upstream. The only channel left is the URL, so the token is a path
segment (or a `?token=` parameter) compared with `hmac.compare_digest`.
That is a **shared secret in a URL, not authentication**: it is logged by
proxies that log URLs, it appears in the sender's configuration in
plaintext, and it does not authenticate the sender, only demonstrate
knowledge of a string. It is defended honestly rather than dressed up:
loopback by default, a minimum length, a refusal to start without one, a
`401` for everything else, and the token kept out of this program's own
log lines -- `BaseHTTPRequestHandler.log_request` would otherwise write
the request line, and the request line contains the secret.
"""

from __future__ import annotations

import hmac
import json
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .webhook import (
    TOKEN_MIN_LENGTH,
    RequestLog,
    WeakToken,
    WebhookPayloadError,
    check_token,
    parse_event,
)

# Re-exported: `TOKEN_MIN_LENGTH` and `WeakToken` are defined next to the
# rest of the receiver's configuration rules in `rom_hub.webhook`, and are
# named here because this is the module whose constructor enforces them.
__all__ = [
    "DRAIN_LIMIT",
    "MAX_BODY_BYTES",
    "QUEUE_MAX",
    "SHUTDOWN_GRACE",
    "TOKEN_MIN_LENGTH",
    "WeakToken",
    "WebhookServer",
]

#: A webhook body is a few hundred bytes. Anything past this is not a game
#: request, and the refusal is made on the declared length before a body
#: byte is read -- the same order `importer.HttpDownloader` checks a
#: download's budget in, and for the same reason.
MAX_BODY_BYTES = 64 * 1024

#: How much of an over-long body to read and throw away *after* refusing
#: it. Closing a socket with the sender's body still unread makes the
#: sender see a connection reset instead of the `413` that explains what
#: happened -- so an ordinarily-too-big body is drained to the end and the
#: refusal arrives intact, while a flood is still cut off here rather than
#: read to completion.
DRAIN_LIMIT = 8 * 1024 * 1024

#: How many claimed requests may wait for the worker. Bounded on purpose:
#: an unbounded queue turns a stuck import into unbounded memory, and a
#: sender that is told `503` retries, while one told `202` for work that
#: was silently dropped never does.
QUEUE_MAX = 32

#: How long `stop()` waits for an in-flight fulfilment before giving up on
#: a clean shutdown. An import can legitimately take much longer than
#: this; the point is not to finish it but to not hang the operator's
#: terminal on Ctrl-C. Whatever was in flight is recorded as interrupted by
#: the next start, which is what `RequestLog.mark_interrupted` is for.
SHUTDOWN_GRACE = 5.0

_JSON = "application/json; charset=utf-8"


class WebhookServer:
    """A loopback HTTP endpoint that queues GG Requestz game requests.

    `fulfil(event, log)` is called on the worker thread, once per newly
    claimed request. It is injected rather than imported so that this
    class has no opinion about plugins, backends or job queues -- the CLI
    builds the real one, and the tests build one that blocks, which is the
    only way to prove the handler does not wait for it.
    """

    def __init__(
        self,
        *,
        token: str,
        log: RequestLog,
        fulfil,
        host: str = "127.0.0.1",
        port: int = 8770,
        path: str = "/requests",
        log_line=None,
    ):
        # Checked here as well as in `cli.webhook_settings`, so a caller
        # that built this class directly cannot bind an endpoint behind a
        # four-character secret. One shared function, so the two cannot
        # disagree about what acceptable means.
        self._token = check_token(token)
        self._log = log
        self._fulfil = fulfil
        self._host = host
        self._requested_port = port
        self.path = _normalise_path(path)
        self._log_line = log_line or (lambda line: None)
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._httpd: ThreadingHTTPServer | None = None
        self._serving: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._stopping = threading.Lock()

    # -- lifecycle -------------------------------------------------------

    @property
    def port(self) -> int:
        """The port actually bound. Not the requested one: a test asks for
        0 and needs to know what it got."""
        if self._httpd is None:
            return self._requested_port
        return self._httpd.server_address[1]

    def url(self, host: str | None = None) -> str:
        """The endpoint to paste into GG Requestz's `REQUEST_WEBHOOK_URL`.

        Built here rather than described in the documentation, because the
        path, the port and the token are three things an operator can get
        wrong independently and a mistake in any of them produces the same
        opaque `401`.
        """
        shown = host or self._host
        if shown in ("0.0.0.0", "::", ""):
            # Never printed as an address: 0.0.0.0 is not somewhere the
            # sender can post to, and printing it as if it were is how an
            # operator ends up debugging the wrong end.
            shown = "<this-host>"
        return f"http://{shown}:{self.port}{self.path}/{self._token}"

    def start(self) -> None:
        stranded = self._log.mark_interrupted()
        for row in stranded:
            self._log_line(
                f"request {row.request_id} was in flight when the receiver "
                f"last stopped; recorded as FAILED"
            )

        handler = _handler_class(self)
        self._httpd = _Server((self._host, self._requested_port), handler)
        if self._host not in ("127.0.0.1", "::1", "localhost"):
            self._log_line(
                f"bound to {self._host}, which is not loopback: the URL secret "
                f"is now reachable from the network and is not authentication. "
                f"Put TLS and an authenticating proxy in front of it."
            )
        self._worker = threading.Thread(
            target=self._drain, name="rom-hub-webhook-worker", daemon=True
        )
        self._worker.start()
        self._serving = threading.Thread(
            # `poll_interval` is how long `shutdown()` can take to be
            # noticed, and the default 0.5s is paid on every stop. This is
            # a sidecar an operator restarts by hand and a suite that
            # starts and stops one per test; a tenth of a second of idle
            # polling is the cheaper side of that trade.
            target=lambda: self._httpd.serve_forever(poll_interval=0.1),
            name="rom-hub-webhook",
            daemon=True,
        )
        self._serving.start()
        self._log_line(f"listening on http://{self._host}:{self.port}{self.path}/***")

    def serve_forever(self) -> None:
        """Start, then block until interrupted. What the CLI calls."""
        self.start()
        try:
            while self._serving is not None and self._serving.is_alive():
                self._serving.join(timeout=0.5)
        except KeyboardInterrupt:
            self._log_line("interrupted; shutting down")
        finally:
            self.stop()

    def stop(self) -> None:
        """Shut down, once, from whichever thread gets here first.

        Two callers is the normal case rather than the odd one:
        `serve_forever` stops in its own `finally`, and the operator (or a
        test) that wants it stopped calls this from another thread. Without
        the lock the two interleave between a `None` check and the call
        after it, which is an `AttributeError` on the way out of a clean
        shutdown.
        """
        with self._stopping:
            httpd, self._httpd = self._httpd, None
            serving, self._serving = self._serving, None
            worker, self._worker = self._worker, None
        if httpd is not None:
            # `shutdown()` blocks until `serve_forever` has returned, so the
            # socket is only closed once nothing is selecting on it.
            httpd.shutdown()
            httpd.server_close()
        if serving is not None and serving is not threading.current_thread():
            serving.join(timeout=SHUTDOWN_GRACE)
        if worker is not None:
            # The sentinel rather than a flag: the worker is parked in
            # `queue.get()` and a flag would not wake it.
            self._queue.put(None)
            worker.join(timeout=SHUTDOWN_GRACE)

    # -- the worker ------------------------------------------------------

    def _drain(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                return
            try:
                self._fulfil(event, self._log)
            except Exception as exc:  # noqa: BLE001 - the worker must survive
                # `webhook.fulfil` already records its own failures, so
                # reaching here means the injected callable itself broke.
                # One dead request must not stop every later one.
                self._log_line(
                    f"request {event.request_id}: fulfilment raised "
                    f"{type(exc).__name__}: {exc}"
                )

    # -- what the handler needs ------------------------------------------

    def authorised(self, path: str) -> bool:
        """Whether `path` carries the token. Constant-time on both compares.

        Both forms are accepted because operators configure this by hand:
        a secret path segment reads better in a URL, and a query parameter
        is what somebody who already has a reverse-proxy route will reach
        for. Neither is compared with `==`; see `_token_matches`.
        """
        parts = urlsplit(path)
        # `or ""` and not `or "/"`, so a receiver configured with
        # ROM_HUB_WEBHOOK_PATH=/ -- whose normalised path *is* the empty
        # string -- still matches its own root for the query form.
        route = parts.path.rstrip("/") or ""
        prefix = f"{self.path}/"
        if route.startswith(prefix) and len(route) > len(prefix):
            return _token_matches(route[len(prefix) :], self._token)
        if route != self.path:
            # Falls through to the same refusal a wrong token gets, so a
            # wrong path and a wrong token are indistinguishable outside.
            return False
        offered = parse_qs(parts.query).get("token", [""])[0]
        return _token_matches(offered, self._token)

    def accept(self, body: bytes) -> tuple[int, dict]:
        """Claim a request and queue it. `(status, response body)`.

        The claim happens here, on the acceptor thread, and not in the
        worker. That is the whole duplicate defence: a re-approval that
        arrives while the first import is still running must see the row
        that import is working on, and a row written by the worker would
        not exist yet.
        """
        event = parse_event(body)
        if not self._log.claim(event):
            return 202, {
                "status": "duplicate",
                "request_id": event.request_id,
                "detail": (
                    "this request_id was already received; nothing was "
                    "imported a second time"
                ),
            }
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Recorded, not swallowed. The row exists (it was just
            # claimed), so leaving it RECEIVED would make a dropped request
            # indistinguishable from one still waiting.
            self._log.finish(
                event.request_id,
                _failed_state(),
                f"the receiver's queue was full ({QUEUE_MAX} requests already "
                f"waiting), so this request was refused rather than dropped. "
                f"Re-approve it once the backlog clears.",
            )
            return 503, {
                "status": "busy",
                "request_id": event.request_id,
                "detail": f"{QUEUE_MAX} requests are already queued",
            }
        return 202, {"status": "accepted", "request_id": event.request_id}

    def note(self, line: str) -> None:
        self._log_line(line)


def _failed_state():
    # Imported lazily through the function to keep the module's import
    # surface to the two names it really needs from `webhook`.
    from .webhook import RequestState

    return RequestState.FAILED


def _token_matches(offered: str, token: str) -> bool:
    """Constant-time comparison of a URL-supplied token against the real one.

    Two things this is not allowed to be. Not `==`, because the token is
    the only gate this endpoint has and `==` returns as soon as two bytes
    differ. And not `hmac.compare_digest` on the strings directly:
    `BaseHTTPRequestHandler` decodes the request line as latin-1, so a
    request for `/requests/caf\xe9` yields a non-ASCII `str`, and
    `compare_digest` raises `TypeError` on those -- which would escape
    `authorised`, bypass the handler's own `try`, and leave the sender with
    a reset connection instead of a `401`. Encoding both sides first is
    what makes every possible path answerable.
    """
    return hmac.compare_digest(
        offered.encode("utf-8", "surrogateescape"),
        token.encode("utf-8", "surrogateescape"),
    )


def _normalise_path(path: str) -> str:
    """One leading slash, no trailing one, so three spellings are one route."""
    cleaned = "/" + (path or "").strip().strip("/")
    return cleaned if cleaned != "/" else ""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # A restarted receiver must be able to rebind immediately; the
    # alternative is an operator who cannot restart their own sidecar for
    # a minute after Ctrl-C.
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        # The default prints a traceback to stderr. A client that hung up
        # mid-response is not a fault of this program's, and a receiver
        # whose log fills with tracebacks is one whose real errors are
        # invisible.
        pass


def _handler_class(server: WebhookServer):
    class _Handler(BaseHTTPRequestHandler):
        # HTTP/1.0, so every response closes the connection. Keep-alive
        # would require this handler to have consumed exactly the whole
        # body on every path including the refusals, and a receiver that
        # desynchronises a reused connection is worse than one that costs
        # a TCP handshake per request. This endpoint sees one request per
        # approval.
        protocol_version = "HTTP/1.0"
        server_version = "rom-hub-webhook"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            """Never the default.

            `log_request` formats the request line, and the request line
            contains the token. This drops it on the floor; the handler
            below logs what happened without the secret in it.
            """

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            self._handle("POST")

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self._handle("GET")

        def do_PUT(self) -> None:  # noqa: N802 - stdlib naming
            self._handle("PUT")

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
            self._handle("DELETE")

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
            self._handle("HEAD")

        def _handle(self, method: str) -> None:
            # The order below is deliberate and the reasons pull against
            # each other.
            #
            # *Authorisation is decided first*, so an unauthorised caller
            # learns nothing about the endpoint -- not whether the path
            # exists, not whether the method was right, not whether the
            # framing was.
            #
            # *But the body is read before anything is written*, whenever
            # its declared length is one this receiver would accept.
            # Answering while the sender is still writing leaves unread
            # bytes in the socket, and closing on those makes the sender
            # see a connection reset instead of the refusal -- which turns
            # a legible `401 the URL did not carry the token` into an
            # unexplained network error in somebody else's log.
            authorised = server.authorised(self.path)
            length, framing = self._declared_length()
            body = b""
            if framing is None:
                try:
                    body = self.rfile.read(length)
                except (OSError, socket.timeout):
                    return  # The sender hung up. Nothing to answer to.

            if not authorised:
                self._answer(
                    401,
                    {
                        "status": "unauthorised",
                        "detail": (
                            "the URL did not carry the configured token; see "
                            "ROM_HUB_WEBHOOK_TOKEN"
                        ),
                    },
                    method,
                    "no token",
                )
                self._drain(length, framing)
                return
            if method != "POST":
                self._answer(
                    405,
                    {
                        "status": "unsupported",
                        "detail": "this endpoint accepts POST only",
                    },
                    method,
                    "wrong method",
                )
                self._drain(length, framing)
                return
            if framing is not None:
                status, detail, why = framing
                self._answer(
                    status, {"status": "invalid", "detail": detail}, method, why
                )
                self._drain(length, framing)
                return

            try:
                status, answer = server.accept(body)
            except WebhookPayloadError as exc:
                self._answer(
                    400,
                    {"status": "invalid", "detail": str(exc)},
                    method,
                    "malformed payload",
                )
                return
            except Exception as exc:  # noqa: BLE001
                # A receiver that dies on one bad request stops fulfilling
                # every later one. 500, logged, still listening.
                self._answer(
                    500,
                    {
                        "status": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    },
                    method,
                    "receiver error",
                )
                return

            self._answer(
                status,
                answer,
                method,
                f"{answer.get('status')} {answer.get('request_id', '')}".strip(),
            )

        def _declared_length(self):
            """`(length, None)` for a body this receiver will read, or
            `(length, (status, detail, why))` for one it refuses to.

            The three refusals are separated from the reading so that the
            reading can happen first for the cases that allow it. A
            `Content-Length` this large is refused on the header alone --
            the same order `importer.HttpDownloader` checks a download's
            budget in, and for the same reason: refusing before a body byte
            is pulled is strictly cheaper than refusing after.
            """
            declared = self.headers.get("Content-Length")
            if declared is None:
                return 0, (
                    411,
                    "a Content-Length is required; this receiver cannot read a "
                    "chunked body",
                    "no content-length",
                )
            try:
                length = int(declared)
            except ValueError:
                return 0, (400, "Content-Length is not a number", "bad content-length")
            if length < 0:
                return 0, (400, "Content-Length is negative", "bad content-length")
            if length > MAX_BODY_BYTES:
                return length, (
                    413,
                    f"body too large: {length} bytes, limit is {MAX_BODY_BYTES}",
                    "oversized",
                )
            return length, None

        def _drain(self, length: int, framing) -> None:
            """Read and discard a refused body, so the refusal gets through.

            Only reached after the response has been written. See
            DRAIN_LIMIT: a body somebody merely got wrong is drained to the
            end, a flood is cut off.
            """
            if framing is None:
                return  # Already read in full.
            try:
                remaining = min(length, DRAIN_LIMIT)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 64 * 1024))
                    if not chunk:
                        return
                    remaining -= len(chunk)
            except (OSError, socket.timeout):
                return

        def _answer(self, status: int, payload: dict, method: str, why: str) -> None:
            # Logged *before* the response is written, for two reasons. A
            # client that hangs up mid-write still gets its decision
            # recorded, and a caller that has already read the response can
            # rely on the log line being there -- written afterwards, the
            # two race, and the loser is whoever is reading the log.
            #
            # The path is never logged, only the route's shape, because the
            # path is where the token lives.
            server.note(f"{method} {server.path}/*** -> {status} ({why})")
            body = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", _JSON)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(body)
            except (OSError, socket.timeout):
                # Client gone. The claim, if there was one, still stands --
                # which is correct: the request was received.
                pass

    return _Handler
