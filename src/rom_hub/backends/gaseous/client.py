"""HTTP client for Gaseous 2.0's REST API.

Everything here was derived by reading gaseous-server's C# controllers and
then measured against a real `ghcr.io/gaseous-project/gaseousserver:v2.0.0-rc.3`.
Where the two disagreed, the running server won and the discrepancy is
recorded in a comment -- that happened more than once, and it is why none
of this is inferred from the OpenAPI document the server also publishes.

## Auth is a cookie, not a token

`AccountController.Login` calls ASP.NET Identity's
`SignInManager.PasswordSignInAsync`, which issues an **authentication
cookie**. There is no bearer token anywhere in Gaseous and no `/api/token`
equivalent, so `httpx.Client`'s cookie jar *is* the credential store; the
client must be long-lived for the same reason.

Two consequences that cost real time to find:

* an unauthenticated API call answers **302** (a redirect to the login
  page), not 401. With `follow_redirects=True` that surfaces as a `200`
  carrying HTML, and JSON parsing fails somewhere far away with a
  message about the wrong thing. So this client follows nothing and
  treats 302 on an API path as exactly what it is: not authenticated.
* `LoginViewModel.Email` is declared `[Required]` and captioned "Email or
  Username" at HEAD, but the shipped build validates it as an e-mail
  address and rejects a bare username with 400. Configure an e-mail.

## Listing roms is per game, per platform

Gaseous has no "list the library" endpoint. Roms hang off games:
`GET /Games/{MetadataMapId}/roms?PlatformId=<id>`. So a listing is
`POST /Games` (paged) followed by one call per game.

`PlatformId` is **not optional in practice**. Omitting it takes the
`PlatformId == -1` branch of `Classes.Roms.GetRomsAsync`, whose SQL joins
a table that does not exist in schema 1042:

    MySqlException: Table 'gaseous.Game' doesn't exist

The controller catches every exception and returns `NotFound`, so the bug
presents as a blanket 404 with nothing in the response to explain it.
A platform-scoped call takes a different, working query. An empty result
also 404s, which is why `roms_for_game` reads 404 as "no rows" rather
than as an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from rom_hub.backends.base import BackendError

_EXCERPT_LIMIT = 300

# Gaseous' own bucket for a file whose platform it could not determine.
# `Metadata_Platform` really does carry a row with `Id = 0`, named
# "Unknown Platform", so this is a first-class platform to the server and
# not a null -- which is what makes listing it possible at all.
UNKNOWN_PLATFORM_ID = 0

# How many games to ask for per `POST /Games` page.
_GAMES_PAGE_SIZE = 200

# `GameSearchModel` declares `Name` and `Sorting` as non-nullable, and
# ASP.NET model validation rejects a body without them with 400 before the
# controller runs. An empty `Name` is the "match everything" spelling.
_MATCH_EVERYTHING = {
    "Name": "",
    "Sorting": {"SortBy": "NameThe", "SortAscending": True},
}


class GaseousError(BackendError):
    """Any Gaseous API failure: non-2xx responses, auth failures, transport errors.

    A `BackendError` so a deliberately backend-agnostic caller --
    `rom_hub.cli.main` -- catches this and `RommError` with one name.
    """


def _excerpt(resp: httpx.Response) -> str:
    try:
        text = resp.text
    except Exception:
        return "<unreadable response body>"
    return text[:_EXCERPT_LIMIT]


class GaseousClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        api_version: str = "1.1",
    ):
        self._username = username
        self._password = password
        self._authenticated = False
        self._platform_cache: dict[str, int] = {}
        self._platforms_loaded = False
        self._api = f"/api/v{api_version}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            # Never follow: an unauthenticated API call 302s to the login
            # page, and following that turns "you are logged out" into a
            # 200 full of HTML. See the module docstring.
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GaseousClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    # -- auth -------------------------------------------------------------

    def authenticate(self) -> None:
        """POST /api/v1.1/Account/Login and keep the identity cookie.

        The body is JSON (`LoginViewModel`), unlike RomM's form-encoded
        OAuth2 grant, and success is a 200 whose body is
        `{"success": true}`. A 2FA-enabled account answers 200 with
        `{"requiresTwoFactor": true}` instead -- which is *not* a
        successful login, and is reported as its own failure because the
        alternative is a client that believes it is authenticated and
        then 302s on every subsequent call.
        """
        try:
            resp = self._client.post(
                f"{self._api}/Account/Login",
                json={
                    "Email": self._username,
                    "Password": self._password,
                    "RememberMe": True,
                },
            )
        except httpx.HTTPError as exc:
            raise GaseousError(
                f"authentication request to Gaseous failed: {exc}"
            ) from exc

        if resp.status_code != 200:
            hint = ""
            if resp.status_code == 400 and "e-mail" in _excerpt(resp).lower():
                hint = (
                    " -- Gaseous validates this field as an e-mail address, so "
                    "GASEOUS_USER must be the account's e-mail, not its username"
                )
            raise GaseousError(
                f"authentication failed ({resp.status_code}): {_excerpt(resp)}{hint}"
            )

        try:
            body = resp.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("requiresTwoFactor"):
            raise GaseousError(
                "Gaseous accepted the password but requires a two-factor code, "
                "which the Hub cannot supply. Use an account with 2FA disabled."
            )

        self._authenticated = True

    # -- internal request plumbing ----------------------------------------

    def _authorized_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self._authenticated:
            self.authenticate()

        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GaseousError(f"{method} {path} failed: {exc}") from exc

        # 302 is Gaseous' spelling of 401 on an API path: the cookie is
        # missing or expired and it is redirecting to the login form.
        if resp.status_code in (301, 302, 307, 401, 403):
            raise GaseousError(
                f"{method} {path} failed: not authenticated to Gaseous "
                f"({resp.status_code}). The identity cookie was rejected or "
                f"has expired; check GASEOUS_USER/GASEOUS_PASSWORD and that "
                f"the account still exists."
            )
        if not (200 <= resp.status_code < 300):
            raise GaseousError(
                f"{method} {path} failed ({resp.status_code}): {_excerpt(resp)}"
            )
        return resp

    @staticmethod
    def _json(resp: httpx.Response, what: str):
        try:
            return resp.json()
        except ValueError as exc:
            raise GaseousError(
                f"{what} did not return JSON: {_excerpt(resp)}"
            ) from exc

    # -- platforms ---------------------------------------------------------

    def list_platforms(self) -> list[dict]:
        resp = self._authorized_request("GET", f"{self._api}/Platforms")
        payload = self._json(resp, "GET /Platforms")
        if not isinstance(payload, list):
            raise GaseousError(
                f"GET /Platforms returned {type(payload).__name__}, expected a list"
            )
        return payload

    def platform_id(self, name: str) -> int:
        """Resolve a platform name or slug to Gaseous' integer platform id.

        Gaseous' platform records carry `slug` ("dos"), `name` ("DOS") and
        often `alternative_name` ("PC DOS"); a plugin may know any of the
        three, so all three are cached. Matching is case-insensitive.

        Raises rather than guessing, for the reason `LibraryBackend` gives:
        filing a ROM under a platform the operator did not choose is worse
        than a visible failure.

        **This id is what the Hub asks for, not necessarily what Gaseous
        uses.** See `GaseousBackend.upload_rom` -- the server derives a
        ROM's real platform from its file signature. The resolution still
        matters (it is what scopes the dedup listing) but it is not a
        promise about where the ROM lands.
        """
        if not self._platforms_loaded:
            for platform in self.list_platforms():
                if not isinstance(platform, dict):
                    continue
                value = platform.get("id")
                if not isinstance(value, int) or isinstance(value, bool):
                    continue
                for key in (
                    platform.get("slug"),
                    platform.get("name"),
                    platform.get("alternative_name"),
                ):
                    if isinstance(key, str) and key:
                        self._platform_cache.setdefault(key.lower(), value)
            self._platforms_loaded = True

        lookup = name.lower()
        if lookup not in self._platform_cache:
            raise GaseousError(f"no Gaseous platform matches {name!r}")
        return self._platform_cache[lookup]

    # -- games and roms ----------------------------------------------------

    def list_games(self) -> list[dict]:
        """Every game in the library, as a flat list, fully paged.

        `POST /Games` answers `{"games": [...], "summary": {...}}`. Paging
        is 1-based: `pageNumber=0` means "no paging, return everything",
        which is fine for a small library and unwise for a real one, so
        this walks pages and stops on a short one.
        """
        games: list[dict] = []
        page = 1
        while True:
            resp = self._authorized_request(
                "POST",
                f"{self._api}/Games",
                params={
                    "pageNumber": page,
                    "pageSize": _GAMES_PAGE_SIZE,
                    "returnSummary": "false",
                    "returnGames": "true",
                },
                json=dict(_MATCH_EVERYTHING),
            )
            payload = self._json(resp, "POST /Games")
            if not isinstance(payload, dict):
                raise GaseousError(
                    f"POST /Games returned {type(payload).__name__}, expected "
                    f"an object"
                )
            batch = payload.get("games")
            if not isinstance(batch, list):
                # `games` is absent rather than empty when nothing matches.
                return games
            games.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < _GAMES_PAGE_SIZE:
                return games
            page += 1

    def roms_for_game(self, metadata_map_id: int, platform_id: int) -> list[dict]:
        """`GET /Games/{id}/roms?PlatformId=<p>` -> that game's roms on `p`.

        A 404 is "no rows", not an error. Gaseous' controller wraps the
        whole lookup in `try { ... } catch { return NotFound(); }`, so an
        empty result and a genuine failure are the same status code; since
        an empty platform is overwhelmingly the common case and a caller
        that treated it as fatal could never list anything, this reads it
        as empty. See the module docstring for why `PlatformId` is always
        sent.
        """
        try:
            resp = self._authorized_request(
                "GET",
                f"{self._api}/Games/{metadata_map_id}/roms",
                params={"PlatformId": platform_id},
            )
        except GaseousError as exc:
            if "(404)" in str(exc):
                return []
            raise

        payload = self._json(
            resp, f"GET /Games/{metadata_map_id}/roms"
        )
        if not isinstance(payload, dict):
            return []
        items = payload.get("gameRomItems")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    # -- upload ------------------------------------------------------------

    def upload_rom(self, path: Path, platform_id: int) -> str:
        """`POST /Roms` (multipart) -> the import session id.

        The response body is a **bare GUID string**, not JSON and not
        quoted -- `RomsController.UploadRom` ends `return
        Ok(sessionid.ToString())`. Calling `.json()` on it raises, so the
        text is taken verbatim and only stripped of the quotes a future
        version might add.

        `OverridePlatformId` is sent because it is the parameter the API
        documents, but it does not decide anything today; see
        `GaseousBackend.upload_rom`.

        This is a whole-file upload, deliberately: Gaseous exposes no
        chunked upload endpoint at all, so there is nothing to chunk into.
        `RomsController` carries `[DisableRequestSizeLimit]` and
        `RequestFormLimits(MultipartBodyLengthLimit = long.MaxValue)`,
        which is what makes that safe for large ROMs. (The 50 MB cap that
        exists in Gaseous belongs to `ContentManagerController`, which
        uploads *attachments*, not ROMs.)
        """
        path = Path(path)
        with path.open("rb") as handle:
            resp = self._authorized_request(
                "POST",
                f"{self._api}/Roms",
                params={"OverridePlatformId": platform_id},
                files={"file": (path.name, handle, "application/octet-stream")},
            )

        session = (resp.text or "").strip().strip('"')
        if not session:
            raise GaseousError(
                f"uploading {path.name!r} returned {resp.status_code} but no "
                f"import session id, so the import cannot be tracked"
            )
        return session

    def import_states(self) -> list[dict]:
        """`POST /Roms/Imports` -> this user's import queue.

        A POST, not a GET, even though it only reads -- the route takes
        the status filter in the body. An empty JSON array means "no
        filter"; the filter is deliberately not used, because the states
        worth waiting for are precisely the ones that have not been
        reached yet.
        """
        resp = self._authorized_request(
            "POST",
            f"{self._api}/Roms/Imports",
            headers={"Content-Type": "application/json"},
            content=json.dumps([]),
        )
        payload = self._json(resp, "POST /Roms/Imports")
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
