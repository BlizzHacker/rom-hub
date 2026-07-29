"""gRPC-Web framing and, above all, that HTTP 200 is not success.

gRPC reports its status in trailers. A call that the server refused still
answers `200 OK` with a body, so a client that reads the status line alone
turns "PERMISSION_DENIED" into "an empty listing" -- and an empty listing
is indistinguishable from an empty library, which is how a failed import
gets confirmed against a listing that was never returned.

Every HTTP call here is mocked with `httpx.MockTransport`. No test
requires a live Retrom.
"""

from __future__ import annotations

import httpx
import pytest

from rom_hub.backends.retrom.grpcweb import (
    CONTENT_TYPE,
    GrpcError,
    GrpcWebChannel,
    frame,
    iter_frames,
    parse_trailers,
)

# Captured verbatim from Retrom 0.8.4: an empty response message followed
# by a trailer frame carrying `grpc-status:0`.
EMPTY_OK_BODY = bytes.fromhex("0000000000800000000f677270632d7374617475733a300d0a")


def _trailers(status: int, message: str = "") -> bytes:
    text = f"grpc-status:{status}\r\n"
    if message:
        text += f"grpc-message:{message}\r\n"
    raw = text.encode()
    return b"\x80" + len(raw).to_bytes(4, "big") + raw


def _channel(handler, **kwargs) -> GrpcWebChannel:
    return GrpcWebChannel(
        "http://retrom.example:5101",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# -- framing ---------------------------------------------------------------


def test_a_request_is_one_length_prefixed_frame():
    assert frame(b"abc") == b"\x00\x00\x00\x00\x03abc"
    assert frame(b"") == b"\x00\x00\x00\x00\x00"


def test_a_captured_response_body_splits_into_a_message_and_trailers():
    frames = list(iter_frames(EMPTY_OK_BODY))
    assert frames == [(0x00, b""), (0x80, b"grpc-status:0\r\n")]


def test_a_truncated_frame_ends_the_walk_rather_than_raising():
    """Reported upward as a missing grpc-status, which is what it is."""
    assert list(iter_frames(b"\x00\x00\x00\x00\x10short")) == []


def test_trailers_are_parsed_case_insensitively_and_without_spaces():
    parsed = parse_trailers(b"Grpc-Status: 7\r\ngrpc-message:nope\r\n\r\n")
    assert parsed == {"grpc-status": "7", "grpc-message": "nope"}


# -- the call --------------------------------------------------------------


def test_a_unary_call_posts_the_framed_request_to_the_method_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read()
        seen["content_type"] = request.headers["content-type"]
        seen["grpc_web"] = request.headers["x-grpc-web"]
        return httpx.Response(200, content=b"\x00\x00\x00\x00\x02hi" + _trailers(0))

    with _channel(handler) as channel:
        assert channel.unary("retrom.GameService/GetGames", b"\x08\x01") == b"hi"

    assert seen["path"] == "/retrom.GameService/GetGames"
    assert seen["body"] == b"\x00\x00\x00\x00\x02\x08\x01"
    assert seen["content_type"] == CONTENT_TYPE
    assert seen["grpc_web"] == "1"


def test_an_empty_response_message_is_a_success_not_a_failure():
    """Every field at its proto3 default encodes to zero bytes."""

    def handler(request):
        return httpx.Response(200, content=EMPTY_OK_BODY)

    with _channel(handler) as channel:
        assert channel.unary("retrom.ServerService/GetServerInfo", b"") == b""


def test_a_non_zero_grpc_status_raises_even_though_the_http_status_is_200():
    def handler(request):
        return httpx.Response(
            200, content=b"\x00\x00\x00\x00\x00" + _trailers(12, "no such method")
        )

    with _channel(handler) as channel:
        with pytest.raises(GrpcError) as exc:
            channel.unary("retrom.GameService/GetGames", b"")

    assert exc.value.status == 12
    assert "UNIMPLEMENTED" in str(exc.value)
    assert "no such method" in str(exc.value)


def test_a_trailers_only_response_is_read_from_the_http_headers():
    """tonic answers some early rejections with no trailer frame at all."""

    def handler(request):
        return httpx.Response(
            200,
            content=b"",
            headers={"grpc-status": "3", "grpc-message": "bad argument"},
        )

    with _channel(handler) as channel:
        with pytest.raises(GrpcError) as exc:
            channel.unary("retrom.GameService/GetGames", b"")

    assert "INVALID_ARGUMENT" in str(exc.value)


def test_a_response_with_no_grpc_status_anywhere_is_a_failure():
    """Silence is not success: a truncated body would otherwise read as an
    empty result, and an empty result is a legitimate value."""

    def handler(request):
        return httpx.Response(200, content=b"\x00\x00\x00\x00\x02hi")

    with _channel(handler) as channel:
        with pytest.raises(GrpcError) as exc:
            channel.unary("retrom.GameService/GetGames", b"")

    assert "no grpc-status" in str(exc.value)


def test_an_unparseable_grpc_status_is_a_failure():
    def handler(request):
        return httpx.Response(
            200, content=b"\x00\x00\x00\x00\x00\x80\x00\x00\x00\x0cgrpc-status:x"
        )

    with _channel(handler) as channel:
        with pytest.raises(GrpcError):
            channel.unary("retrom.GameService/GetGames", b"")


def test_a_404_says_the_server_may_predate_the_method():
    def handler(request):
        return httpx.Response(404, text="not found")

    with _channel(handler) as channel:
        with pytest.raises(GrpcError) as exc:
            channel.unary("retrom.GameService/GetGames", b"")

    assert "older" in str(exc.value)


def test_a_transport_error_becomes_unavailable_not_a_traceback():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with _channel(handler) as channel:
        with pytest.raises(GrpcError) as exc:
            channel.unary("retrom.ServerService/GetServerInfo", b"")

    assert exc.value.status == 14
    assert "UNAVAILABLE" in str(exc.value)
