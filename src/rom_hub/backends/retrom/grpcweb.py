"""Unary gRPC-Web over HTTP/1.1, on the one `httpx.Client` the Hub already has.

## Why gRPC-Web and not gRPC

Retrom's writes are not reachable over REST. `packages/rest-service/src/`
mounts exactly three routers -- `/rest/file/{id}` and `/rest/game/{id}`
(both `get`), and `/rest/public/{*tail}` (a `ServeDir`) -- so there is no
REST route that lists games, resolves a platform, or writes metadata. Those
live on `GameService`, `PlatformService`, `MetadataService` and
`LibraryService` in `packages/grpc-service`.

Retrom serves all three of its services on **one** port and picks between
them per request (`packages/service/src/lib.rs`):

    let is_grpc = req.headers().get(CONTENT_TYPE)
        .filter(|ct| ct.starts_with(b"application/grpc")).is_some();
    ...
    if is_grpc { grpc_service } else if path.starts_with("/dav") { webdav }
    else { rest_service }

and the gRPC router is wrapped in `tonic_web::GrpcWebLayer`. So
`application/grpc-web+proto` is routed to the same handlers as native gRPC,
is what Retrom's own web client uses, and -- unlike native gRPC -- needs no
HTTP/2, no ALPN, and no second networking library. The Hub keeps one HTTP
stack, and the backend inherits its timeouts and transport injection for
free.

## The framing

A unary call is a POST to `/<package>.<Service>/<Method>` whose body is one
length-prefixed frame:

    [1 byte flags][4 bytes big-endian length][message]

The response body is the same, followed by a frame whose flag has bit 0x80
set carrying the trailers as HTTP-header text. Measured against Retrom
0.8.4:

    POST /retrom.LibraryService/UpdateLibrary
    -> 200 application/grpc-web+proto
       00 00000026 0a24 35343965...      (the response message)
       80 0000000f "grpc-status:0\r\n"   (the trailers)

**HTTP 200 is not success.** gRPC carries its status in the trailers, so a
call that failed on the server still answers `200 OK` with a body. Reading
only the status line would report a refusal as a successful call returning
an empty message -- which for `GetGames` is indistinguishable from an empty
library, and would let an import "confirm" against a listing that was never
returned. `unary()` therefore refuses to return until it has seen
`grpc-status: 0`, and treats a *missing* status as an error too.
"""

from __future__ import annotations

import httpx

from rom_hub.backends.base import BackendError

#: What tonic sends and accepts. The `+proto` suffix matters: the base
#: `application/grpc-web` type means the same framing but leaves the
#: message encoding unstated, and tonic-web's own client sends `+proto`.
CONTENT_TYPE = "application/grpc-web+proto"

#: Set on the trailer frame's flag byte. Bit 0 (0x01) would mean the frame
#: is compressed, which this client never sends and never asks for.
_TRAILER_FLAG = 0x80

_FRAME_HEADER = 5

#: The canonical gRPC status names, so a failure says "NOT_FOUND" rather
#: than "5". Straight from the gRPC status-code list.
STATUS_NAMES = {
    0: "OK",
    1: "CANCELLED",
    2: "UNKNOWN",
    3: "INVALID_ARGUMENT",
    4: "DEADLINE_EXCEEDED",
    5: "NOT_FOUND",
    6: "ALREADY_EXISTS",
    7: "PERMISSION_DENIED",
    8: "RESOURCE_EXHAUSTED",
    9: "FAILED_PRECONDITION",
    10: "ABORTED",
    11: "OUT_OF_RANGE",
    12: "UNIMPLEMENTED",
    13: "INTERNAL",
    14: "UNAVAILABLE",
    15: "DATA_LOSS",
    16: "UNAUTHENTICATED",
}


class GrpcError(BackendError):
    """A gRPC call that did not answer `grpc-status: 0`.

    A `BackendError` so `cli.main` catches it with everything else; the
    status name and the server's `grpc-message` are both in the text,
    because "UNIMPLEMENTED" and "INTERNAL" call for very different actions
    from an operator.
    """

    def __init__(self, method: str, status: int, message: str):
        self.method = method
        self.status = status
        self.detail = message
        name = STATUS_NAMES.get(status, str(status))
        suffix = f": {message}" if message else ""
        super().__init__(f"{method} failed with gRPC status {name}{suffix}")


def frame(message: bytes) -> bytes:
    """Wrap one serialized message as a gRPC-Web data frame."""
    return b"\x00" + len(message).to_bytes(4, "big") + message


def iter_frames(body: bytes):
    """Walk the frames in a gRPC-Web response body.

    Stops at the first truncated frame rather than raising: a short read
    is reported by the caller as a missing `grpc-status`, which is the
    accurate description of what went wrong and the same outcome as any
    other incomplete response.
    """
    offset = 0
    while offset + _FRAME_HEADER <= len(body):
        flags = body[offset]
        length = int.from_bytes(body[offset + 1 : offset + _FRAME_HEADER], "big")
        start = offset + _FRAME_HEADER
        end = start + length
        if end > len(body):
            return
        yield flags, body[start:end]
        offset = end


def parse_trailers(raw: bytes) -> dict[str, str]:
    """The trailer frame's payload, which is HTTP-header text.

    Names are lowercased because gRPC-Web trailer names are
    case-insensitive and tonic does not promise a spelling.
    """
    trailers: dict[str, str] = {}
    for line in raw.decode("utf-8", "replace").split("\r\n"):
        if not line.strip():
            continue
        name, _, value = line.partition(":")
        trailers[name.strip().lower()] = value.strip()
    return trailers


class GrpcWebChannel:
    """One Retrom server, addressed by fully-qualified method name."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "content-type": CONTENT_TYPE,
                "accept": CONTENT_TYPE,
                # tonic-web reads this to tell a gRPC-Web client from a
                # browser doing something else with the same content type.
                "x-grpc-web": "1",
            },
        )

    @property
    def base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GrpcWebChannel":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def unary(self, method: str, request: bytes) -> bytes:
        """Call `method` (e.g. `retrom.GameService/GetGames`) once.

        Returns the serialized response message, or raises `GrpcError`.
        An empty body is a legitimate response -- a message all of whose
        fields are at their proto3 defaults encodes to zero bytes -- so
        emptiness is never itself treated as a failure.
        """
        path = "/" + method.lstrip("/")
        try:
            resp = self._client.post(path, content=frame(request))
        except httpx.HTTPError as exc:
            raise GrpcError(method, 14, f"transport error: {exc}") from exc

        if resp.status_code != 200:
            # Not a gRPC status at all: the request never reached a
            # handler. 404 here almost always means the server is older
            # than the RPC being called, so say so.
            hint = (
                " -- the server has no such method; it may be an older "
                "Retrom than this backend was written against"
                if resp.status_code == 404
                else ""
            )
            raise GrpcError(
                method, 2, f"HTTP {resp.status_code} from {path}{hint}"
            )

        message = b""
        trailers: dict[str, str] = {}
        for flags, payload in iter_frames(resp.content):
            if flags & _TRAILER_FLAG:
                trailers.update(parse_trailers(payload))
            elif not message:
                message = payload

        # A "trailers-only" response carries the status in the HTTP headers
        # and has no trailer frame at all. tonic emits one for some early
        # rejections, and missing it would turn a refusal into a silent
        # empty result.
        if "grpc-status" not in trailers:
            for name in ("grpc-status", "grpc-message"):
                value = resp.headers.get(name)
                if value is not None:
                    trailers[name] = value

        raw_status = trailers.get("grpc-status")
        if raw_status is None:
            raise GrpcError(
                method,
                2,
                "the response carried no grpc-status, so the call cannot be "
                "reported as having succeeded (truncated response, or not a "
                "gRPC-Web endpoint)",
            )
        try:
            status = int(raw_status)
        except ValueError:
            raise GrpcError(
                method, 2, f"unparseable grpc-status {raw_status!r}"
            ) from None

        if status != 0:
            raise GrpcError(method, status, trailers.get("grpc-message", ""))
        return message
