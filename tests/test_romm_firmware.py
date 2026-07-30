"""RomM's firmware endpoints, as the client actually calls them.

Every call is mocked with `httpx.MockTransport`. What is asserted is the
*request shape*, because that is where this kind of code goes wrong: RomM
declares `platform_id` as a bare `int` parameter on both routes, which
FastAPI reads from the query string even on the POST, and `files:
list[UploadFile] = File(...)` as a repeated multipart part. A request
built from a guess about either one fails at runtime with a 422 that says
nothing useful, so the shape is pinned here against the signatures in
`backend/endpoints/firmware.py`.
"""

from pathlib import Path

import httpx
import pytest

from rom_hub.backends.romm.client import RommClient, RommError

FIRMWARE = [
    {"id": 1, "platform_id": 7, "file_name": "dmg_boot.bin", "file_size_bytes": 256},
    {"id": 2, "platform_id": 7, "file_name": "cgb_boot.bin", "file_size_bytes": 2304},
]


def _client(calls, *, list_body=None, post_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok", "type": "bearer"})
        if request.url.path == "/api/firmware" and request.method == "GET":
            return httpx.Response(
                200, json=FIRMWARE if list_body is None else list_body
            )
        if request.url.path == "/api/firmware" and request.method == "POST":
            if post_status != 200:
                return httpx.Response(post_status, json={"detail": "nope"})
            return httpx.Response(200, json={"uploaded": 1, "firmware": FIRMWARE})
        return httpx.Response(404, json={"detail": request.url.path})

    return RommClient(
        "https://romm.example", "u", "p", transport=httpx.MockTransport(handler)
    )


def test_listing_sends_platform_id_as_a_query_parameter():
    calls = []
    rows = _client(calls).list_firmware(7)
    request = calls[-1]
    assert request.method == "GET"
    assert request.url.path == "/api/firmware"
    assert request.url.params["platform_id"] == "7"
    assert [row["file_name"] for row in rows] == ["dmg_boot.bin", "cgb_boot.bin"]


def test_listing_expects_a_bare_list_not_a_paginated_envelope():
    """`get_platform_firmware` returns `list[FirmwareSchema]` and does no
    paging at all -- which is the one way it differs from `/api/roms`, and
    the reason `list_roms`' page walking is not copied onto it. A dict here
    would mean the endpoint changed, and iterating it would silently yield
    its *keys*, exactly as it once did for roms."""
    calls = []
    client = _client(calls, list_body={"items": FIRMWARE, "total": 2})
    with pytest.raises(RommError, match="expected a list"):
        client.list_firmware(7)


def test_upload_posts_every_file_as_a_repeated_part(tmp_path):
    calls = []
    first = tmp_path / "dmg_boot.bin"
    first.write_bytes(b"dmg")
    second = tmp_path / "cgb_boot.bin"
    second.write_bytes(b"cgb")

    _client(calls).upload_firmware([first, second], 7)

    request = calls[-1]
    assert request.method == "POST"
    assert request.url.path == "/api/firmware"
    # A query parameter, not a form field. `platform_id: int` on the POST
    # carries no Body/Form marker, so FastAPI takes it from the query
    # string and a form field is a 422.
    assert request.url.params["platform_id"] == "7"
    assert request.headers["content-type"].startswith("multipart/form-data")

    body = request.read()
    assert body.count(b'name="files"') == 2
    assert b'filename="dmg_boot.bin"' in body
    assert b'filename="cgb_boot.bin"' in body
    assert b"dmg" in body and b"cgb" in body


def test_the_whole_set_goes_in_one_request(tmp_path):
    """`files: list[UploadFile]` takes them together, and one request means
    one `scan_firmware` pass rather than N."""
    calls = []
    paths = []
    for name in ("a.bin", "b.bin", "c.bin"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)

    _client(calls).upload_firmware(paths, 7)

    posts = [c for c in calls if c.method == "POST" and c.url.path == "/api/firmware"]
    assert len(posts) == 1


def test_an_empty_file_is_refused_before_it_is_sent(tmp_path):
    """RomM's `add_firmware` skips an empty filename and would otherwise
    record a zero-byte BIOS as verified-against-nothing."""
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    with pytest.raises(RommError, match="empty firmware file"):
        _client([]).upload_firmware([empty], 7)


def test_uploading_nothing_is_refused():
    with pytest.raises(RommError, match="no files were given"):
        _client([]).upload_firmware([], 7)


def test_an_unreadable_file_names_itself(tmp_path):
    missing = tmp_path / "gone.bin"
    with pytest.raises(RommError, match="cannot read firmware file"):
        _client([]).upload_firmware([missing], 7)


def test_a_403_on_the_upload_names_the_scopes(tmp_path):
    """The likely misconfiguration is a token issued without
    `firmware.write`, which authenticates fine and then 403s."""
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")
    with pytest.raises(RommError, match="firmware.write"):
        _client([], post_status=403).upload_firmware([path], 7)


def test_the_backend_forwards_both_calls(tmp_path):
    """The backend is a composition, not a second implementation."""
    from rom_hub.backends.romm.backend import RommBackend

    calls = []
    backend = RommBackend("https://romm.example", "u", "p", client=_client(calls))
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")

    assert [row["file_name"] for row in backend.list_firmware(7)] == [
        "dmg_boot.bin",
        "cgb_boot.bin",
    ]
    # Returns None on purpose: what `add_firmware` answers with is the
    # platform's whole firmware listing, which a caller would misread as
    # "what I just sent".
    assert backend.upload_firmware([path], 7) is None


def test_paths_may_be_strings(tmp_path):
    """`FirmwareInstallResult.files` are `Path`s, but nothing stops a
    caller passing what it read out of a job record."""
    calls = []
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")
    _client(calls).upload_firmware([str(path)], 7)
    assert b'filename="a.bin"' in calls[-1].read()


def test_pathlib_is_what_the_client_names_the_file_by(tmp_path):
    """The part filename is the *basename*, never the operator's directory
    layout -- that would leak the Hub's own paths into the library."""
    calls = []
    nested = tmp_path / "deep" / "deeper"
    nested.mkdir(parents=True)
    path = nested / "dmg_boot.bin"
    path.write_bytes(b"x")
    _client(calls).upload_firmware([path], 7)
    body = calls[-1].read()
    assert b'filename="dmg_boot.bin"' in body
    assert str(nested).encode() not in body
    assert Path(path).name == "dmg_boot.bin"
