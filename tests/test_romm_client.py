"""RomM HTTP client: auth (OAuth2 form-encoded) and cached platform resolution.

Every call is mocked with httpx.MockTransport. No test here may require a
live RomM instance.
"""

import httpx
import pytest

from rom_hub.backends.romm.client import (
    _ROMS_PAGE_SIZE,
    REQUIRED_SCOPES,
    RommClient,
    RommError,
)

PLATFORMS = [
    {"id": 5, "slug": "dos", "name": "DOS"},
    {"id": 9, "slug": "snes", "name": "Super Nintendo"},
    # slug and fs_slug can differ; fs_slug-only lookups must still resolve.
    {"id": 12, "slug": "genesis-slug", "fs_slug": "genesis-fs", "name": "Genesis"},
]


def _handler(calls, platforms=None, token_status=200, token_body=None, collections=None):
    platforms = PLATFORMS if platforms is None else platforms
    collections = [] if collections is None else collections
    created = {"next_id": 100}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            if token_status != 200:
                return httpx.Response(
                    token_status, json=token_body or {"detail": "invalid credentials"}
                )
            return httpx.Response(
                200, json=token_body or {"access_token": "tok-123", "token_type": "bearer"}
            )
        if request.url.path == "/api/platforms":
            return httpx.Response(200, json=platforms)
        if request.url.path == "/api/collections" and request.method == "GET":
            return httpx.Response(200, json=collections)
        if request.url.path == "/api/collections" and request.method == "POST":
            new_id = created["next_id"]
            created["next_id"] += 1
            return httpx.Response(201, json={"id": new_id, "name": "created"})
        if request.url.path.startswith("/api/collections/") and request.url.path.endswith(
            "/roms"
        ):
            return httpx.Response(201, json={"ok": True})
        return httpx.Response(404, json={"detail": f"unhandled path {request.url.path}"})

    return handler


def _client(calls, **kwargs):
    return RommClient(
        "https://romm.example",
        "user",
        "pw",
        transport=httpx.MockTransport(_handler(calls, **{
            k: v
            for k, v in kwargs.items()
            if k in ("platforms", "token_status", "token_body", "collections")
        })),
    )


def test_authenticate_posts_form_encoded_and_stores_token():
    calls = []
    client = _client(calls)
    client.authenticate()

    assert len(calls) == 1
    req = calls[0]
    assert req.method == "POST"
    assert req.url.path == "/api/token"
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    body = req.read().decode()
    assert "grant_type=password" in body


def test_authenticate_requests_the_scopes_the_import_pipeline_needs():
    """RomM issues a *valid* token with `"scopes":""` when the token request
    omits `scope`, and then answers every API call with 403. Measured against
    a real RomM 4.9.2: no scope -> GET /api/platforms 403; with these scopes
    -> platforms/roms/collections 200 and upload/start reaches 400 (bad
    platform id) rather than 403.

    So the scope parameter is not optional garnish -- without it the client
    authenticates successfully and then cannot do anything at all.
    """
    calls = []
    client = _client(calls)
    client.authenticate()

    body = calls[0].read().decode()
    from urllib.parse import parse_qs

    fields = parse_qs(body)
    assert "scope" in fields, f"token request sent no scope field: {body!r}"
    sent = set(fields["scope"][0].split())
    assert sent == {
        "me.read",
        "roms.read",
        "roms.write",
        "platforms.read",
        "platforms.write",
        "collections.read",
        "collections.write",
        "firmware.read",
        "firmware.write",
    }
    # The constant is what the rest of the codebase and the docs refer to;
    # it must be the thing actually sent, not a parallel literal.
    assert sent == set(REQUIRED_SCOPES.split())


def test_required_scopes_asks_for_nothing_the_import_does_not_need():
    """Requesting users.* or tasks.run would hand the Hub's token authority
    over other people's accounts and the server's task runner for no reason.

    The list grows only when a caller does. `firmware.*` is here because
    `rom-hub firmware install` reads the platform's firmware and posts to
    `/api/firmware`; before that command existed the scope was not asked
    for, and `assets.*`, `devices.*` and `roms.user.*` still are not."""
    granted = REQUIRED_SCOPES.split()
    assert granted, "REQUIRED_SCOPES must not be empty"
    assert not [s for s in granted if s.startswith("users.")]
    assert "tasks.run" not in granted
    assert not [
        s
        for s in granted
        if s.split(".")[0]
        not in {"me", "roms", "platforms", "collections", "firmware"}
    ]


def test_403_on_a_read_names_scopes_so_the_real_misconfiguration_is_legible():
    """The failure a misconfigured deployment actually hits: a token that
    authenticated fine but carries no (or too few) scopes. A bare "403" sends
    an operator hunting for a credentials problem that isn't there, so the
    message has to name scopes."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(403, json={"detail": "Forbidden"})

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RommError) as exc_info:
        client.list_platforms()

    message = str(exc_info.value)
    assert "403" in message
    assert "scope" in message.lower()
    assert not isinstance(exc_info.value, httpx.HTTPStatusError)


def test_subsequent_calls_send_bearer_token():
    calls = []
    client = _client(calls)
    client.authenticate()
    client.list_platforms()

    assert calls[-1].headers["authorization"] == "Bearer tok-123"


def test_platform_id_resolves_via_platforms_endpoint_as_int():
    calls = []
    client = _client(calls)

    result = client.platform_id("dos")

    assert result == 5
    assert isinstance(result, int)
    assert any(c.url.path == "/api/platforms" for c in calls)


def test_platform_id_is_cached_no_second_http_request():
    calls = []
    client = _client(calls)

    client.platform_id("dos")
    count_after_first = len(calls)
    client.platform_id("snes")  # different slug, should use the same cached listing
    client.platform_id("dos")

    assert len(calls) == count_after_first


def test_platform_id_unknown_slug_raises_romm_error_naming_it():
    calls = []
    client = _client(calls)

    with pytest.raises(RommError, match="nope"):
        client.platform_id("nope")


def test_platform_id_falls_back_to_fs_slug():
    """PlatformSchema has both `slug` and `fs_slug`; a lookup that only
    matches fs_slug (not slug) must still resolve."""
    calls = []
    client = _client(calls)

    assert client.platform_id("genesis-fs") == 12


def test_platform_id_lookup_is_case_insensitive():
    calls = []
    client = _client(calls)

    assert client.platform_id("DOS") == 5
    assert client.platform_id("Genesis-Fs") == 12


def test_401_raises_romm_error_about_authentication_not_httpstatuserror():
    calls = []
    client = _client(calls, token_status=401, token_body={"detail": "bad credentials"})

    with pytest.raises(RommError, match="(?i)authenticat") as exc_info:
        client.authenticate()

    assert not isinstance(exc_info.value, httpx.HTTPStatusError)


def test_non_2xx_on_a_regular_call_raises_romm_error_with_status_and_body():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(500, text="internal server error, details omitted")

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RommError, match="500") as exc_info:
        client.list_platforms()

    assert "internal server error" in str(exc_info.value)
    assert not isinstance(exc_info.value, httpx.HTTPStatusError)


def _paged_roms_client(pages_of, total):
    """A mock RomM whose /api/roms answers the real paginated envelope."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if request.url.path == "/api/roms":
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 50))
            items = pages_of[offset : offset + limit]
            return httpx.Response(
                200,
                json={
                    "items": items,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "char_index": {},
                    "rom_id_index": [],
                    "filter_values": {},
                },
            )
        return httpx.Response(404, json={"detail": "unhandled"})

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )
    return client, calls


def test_list_roms_unwraps_the_paginated_envelope_into_a_list():
    """GET /api/roms returns CustomLimitOffsetPage_SimpleRomSchema_ --
    `{"items": [...], "total": N, "limit": 50, "offset": 0, ...}` -- not a
    bare list. Returning the envelope makes every consumer iterate a dict,
    which yields its *keys* (strings). `find_duplicate` skips non-dict
    entries, so it silently matched nothing: dedup never fired and the
    post-upload confirmation could never find the rom it had just uploaded.
    """
    roms = [{"id": 1, "sha1_hash": "aa"}, {"id": 2, "sha1_hash": "bb"}]
    client, _ = _paged_roms_client(roms, total=2)

    result = client.list_roms(5)

    assert isinstance(result, list), f"expected a list, got {type(result).__name__}"
    assert result == roms
    # The thing that actually bit: every entry must be a dict a consumer can
    # read hashes off, not a string key from the envelope.
    assert all(isinstance(r, dict) for r in result)


def test_list_roms_pages_past_the_server_limit():
    """`limit` is capped server-side (it defaults to 50). A platform with
    more roms than one page would otherwise be compared against only its
    first page, so dedup would miss duplicates beyond it and re-upload
    them.

    Sized off the client's own page size so this keeps testing paging if
    that constant is ever retuned.
    """
    count = _ROMS_PAGE_SIZE + 30
    roms = [{"id": i, "sha1_hash": f"{i:040x}"} for i in range(1, count + 1)]
    client, calls = _paged_roms_client(roms, total=count)

    result = client.list_roms(5)

    assert len(result) == count
    assert [r["id"] for r in result] == list(range(1, count + 1))
    rom_calls = [c for c in calls if c.url.path == "/api/roms"]
    assert len(rom_calls) > 1, "expected more than one page to be fetched"
    assert all(c.url.params.get("platform_ids") == "5" for c in rom_calls)
    # Offsets must advance, or a second page is just the first page again.
    assert [c.url.params.get("offset") for c in rom_calls] == [
        "0", str(_ROMS_PAGE_SIZE)
    ]


def test_list_roms_stops_instead_of_looping_forever_on_a_lying_total():
    """A `total` that never matches what the server actually returns must
    not spin: an empty page ends the walk regardless of what `total` says."""
    client, calls = _paged_roms_client([{"id": 1}], total=10_000)

    result = client.list_roms(5)

    assert result == [{"id": 1}]
    assert len([c for c in calls if c.url.path == "/api/roms"]) < 10


def test_list_roms_still_accepts_a_bare_list_response():
    """Tolerate a RomM that answers a plain array rather than the envelope,
    so the client is not pinned to one server version's response shape."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(200, json=[{"id": 7, "sha1_hash": "cc"}])

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )

    assert client.list_roms(5) == [{"id": 7, "sha1_hash": "cc"}]


def test_ensure_collection_returns_existing_id_without_creating():
    calls = []
    client = _client(calls, collections=[{"id": 7, "name": "Imports"}])

    result = client.ensure_collection("Imports")

    assert result == 7
    assert not any(
        c.url.path == "/api/collections" and c.method == "POST" for c in calls
    )


def test_ensure_collection_creates_when_missing():
    calls = []
    client = _client(calls, collections=[])

    result = client.ensure_collection("New Collection")

    assert result == 100
    assert any(c.url.path == "/api/collections" and c.method == "POST" for c in calls)


def test_ensure_collection_create_posts_multipart_not_json():
    """Body_add_collection_api_collections_post mixes Form fields with an
    optional `artwork: binary` file, which makes the endpoint
    multipart/form-data — a JSON body here is wrong."""
    calls = []
    client = _client(calls, collections=[])

    client.ensure_collection("New Collection")

    post = next(
        c for c in calls if c.url.path == "/api/collections" and c.method == "POST"
    )
    content_type = post.headers["content-type"]
    assert content_type.startswith("multipart/form-data")
    body = post.read().decode(errors="replace")
    assert 'name="name"' in body
    assert "New Collection" in body


def test_add_to_collection_posts_json_not_multipart():
    """CollectionRomsPayload ({"rom_ids": [...]})  IS JSON, unlike the
    collections-create endpoint."""
    calls = []
    client = _client(calls)

    client.add_to_collection(7, [1, 2, 3])

    post = calls[-1]
    assert post.method == "POST"
    assert post.url.path == "/api/collections/7/roms"
    assert post.headers["content-type"] == "application/json"
    import json as _json

    assert _json.loads(post.read()) == {"rom_ids": [1, 2, 3]}


def test_401_or_403_on_a_write_raises_romm_error_naming_authorization():
    """A write bounced for auth reasons (e.g. missing scope on the token)
    must surface plainly, not as a generic status-code message."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok-123"})
        if request.url.path == "/api/collections/7/roms":
            return httpx.Response(403, json={"detail": "forbidden"})
        return httpx.Response(404, json={"detail": "unhandled"})

    client = RommClient(
        "https://romm.example", "user", "pw", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RommError, match="(?i)authoriz") as exc_info:
        client.add_to_collection(7, [1])

    assert not isinstance(exc_info.value, httpx.HTTPStatusError)
