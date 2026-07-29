"""RomM HTTP client: auth (OAuth2 form-encoded) and cached platform resolution.

Every call is mocked with httpx.MockTransport. No test here may require a
live RomM instance.
"""

import httpx
import pytest

from romm_hub.romm.client import REQUIRED_SCOPES, RommClient, RommError

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
    }
    # The constant is what the rest of the codebase and the docs refer to;
    # it must be the thing actually sent, not a parallel literal.
    assert sent == set(REQUIRED_SCOPES.split())


def test_required_scopes_asks_for_nothing_the_import_does_not_need():
    """Requesting users.* or tasks.run would hand the Hub's token authority
    over other people's accounts and the server's task runner for no reason
    -- import touches roms, platforms and collections only."""
    granted = REQUIRED_SCOPES.split()
    assert granted, "REQUIRED_SCOPES must not be empty"
    assert not [s for s in granted if s.startswith("users.")]
    assert "tasks.run" not in granted
    assert not [
        s
        for s in granted
        if s.split(".")[0] not in {"me", "roms", "platforms", "collections"}
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
