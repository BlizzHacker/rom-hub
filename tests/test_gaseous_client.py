"""Gaseous HTTP client: cookie auth, platform resolution, listing, upload.

Every call is mocked with httpx.MockTransport. No test here may require a
live Gaseous instance.

The fixtures below are trimmed copies of real responses from
`ghcr.io/gaseous-project/gaseousserver:v2.0.0-rc.3`, not invented shapes
-- including the parts that are surprising (a bare GUID as an upload
response body, a 404 for an empty rom listing, a 302 instead of a 401).
"""

import httpx
import pytest

from rom_hub.backends.gaseous.client import (
    UNKNOWN_PLATFORM_ID,
    GaseousClient,
    GaseousError,
)

API = "/api/v1.1"

# Trimmed from GET /api/v1.1/Platforms. `alternative_name` is present on
# some records and absent on others, which is why resolution reads all
# three of slug/name/alternative_name defensively.
PLATFORMS = [
    {"sourceType": "None", "id": 13, "name": "DOS", "slug": "dos",
     "alternative_name": "PC DOS"},
    {"sourceType": "None", "id": 19, "name": "Super Nintendo Entertainment System",
     "slug": "snes"},
    {"sourceType": "None", "id": 0, "name": "Unknown Platform", "slug": "unknown"},
]

# Trimmed from GET /api/v1.1/Games/1/roms?PlatformId=0.
ROM_ON_UNKNOWN = {
    "platformId": 0,
    "platform": "Unknown Platform",
    "metadataMapId": 1,
    "id": 1,
    "name": "rubik.zip",
    "size": 15360,
    "crc": "bbce0b77",
    "md5": "e73b39d69c07acc8f110cdb56d9060c2",
    "sha1": "6474301c3233ac5f60ba772de5c3dbcd57780e15",
    "relativePath": "unknown/rubik/rubik.zip",
}

ROM_ON_DOS = {
    "platformId": 13,
    "platform": "DOS",
    "metadataMapId": 2,
    "id": 2,
    "name": "identified.img",
    "size": 512,
    "crc": "aaaa1111",
    "md5": "11111111111111111111111111111111",
    "sha1": "2222222222222222222222222222222222222222",
}


def _handler(
    calls,
    platforms=None,
    login_status=200,
    login_body=None,
    games=None,
    roms=None,
    upload_body="4a5f2b1c-0000-4000-8000-abcdefabcdef",
    upload_status=200,
    imports=None,
    authenticated=None,
):
    platforms = PLATFORMS if platforms is None else platforms
    games = [] if games is None else games
    # {(metadata_map_id, platform_id): [rom, ...]}
    roms = {} if roms is None else roms
    imports = [] if imports is None else imports
    state = {"logged_in": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path

        if path == f"{API}/Account/Login":
            if login_status != 200:
                return httpx.Response(
                    login_status, json=login_body or {"detail": "nope"}
                )
            state["logged_in"] = True
            return httpx.Response(200, json=login_body or {"success": True})

        # Everything else needs the cookie. Gaseous redirects rather than
        # answering 401; `authenticated=False` forces that branch.
        logged_in = state["logged_in"] if authenticated is None else authenticated
        if not logged_in:
            return httpx.Response(302, headers={"location": "/Account/Login"})

        if path == f"{API}/Platforms":
            return httpx.Response(200, json=platforms)

        if path == f"{API}/Games" and request.method == "POST":
            page = int(request.url.params.get("pageNumber", 1))
            size = int(request.url.params.get("pageSize", 200))
            start = (page - 1) * size
            return httpx.Response(200, json={"games": games[start : start + size]})

        if path.startswith(f"{API}/Games/") and path.endswith("/roms"):
            map_id = int(path.split("/")[-2])
            platform_id = int(request.url.params.get("PlatformId", -1))
            found = roms.get((map_id, platform_id), [])
            if not found:
                # Gaseous 404s an empty listing: the controller catches
                # every exception and returns NotFound.
                return httpx.Response(404, json={"status": 404})
            return httpx.Response(
                200, json={"gameRomItems": found, "count": len(found)}
            )

        if path == f"{API}/Roms" and request.method == "POST":
            # A bare GUID, not JSON. Ok(sessionid.ToString()).
            return httpx.Response(upload_status, text=upload_body)

        if path == f"{API}/Roms/Imports":
            return httpx.Response(200, json=imports)

        return httpx.Response(404, json={"detail": f"unhandled {path}"})

    return handler


def _client(calls, **kwargs):
    return GaseousClient(
        "https://gaseous.example",
        "romhub@example.com",
        "pw",
        transport=httpx.MockTransport(_handler(calls, **kwargs)),
    )


# -- auth ------------------------------------------------------------------


def test_login_posts_json_and_keeps_the_cookie():
    """Gaseous is cookie auth, not a bearer token: there is no Authorization
    header to assert on, so the assertion is that the body is JSON and that
    a second call does not log in again."""
    calls = []
    client = _client(calls)
    client.authenticate()

    login = calls[0]
    assert login.method == "POST"
    assert login.url.path == f"{API}/Account/Login"
    assert b'"Email"' in login.content and b'"Password"' in login.content
    # No token anywhere -- if this ever grows an Authorization header it is
    # because someone assumed RomM's scheme.
    assert "Authorization" not in login.headers

    client.list_platforms()
    assert sum(1 for c in calls if c.url.path.endswith("/Login")) == 1


def test_a_bad_password_is_reported_as_an_auth_failure():
    calls = []
    client = _client(calls, login_status=401)
    with pytest.raises(GaseousError) as exc:
        client.authenticate()
    assert "authentication failed" in str(exc.value)


def test_a_username_rejected_as_not_an_email_says_so():
    """The shipped build validates LoginViewModel.Email as an e-mail even
    though it is captioned "Email or Username"; an operator who set
    GASEOUS_USER to a bare username needs to be told that, not shown a
    400."""
    calls = []
    client = _client(
        calls,
        login_status=400,
        login_body={"errors": {"Email": ["The Email field is not a valid e-mail address."]}},
    )
    with pytest.raises(GaseousError) as exc:
        client.authenticate()
    assert "e-mail" in str(exc.value)
    assert "GASEOUS_USER" in str(exc.value)


def test_two_factor_is_not_mistaken_for_a_successful_login():
    """2FA answers 200 with requiresTwoFactor, and a client that read only
    the status code would believe it was logged in and then 302 on every
    call afterwards."""
    calls = []
    client = _client(calls, login_body={"requiresTwoFactor": True})
    with pytest.raises(GaseousError) as exc:
        client.authenticate()
    assert "two-factor" in str(exc.value)


def test_a_302_is_treated_as_not_authenticated_not_as_success():
    """The redirect must never be followed: following it answers 200 with
    the login page's HTML, and the failure then surfaces as a JSON parse
    error somewhere unrelated."""
    calls = []
    client = _client(calls, authenticated=False)
    with pytest.raises(GaseousError) as exc:
        client.list_platforms()
    assert "not authenticated" in str(exc.value)


# -- platforms -------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("dos", 13),
        ("DOS", 13),
        ("Dos", 13),
        ("PC DOS", 13),  # alternative_name
        ("snes", 19),
        ("Super Nintendo Entertainment System", 19),
    ],
)
def test_platform_resolution_matches_slug_name_or_alternative_name(name, expected):
    calls = []
    client = _client(calls)
    assert client.platform_id(name) == expected


def test_platform_resolution_is_cached_after_the_first_call():
    calls = []
    client = _client(calls)
    client.platform_id("dos")
    client.platform_id("snes")
    assert sum(1 for c in calls if c.url.path == f"{API}/Platforms") == 1


def test_an_unmatched_platform_raises_rather_than_guessing():
    """Filing a ROM under a platform the operator did not choose is worse
    than a visible failure."""
    calls = []
    client = _client(calls)
    with pytest.raises(GaseousError) as exc:
        client.platform_id("dreamcast")
    assert "dreamcast" in str(exc.value)


# -- listing ---------------------------------------------------------------


def test_roms_for_game_always_sends_a_platform_id():
    """Omitting it takes the PlatformId == -1 branch, whose SQL joins a
    `Game` table that does not exist in schema 1042 -- the controller
    swallows the exception and 404s, so the bug is invisible."""
    calls = []
    client = _client(calls, roms={(1, 0): [ROM_ON_UNKNOWN]})
    client.roms_for_game(1, 0)
    listing = [c for c in calls if c.url.path.endswith("/roms")][0]
    assert listing.url.params["PlatformId"] == "0"


def test_an_empty_rom_listing_is_empty_not_an_error():
    """Gaseous returns 404 for a platform with no roms, because its
    controller cannot tell an empty result from a failure. A client that
    raised here could never list anything."""
    calls = []
    client = _client(calls, roms={})
    assert client.roms_for_game(1, 13) == []


def test_list_games_pages_until_a_short_page():
    calls = []
    many = [{"metadataMapId": i, "platformIds": [0]} for i in range(1, 251)]
    client = _client(calls, games=many)
    games = client.list_games()
    assert len(games) == 250
    pages = [c for c in calls if c.url.path == f"{API}/Games"]
    assert len(pages) == 2
    assert pages[0].url.params["pageNumber"] == "1"
    assert pages[1].url.params["pageNumber"] == "2"


def test_list_games_sends_the_fields_model_validation_demands():
    """GameSearchModel declares Name and Sorting non-nullable; a body
    without them is rejected with 400 before the controller runs."""
    calls = []
    client = _client(calls, games=[])
    client.list_games()
    body = [c for c in calls if c.url.path == f"{API}/Games"][0].content
    assert b'"Name"' in body and b'"Sorting"' in body


# -- upload ----------------------------------------------------------------


def test_upload_sends_multipart_and_reads_the_bare_guid_body(tmp_path):
    """The response is a GUID string, not JSON. Calling .json() on it
    raises, which would have reported every successful upload as a
    failure."""
    calls = []
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"PK\x03\x04rubik")
    client = _client(calls)

    session = client.upload_rom(rom, 13)

    assert session == "4a5f2b1c-0000-4000-8000-abcdefabcdef"
    upload = [c for c in calls if c.url.path == f"{API}/Roms"][0]
    assert upload.headers["content-type"].startswith("multipart/form-data")
    assert b"rubik.zip" in upload.content
    assert upload.url.params["OverridePlatformId"] == "13"


def test_an_upload_with_no_session_id_is_a_failure(tmp_path):
    """A 200 with an empty body would leave nothing to wait on, and the
    import would be reported done before it had started."""
    calls = []
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"data")
    client = _client(calls, upload_body="")
    with pytest.raises(GaseousError) as exc:
        client.upload_rom(rom, 13)
    assert "session id" in str(exc.value)


def test_import_states_is_a_post_with_an_empty_filter():
    """A POST even though it only reads: the status filter travels in the
    body."""
    calls = []
    client = _client(calls, imports=[{"sessionId": "abc", "state": "Completed"}])
    assert client.import_states() == [{"sessionId": "abc", "state": "Completed"}]
    req = [c for c in calls if c.url.path == f"{API}/Roms/Imports"][0]
    assert req.method == "POST"
    assert req.content == b"[]"


def test_unknown_platform_id_is_zero():
    """Not a sentinel this codebase invented: Metadata_Platform really
    carries a row with Id = 0 named "Unknown Platform", which is what
    makes listing that bucket possible."""
    assert UNKNOWN_PLATFORM_ID == 0
