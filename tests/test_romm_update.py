"""`PUT /api/roms/{id}` -- the metadata write, and `GET /api/roms/{id}`.

The encoding was **measured against a real RomM 4.9.2**, not inferred from
the schema, because the schema is misleading here and the codebase's other
multipart call does the opposite of what this one needs. Against the
disposable server, on rom 1:

    multipart, name + igdb_id, no artwork part    -> 200, both applied
    multipart, name only, afterwards              -> 200, igdb_id SURVIVED
    urlencoded (no multipart at all), name only   -> 200, igdb_id SURVIVED
    multipart with an EMPTY artwork part          -> 400, nothing applied
    multipart with an unknown extra part          -> 200, part ignored

Two things follow. **Unset fields are not blanked**: RomM applies what it
is given and leaves the rest, so a partial patch is safe *provided* the
absent field is genuinely absent from the request. And **the empty-artwork
trick used by `ensure_collection` is wrong here** -- it is a 400, so a
no-artwork update must not carry an artwork part at all. That is why this
sends urlencoded when there is no artwork and multipart only when there is.

Every call is mocked with httpx.MockTransport. No test here may require a
live RomM instance.
"""

import httpx
import pytest

from romm_hub.romm.client import RommClient, RommError

ROM = {
    "id": 42,
    "name": "doom",
    "fs_name": "doom.zip",
    "platform_slug": "dos",
    "fs_size_bytes": 1234,
}


def _client(calls, rom=None, update_status=200):
    rom = ROM if rom is None else rom

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok", "type": "bearer"})
        if request.url.path == "/api/roms/42" and request.method == "GET":
            return httpx.Response(200, json=rom)
        if request.url.path == "/api/roms/42" and request.method == "PUT":
            if update_status != 200:
                return httpx.Response(update_status, json={"detail": "nope"})
            return httpx.Response(200, json={**rom, "name": "updated"})
        return httpx.Response(404, json={"detail": request.url.path})

    return RommClient(
        "https://romm.example", "u", "pw", transport=httpx.MockTransport(handler)
    )


def _put(calls) -> httpx.Request:
    return [c for c in calls if c.method == "PUT"][0]


def _body(request: httpx.Request) -> str:
    return request.read().decode("utf-8", errors="replace")


def test_get_rom_returns_the_record():
    calls = []
    assert _client(calls).get_rom(42)["name"] == "doom"


def test_update_rom_sends_only_the_named_fields():
    """The invariant that protects a curated library: a field the caller
    did not set is not in the request at all, so RomM cannot blank it."""
    calls = []
    _client(calls).update_rom(42, {"name": "Doom"})

    put = _put(calls)
    assert put.url.path == "/api/roms/42"
    body = _body(put)
    assert "Doom" in body
    assert "igdb_id" not in body
    assert "raw_igdb_metadata" not in body


def test_an_update_without_artwork_carries_no_artwork_part():
    """Measured: an empty artwork part is a 400 from RomM, and would be an
    invitation to replace a user's cover with nothing if it were not."""
    calls = []
    _client(calls).update_rom(42, {"name": "Doom"})
    put = _put(calls)
    assert 'name="artwork"' not in _body(put)
    assert put.headers["content-type"] == "application/x-www-form-urlencoded"


def test_update_rom_posts_multipart_when_there_is_artwork():
    calls = []
    _client(calls).update_rom(
        42, {"name": "Doom"}, artwork=("cover.png", b"\x89PNG-bytes", "image/png")
    )

    put = _put(calls)
    assert put.headers["content-type"].startswith("multipart/form-data")
    body = _body(put)
    assert 'name="artwork"' in body
    assert 'filename="cover.png"' in body
    assert "PNG-bytes" in body
    # The other fields ride along in the same request, not a second one.
    assert 'name="name"' in body
    assert len([c for c in calls if c.method == "PUT"]) == 1


def test_a_failed_update_raises_rommerror_not_an_httpx_exception():
    calls = []
    with pytest.raises(RommError, match="422"):
        _client(calls, update_status=422).update_rom(42, {"name": "x"})


def test_update_rom_refuses_a_field_name_that_is_not_a_metadata_field():
    """The fields mapping is built from plugin-supplied keys upstream, so
    this is the last layer that can notice a key which should never have
    reached it -- `fs_name`, for one, renames the file on disk."""
    calls = []
    with pytest.raises(RommError, match="fs_name"):
        _client(calls).update_rom(42, {"fs_name": "../evil.zip"})
    assert not [c for c in calls if c.method == "PUT"]
