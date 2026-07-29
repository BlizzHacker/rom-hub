"""The host buffers every response body on a plugin's behalf, so the body
size is a host memory budget, not a plugin concern.

This does not need a hostile plugin to go wrong: `archive-org` legitimately
declares `*.archive.org`, and `ia801504.us.archive.org` serves multi-GB ROM
archives. One `ctx.http.get` of one of those costs roughly 3x the body size in
host RAM before anything is written to a pipe.
"""

import httpx
import pytest

from romm_hub.broker.fetcher import HttpxFetcher, ResponseTooLarge

ONE_MIB = 1024 * 1024
CAP = 2 * ONE_MIB


class CountingStream(httpx.SyncByteStream):
    """Yields `count` chunks, recording how many bytes were actually pulled."""

    def __init__(self, chunk: bytes, count: int):
        self._chunk = chunk
        self._count = count
        self.yielded = 0

    def __iter__(self):
        for _ in range(self._count):
            self.yielded += len(self._chunk)
            yield self._chunk


def _fetcher(handler) -> HttpxFetcher:
    return HttpxFetcher(
        max_response_bytes=CAP, transport=httpx.MockTransport(handler)
    )


def test_a_response_within_the_budget_is_returned_whole():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello")

    fetcher = _fetcher(handler)
    try:
        assert fetcher.get("https://allowed.example/small", {}) == (200, "hello")
    finally:
        fetcher.close()


def test_a_huge_response_body_is_not_buffered_whole():
    stream = CountingStream(b"x" * (ONE_MIB // 4), 64)  # 16 MiB available

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    fetcher = _fetcher(handler)
    try:
        with pytest.raises(ResponseTooLarge, match="exceeded"):
            fetcher.get("https://allowed.example/big.zip", {})
    finally:
        fetcher.close()
    # Whatever the server is willing to send, the host stops at its budget.
    assert stream.yielded <= CAP + ONE_MIB, (
        f"host pulled {stream.yielded} bytes for a {CAP}-byte budget"
    )


def test_an_oversize_content_length_is_refused_before_the_body_is_pulled():
    stream = CountingStream(b"x" * ONE_MIB, 300)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Length": str(300 * ONE_MIB)}, stream=stream
        )

    fetcher = _fetcher(handler)
    try:
        with pytest.raises(ResponseTooLarge, match="exceeded"):
            fetcher.get("https://allowed.example/300mb.zip", {})
    finally:
        fetcher.close()
    assert stream.yielded == 0, "a declared oversize length should cost nothing"


def test_the_declared_charset_is_honoured():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=iso-8859-1"},
            content="café".encode("iso-8859-1"),
        )

    fetcher = _fetcher(handler)
    try:
        assert fetcher.get("https://allowed.example/x", {})[1] == "café"
    finally:
        fetcher.close()


def test_a_cookie_from_one_request_never_rides_along_on_the_next():
    """One fetcher serves every plugin in a fan-out; its jar must not.

    Two plugins allowed on the same host would otherwise share session state.
    """
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        return httpx.Response(
            200, headers={"Set-Cookie": "sess=plugin-A-secret"}, text="ok"
        )

    fetcher = _fetcher(handler)
    try:
        fetcher.get("https://allowed.example/a", {})
        fetcher.get("https://allowed.example/b", {})
    finally:
        fetcher.close()
    assert seen == [None, None], f"cookie leaked between requests: {seen}"


def test_an_unknown_charset_falls_back_rather_than_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=not-a-real-charset"},
            content=b"plain ascii",
        )

    fetcher = _fetcher(handler)
    try:
        assert fetcher.get("https://allowed.example/x", {})[1] == "plain ascii"
    finally:
        fetcher.close()
