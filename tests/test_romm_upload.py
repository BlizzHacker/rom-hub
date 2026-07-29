"""Chunked upload orchestration for RomM's /api/roms/upload/* endpoints.

No test here may require a live RomM: every HTTP call is mocked via
httpx.MockTransport, exactly like tests/test_romm_client.py.

The mock enforces RomM's actual chunk-sizing contract (mirrored from
RomM's own backend/endpoints/roms/upload.py::_expected_chunk_size) rather
than accepting any chunk length -- a chunk-size bug is otherwise
invisible to a mock that rubber-stamps whatever bytes it's given.
"""

import hashlib
import os

import httpx
import pytest

from romm_hub.romm.client import RommClient, RommError
from romm_hub.romm.upload import DEFAULT_CHUNK_SIZE, upload_file


def _expected_chunk_size(total_size: int, total_chunks: int, chunk_index: int) -> int:
    """Mirrors RomM's own backend/endpoints/roms/upload.py::_expected_chunk_size.
    Every chunk but the last must be exactly ceil(total_size/total_chunks);
    the last is whatever remains."""
    chunk_size = (total_size + total_chunks - 1) // total_chunks
    if chunk_index < total_chunks - 1:
        return chunk_size
    return total_size - (chunk_size * (total_chunks - 1))


def _handler(calls, upload_id="up-1", fail_on_chunk_index=None):
    state: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if path == "/api/roms/upload/start" and request.method == "POST":
            state["total_size"] = int(request.headers["x-upload-total-size"])
            state["total_chunks"] = int(request.headers["x-upload-total-chunks"])
            return httpx.Response(201, json={"upload_id": upload_id})
        if path == f"/api/roms/upload/{upload_id}" and request.method == "PUT":
            index = int(request.headers["x-chunk-index"])
            if fail_on_chunk_index is not None and index == fail_on_chunk_index:
                return httpx.Response(500, text="chunk rejected")
            body = request.read()
            expected = _expected_chunk_size(
                state["total_size"], state["total_chunks"], index
            )
            if len(body) != expected:
                # This is what a contract-honest server does when a
                # chunk's length doesn't match its own derived
                # expectation -- the mock must enforce this, never
                # rubber-stamp whatever size the client happened to send.
                return httpx.Response(
                    400,
                    json={
                        "detail": (
                            f"chunk {index} wrong size: expected {expected}, "
                            f"got {len(body)}"
                        )
                    },
                )
            return httpx.Response(
                200, json={"received": index + 1, "total": state["total_chunks"]}
            )
        if path == f"/api/roms/upload/{upload_id}/complete" and request.method == "POST":
            return httpx.Response(201, json={"id": 999, "rom_id": 999})
        if path == f"/api/roms/upload/{upload_id}/cancel" and request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": f"unhandled path {path}"})

    return handler


def _client(calls, **kwargs):
    return RommClient(
        "https://romm.example",
        "user",
        "pw",
        transport=httpx.MockTransport(_handler(calls, **kwargs)),
    )


def test_20mb_file_sends_3_chunks_with_correct_headers(tmp_path):
    calls = []
    client = _client(calls)
    f = tmp_path / "game.zip"
    f.write_bytes(os.urandom(20 * 1024 * 1024))

    upload_file(client, f, platform_id=5)

    start = next(c for c in calls if c.url.path == "/api/roms/upload/start")
    assert start.headers["x-upload-platform"] == "5"
    assert start.headers["x-upload-filename"] == "game.zip"
    assert start.headers["x-upload-total-size"] == str(20 * 1024 * 1024)
    assert start.headers["x-upload-total-chunks"] == "3"

    puts = [c for c in calls if c.method == "PUT"]
    assert len(puts) == 3
    assert sorted(int(p.headers["x-chunk-index"]) for p in puts) == [0, 1, 2]


def test_uneven_division_sends_server_formula_sized_chunks(tmp_path):
    """20 MiB at the 8 MiB default chunk size does not divide evenly.
    total_chunks=3, but the server computes its own expected chunk size as
    ceil(total_size/total_chunks) -- NOT the 8 MiB we asked for -- and
    rejects any chunk whose length differs. This pins the exact byte
    counts RomM's real _expected_chunk_size demands: 6990507, 6990507,
    6990506."""
    calls = []
    client = _client(calls)
    f = tmp_path / "game.zip"
    total_size = 20 * 1024 * 1024
    f.write_bytes(os.urandom(total_size))

    upload_file(client, f, platform_id=5)  # default chunk_size = 8 MiB

    puts = sorted(
        (c for c in calls if c.method == "PUT"),
        key=lambda c: int(c.headers["x-chunk-index"]),
    )
    sizes = [len(p.read()) for p in puts]
    assert sizes == [6990507, 6990507, 6990506]
    assert sum(sizes) == total_size


def test_chunk_bodies_reconstruct_file_byte_for_byte(tmp_path):
    """The whole point of chunking is that reassembly is lossless."""
    calls = []
    client = _client(calls)
    f = tmp_path / "rom.bin"
    size = 2500  # -> 3 chunks; desired chunk_size only decides total_chunks
    original = os.urandom(size)
    f.write_bytes(original)

    upload_file(client, f, platform_id=5, chunk_size=1000)

    puts = sorted(
        (c for c in calls if c.method == "PUT"),
        key=lambda c: int(c.headers["x-chunk-index"]),
    )
    assert len(puts) == 3
    reconstructed = b"".join(p.read() for p in puts)
    assert reconstructed == original
    assert hashlib.sha256(reconstructed).hexdigest() == hashlib.sha256(original).hexdigest()


def test_file_smaller_than_one_chunk_sends_a_single_put(tmp_path):
    calls = []
    client = _client(calls)
    f = tmp_path / "small.rom"
    f.write_bytes(os.urandom(100))

    upload_file(client, f, platform_id=5, chunk_size=DEFAULT_CHUNK_SIZE)

    start = next(c for c in calls if c.url.path == "/api/roms/upload/start")
    assert start.headers["x-upload-total-chunks"] == "1"
    puts = [c for c in calls if c.method == "PUT"]
    assert len(puts) == 1


def test_empty_file_is_rejected_before_any_request_is_made(tmp_path):
    """total_chunks: 0 would make the server wait forever for a chunk that
    never arrives -- reject client-side before any HTTP call goes out."""
    calls = []
    client = _client(calls)
    f = tmp_path / "empty.rom"
    f.write_bytes(b"")

    with pytest.raises(RommError, match="empty"):
        upload_file(client, f, platform_id=5)

    assert calls == []


def test_failure_mid_upload_cancels_and_raises_romm_error(tmp_path):
    calls = []
    client = _client(calls, fail_on_chunk_index=1)
    f = tmp_path / "rom.bin"
    f.write_bytes(os.urandom(2500))

    with pytest.raises(RommError):
        upload_file(client, f, platform_id=5, chunk_size=1000)

    cancels = [c for c in calls if c.url.path.endswith("/cancel")]
    assert len(cancels) == 1


def test_failure_completing_also_cancels(tmp_path):
    """A failure at /complete still leaves a dangling upload session --
    cancel it too, not just mid-chunk failures."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if path == "/api/roms/upload/start":
            return httpx.Response(201, json={"upload_id": "up-1"})
        if path == "/api/roms/upload/up-1" and request.method == "PUT":
            return httpx.Response(200, json={"received": 1, "total": 1})
        if path == "/api/roms/upload/up-1/complete":
            return httpx.Response(500, text="complete failed")
        if path == "/api/roms/upload/up-1/cancel":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": f"unhandled {path}"})

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )
    f = tmp_path / "g.rom"
    f.write_bytes(os.urandom(10))

    with pytest.raises(RommError):
        upload_file(client, f, platform_id=5)

    assert any(c.url.path.endswith("/cancel") for c in calls)


def test_x_upload_platform_is_sent_as_the_integer_id_not_a_slug(tmp_path):
    calls = []
    client = _client(calls)
    f = tmp_path / "g.rom"
    f.write_bytes(os.urandom(10))

    upload_file(client, f, platform_id=42)

    start = next(c for c in calls if c.url.path == "/api/roms/upload/start")
    assert start.headers["x-upload-platform"] == "42"


def test_upload_file_returns_the_complete_response(tmp_path):
    calls = []
    client = _client(calls)
    f = tmp_path / "g.rom"
    f.write_bytes(os.urandom(10))

    result = upload_file(client, f, platform_id=5)

    assert result == {"id": 999, "rom_id": 999}


def test_missing_upload_id_in_start_response_raises_clear_error(tmp_path):
    """If a /start response ever lacks upload_id (e.g. a future RomM field
    rename), fail with a message naming the keys actually present rather
    than a bare KeyError -- and never attempt a chunk without a valid id."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if path == "/api/roms/upload/start":
            return httpx.Response(201, json={"session": "up-1"})  # wrong key
        return httpx.Response(404, json={"detail": f"unhandled {path}"})

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )
    f = tmp_path / "g.rom"
    f.write_bytes(os.urandom(10))

    with pytest.raises(RommError, match="upload_id") as exc_info:
        upload_file(client, f, platform_id=5)
    assert "session" in str(exc_info.value)

    assert not any(c.method == "PUT" for c in calls)


def test_complete_409_missing_chunks_message_reaches_romm_error(tmp_path):
    """RomM's /complete returns 409 'Missing chunks: [...]' when
    received != total_chunks. That text must reach the RommError message,
    not be swallowed by generic error handling."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if path == "/api/roms/upload/start":
            return httpx.Response(201, json={"upload_id": "up-1"})
        if path == "/api/roms/upload/up-1" and request.method == "PUT":
            return httpx.Response(200, json={"received": 1, "total": 1})
        if path == "/api/roms/upload/up-1/complete":
            return httpx.Response(409, json={"detail": "Missing chunks: [0]"})
        if path == "/api/roms/upload/up-1/cancel":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": f"unhandled {path}"})

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )
    f = tmp_path / "g.rom"
    f.write_bytes(os.urandom(10))

    with pytest.raises(RommError, match="Missing chunks"):
        upload_file(client, f, platform_id=5)
