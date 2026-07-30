"""Gaseous HTTP client: cookie auth, platform resolution, listing, upload.

Every call is mocked with httpx.MockTransport. No test here may require a
live Gaseous instance.

The fixtures below are trimmed copies of real responses from
`ghcr.io/gaseous-project/gaseousserver:v2.0.0-rc.3`, not invented shapes
-- including the parts that are surprising (a bare GUID as an upload
response body, a 404 for an empty rom listing, a 302 instead of a 401).

## The mock answers from a recording, and refuses anything it has not seen

`POST /Games` and `POST /Roms` are not stubbed to succeed. They are
answered out of `tests/fixtures/gaseous/api-contract.json`, which is a
`curl` recording of both live Gaseous generations, and a request whose
shape is not in that recording raises `UncapturedRequest` instead of
being waved through.

That is the point of this file, and it is a direct response to how the
`POST /Games` 400 shipped: the previous mock accepted **any** body with a
200 and the test asserted only that `Name` and `Sorting` were in it, so
the suite was checking this project's own belief about the request rather
than the server's. A test built that way cannot fail when the server
changes -- it can only fail when we change. Now the required-field rules
of *both* generations are enforced against every request the client makes,
and adding a field to `_MATCH_EVERYTHING` without capturing it against a
real server fails the suite.
"""

import json
from pathlib import Path

import httpx
import pytest

from rom_hub.backends.gaseous.client import (
    UNKNOWN_PLATFORM_ID,
    GaseousClient,
    GaseousError,
)

API = "/api/v1.1"

# -- the recording ---------------------------------------------------------

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "gaseous" / "api-contract.json").read_text(
        encoding="utf-8"
    )
)["generations"]

#: The two live servers the recording came from, keyed by what each one
#: answers at `GET /System/Version`. Tests that care about cross-version
#: behaviour parametrize over this; tests that do not run against 2.0,
#: which is the generation this client targets.
GENERATIONS = tuple(CONTRACT)
CURRENT = "2.0.0.0"

assert set(GENERATIONS) == {"1.7.14.0", "2.0.0.0"}, GENERATIONS


class UncapturedRequest(AssertionError):
    """The client sent a shape no live server was ever asked about.

    Raised rather than answered, because the alternative -- inventing a
    200 for it -- is precisely the hole this file exists to close. Fix it
    by capturing the new shape against real servers (see the fixture's
    README), not by loosening the mock.
    """


def _replay(generation: str, prefix: str, sent: dict) -> tuple[str, dict]:
    """Find the captured case for `sent`, or refuse.

    Returns `(case_name, capture)`. Matching is on the request body
    exactly: a body that differs by one key is a different request as far
    as ASP.NET model validation is concerned, and treating it as
    equivalent is how a 400 hides.
    """
    cases = CONTRACT[generation]
    for name, capture in cases.items():
        if name.startswith(prefix) and capture.get("request") == sent:
            return name, capture
    raise UncapturedRequest(
        f"the client sent a {prefix}* body that no capture covers on Gaseous "
        f"{generation}:\n  {json.dumps(sent, sort_keys=True)}\n"
        f"Captured bodies on that server:\n"
        + "\n".join(
            f"  {name}: {json.dumps(c['request'], sort_keys=True)}"
            for name, c in cases.items()
            if name.startswith(prefix) and "request" in c
        )
        + "\nCapture it against a live Gaseous before relying on it; see "
        "tests/fixtures/gaseous/README.md."
    )

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


def _multipart_field_names(content: bytes) -> set[str]:
    """The part names in a multipart body, ignoring `filename=`.

    Crude on purpose -- this only has to tell `file` from `files`, which
    is the whole difference between the two generations' upload. The
    negative lookbehind matters: every part here also carries a
    `filename="..."`, and counting that as a field name makes the
    assertion vacuous.
    """
    import re

    found = re.findall(rb'(?<!file)name="([^"]+)"', content or b"")
    return {name.decode("utf-8", "replace") for name in found}


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
    generation=CURRENT,
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
            # Validation first, exactly as ASP.NET runs it: the controller
            # never sees a body that fails model binding, so neither does
            # the paging below.
            _, capture = _replay(generation, "games.", json.loads(request.content))
            if capture["status"] != 200:
                return httpx.Response(capture["status"], json=capture["response"])
            page = int(request.url.params.get("pageNumber", 1))
            size = int(request.url.params.get("pageSize", 200))
            start = (page - 1) * size
            batch = games[start : start + size]
            # The success envelope differs between the two: 1.7.x carries
            # `count` and `alphaList` alongside `games`, 2.0 answers
            # `games` alone. Both are taken from the recording rather than
            # written here, so the client is read against each server's
            # real envelope.
            body = dict(capture["response"])
            body["games"] = batch
            if "count" in body:
                body["count"] = len(batch)
            return httpx.Response(200, json=body)

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
            fields = _multipart_field_names(request.content)
            case = (
                "roms.multipart-field-files"
                if "files" in fields
                else "roms.multipart-field-file"
            )
            capture = CONTRACT[generation][case]
            if capture["status"] != 200:
                return httpx.Response(capture["status"], json=capture["response"])
            if not isinstance(capture["response"], str):
                # 1.7.x answering the 2.0 field name: 200, and nothing
                # stored. Replayed verbatim, because a client that reads
                # this as a session id is the bug being guarded against.
                return httpx.Response(200, json=capture["response"])
            # A bare GUID, not JSON. Ok(sessionid.ToString()). The literal
            # is overridable so a test can assert on a known value.
            return httpx.Response(upload_status, text=upload_body)

        if path == f"{API}/Roms/Imports":
            capture = CONTRACT[generation]["roms.imports"]
            if capture["status"] != 200:
                # 1.7.x registers no such route at all.
                return httpx.Response(capture["status"], text="")
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


@pytest.mark.parametrize("generation", GENERATIONS)
def test_list_games_sends_a_body_every_gaseous_generation_accepts(generation):
    """The regression this file was rewritten for.

    `GameSearchModel` has no `[Required]` anywhere; gaseous-server builds
    with `<Nullable>enable</Nullable>`, so every non-nullable reference
    property is implicitly required unless a member initializer leaves a
    non-null value behind. The two shipped declarations therefore demand
    different things -- 1.7.x wants the five list filters, 2.0 wants
    `Sorting` -- and the client used to send only 2.0's, which 1.7.14
    answers 400.

    Parametrized over both recordings, so this fails if `list_games` ever
    goes back to a body only one of them accepts.
    """
    calls = []
    client = _client(calls, games=[], generation=generation)
    assert client.list_games() == []
    sent = json.loads([c for c in calls if c.url.path == f"{API}/Games"][0].content)
    case, capture = _replay(generation, "games.", sent)
    assert capture["status"] == 200
    assert case == "games.union-body"


@pytest.mark.parametrize("generation", GENERATIONS)
def test_the_empty_filter_is_a_list_not_a_null(generation):
    """`null` is not "unset": implicit-required tests the bound value, so
    an explicit null fails 1.7.x's validation exactly as an absent key
    does. Measured -- same 400, same five field names."""
    calls = []
    client = _client(calls, games=[], generation=generation)
    client.list_games()
    sent = json.loads([c for c in calls if c.url.path == f"{API}/Games"][0].content)
    for field in ("Platform", "Genre", "GameMode", "PlayerPerspective", "Theme"):
        assert sent[field] == [], f"{field} must be [] -- null is rejected"


def test_the_old_body_really_is_rejected_by_the_older_server():
    """The evidence, not a comment: the recording's 400 for the shape this
    client used to send, replayed through the same transport the client
    uses. If this ever passes, the fixture stopped describing 1.7.14."""
    old_body = {"Name": "", "Sorting": {"SortBy": "NameThe", "SortAscending": True}}
    case, capture = _replay("1.7.14.0", "games.", old_body)
    assert case == "games.minimal-2.0-body"
    assert capture["status"] == 400
    assert set(capture["response"]["errors"]) == {
        "Genre",
        "Theme",
        "GameMode",
        "Platform",
        "PlayerPerspective",
    }
    # And the same body is fine on 2.0, which is why it shipped.
    assert _replay("2.0.0.0", "games.", old_body)[1]["status"] == 200


def test_a_400_from_the_listing_is_reported_with_the_servers_own_words():
    """A validation failure has to name the fields. The operator-facing
    half of the regression was a `GaseousError` whose text was the only
    clue about which server generation was on the other end."""
    calls = []
    client = _client(calls, games=[], generation="1.7.14.0")

    # Force the old body back through the real client, the way a
    # regression would.
    from rom_hub.backends.gaseous import client as client_module

    original = client_module._MATCH_EVERYTHING
    client_module._MATCH_EVERYTHING = {
        "Name": "",
        "Sorting": {"SortBy": "NameThe", "SortAscending": True},
    }
    try:
        with pytest.raises(GaseousError) as exc:
            client.list_games()
    finally:
        client_module._MATCH_EVERYTHING = original

    assert "400" in str(exc.value)
    assert "Genre" in str(exc.value)


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


def test_the_upload_uses_the_2_0_multipart_field_name(tmp_path):
    """`file`, singular. 2.0's signature is `UploadRom(IFormFile file,...)`
    and it 400s on `files`; 1.7.x's is `List<IFormFile> files` and binds
    nothing from `file`. The two cannot both be satisfied by one part."""
    calls = []
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"PK\x03\x04rubik")
    client = _client(calls)
    client.upload_rom(rom, 13)

    upload = [c for c in calls if c.url.path == f"{API}/Roms"][0]
    assert _multipart_field_names(upload.content) == {"file"}


def test_an_older_gaseous_that_stored_nothing_is_a_failure_not_a_session(tmp_path):
    """The dangerous shape in the recording.

    1.7.x answers the 2.0 upload with **200** and `{"count":0,"size":0}`
    having kept no bytes: the `file` part binds to nothing, so its
    `foreach` never runs. That body is truthy, so it used to become the
    "session id" the import then waited on -- a job reported as under way
    for a ROM that was never written. It has to fail, and it has to say
    why.
    """
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"PK\x03\x04rubik")
    calls = []
    client = _client(calls, generation="1.7.14.0")

    with pytest.raises(GaseousError) as exc:
        client.upload_rom(rom, 13)

    message = str(exc.value)
    assert "stored nothing" in message
    assert "1.7" in message
    assert "Nothing was uploaded" in message


def test_a_guid_body_is_still_accepted_unchanged(tmp_path):
    """The guard is narrow: only a JSON object carrying `count` is
    refused. A bare GUID, a quoted GUID and anything else non-object go
    through, so a future response shape is not pre-emptively rejected."""
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"data")
    for body in ("4a5f2b1c-0000-4000-8000-abcdefabcdef", '"quoted-guid"'):
        calls = []
        client = _client(calls, upload_body=body)
        assert client.upload_rom(rom, 13) == body.strip('"')


def test_import_states_is_absent_before_2_0():
    """`POST /Roms/Imports` 404s on 1.7.x -- the route is not registered
    at all. Recorded so that a future attempt to support that generation
    starts from what the server does rather than from a guess."""
    assert CONTRACT["1.7.14.0"]["roms.imports"]["status"] == 404
    assert CONTRACT["2.0.0.0"]["roms.imports"]["status"] == 200


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
