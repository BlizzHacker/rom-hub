"""Chunked upload orchestration for RomM's /api/roms/upload/* endpoints.

No test here may require a live RomM: every HTTP call is mocked via
httpx.MockTransport, exactly like tests/test_romm_client.py.
"""

import hashlib
import os

import httpx
import pytest

from romm_hub.romm.client import RommClient, RommError
from romm_hub.romm.upload import DEFAULT_CHUNK_SIZE, upload_file


def _handler(calls, upload_id="up-1", fail_on_chunk_index=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if path == "/api/roms/upload/start" and request.method == "POST":
            return httpx.Response(201, json={"id": upload_id})
        if path == f"/api/roms/upload/{upload_id}" and request.method == "PUT":
            index = int(request.headers["x-chunk-index"])
            if fail_on_chunk_index is not None and index == fail_on_chunk_index:
                return httpx.Response(500, text="chunk rejected")
            return httpx.Response(200, json={"ok": True})
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


def test_chunk_bodies_reconstruct_file_byte_for_byte(tmp_path):
    """The whole point of chunking is that reassembly is lossless."""
    calls = []
    client = _client(calls)
    f = tmp_path / "rom.bin"
    size = 2500  # -> 3 chunks at chunk_size=1000
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
            return httpx.Response(201, json={"id": "up-1"})
        if path == "/api/roms/upload/up-1" and request.method == "PUT":
            return httpx.Response(200, json={"ok": True})
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
