"""HTTP client for RomM 4.9.2's REST API.

Auth is OAuth2 password grant — the token endpoint takes a form-encoded
body, not JSON, and it must be given an explicit `scope` (see
`REQUIRED_SCOPES`) or the token it returns can do nothing at all. Every
non-2xx response is converted to a `RommError`
carrying the status and a body excerpt before it reaches a caller; a raw
`httpx.HTTPStatusError` (or any other httpx exception) must never escape
this module.

`x-upload-platform` (used by the chunked upload in Task 4) is an integer
platform id, not a slug, so `platform_id()` resolves slug -> id via
`GET /api/platforms` and caches the whole listing on first use.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from rom_hub.backends.base import BackendError
from rom_hub.types import PROVIDER_ID_FIELDS, RAW_METADATA_FIELDS

_EXCERPT_LIMIT = 300

# What `update_rom` may write. RomM's update body also accepts `fs_name`,
# `url_cover` and `url_manual`, and none of the three is here: `fs_name`
# renames the file on disk, which is not a metadata edit, and the two URL
# fields make *RomM* fetch a plugin-named URL server-side, which is a way
# around the Hub's allowlist and its artwork size ceiling both. See
# `MetadataPatch`'s docstring for the measurement.
#
# `summary` was added on 2026-08-01 after measuring that it round-trips
# where the eight `raw_*_metadata` fields do not. It is the only field on
# this endpoint that can carry a release date, a developer or a genre into
# the library at all.
UPDATABLE_ROM_FIELDS = (
    frozenset({"name", "summary"}) | PROVIDER_ID_FIELDS | RAW_METADATA_FIELDS
)

# `GET /api/heartbeat` -> METADATA_SOURCES flag, per provider-id field.
#
# The flag says whether RomM holds that provider's credentials. It is a
# public endpoint -- measured unauthenticated, HTTP 200 -- so this is
# always answerable, even before `authenticate()`.
#
# Only the fields RomM re-fetches on are listed. RomM 4.9.2's `update_rom`
# has a "Fetch metadata from external sources" block covering flashpoint,
# launchbox, ra, moby, ss and igdb; `sgdb_id`, `tgdb_id`, `hasheous_id`,
# `hltb_id` and `libretro_id` are stored and nothing else, so they are
# absent here and never gated.
HEARTBEAT_FLAGS = {
    "igdb_id": "IGDB_API_ENABLED",
    "ss_id": "SS_API_ENABLED",
    "moby_id": "MOBY_API_ENABLED",
    "ra_id": "RA_API_ENABLED",
    "launchbox_id": "LAUNCHBOX_API_ENABLED",
    "flashpoint_id": "FLASHPOINT_API_ENABLED",
}

# Of those six, the fields whose re-fetch actually *fails* when the
# provider is not configured. This is a measured set, not a derived one,
# and the difference between it and `HEARTBEAT_FLAGS` is the whole point:
# writing `igdb_id` to a keyless RomM answers 200 and stores the number,
# while writing `ra_id` to one answers 500 and stores nothing. Measured
# one field at a time against RomM 4.9.2 with every source disabled; see
# `rom_hub.backends.base` for the table.
#
# Narrow by measurement. A field added here on suspicion would withhold a
# perfectly good id and make every enrich quietly poorer, which is the
# failure mode this module is trying to end rather than repeat.
UNSAFE_WITHOUT_CREDENTIALS = frozenset({"ra_id"})

# How many roms to ask for per `GET /api/roms` page. The server defaults
# `limit` to 50; asking for more just means fewer round trips over the
# whole platform, which dedup walks in full on every import.
_ROMS_PAGE_SIZE = 500

# The OAuth2 scopes the import pipeline needs, space-separated exactly as
# `Body_token_api_token_post.scope` wants them.
#
# This is not optional. RomM's token endpoint defaults `scope` to "" and
# happily issues a *valid* token carrying `"scopes":""` -- authentication
# succeeds, and then every single API call answers 403. Measured against a
# real RomM 4.9.2:
#
#   no scope   -> JWT has "scopes":"" -> GET /api/platforms -> 403
#   these      -> JWT echoes them     -> /api/platforms, /api/roms,
#                                        /api/collections all 200, and
#                                        POST /api/roms/upload/start reaches
#                                        400 (bad platform id) rather than
#                                        403, proving upload is covered
#
# One scope per thing the pipeline actually does, and nothing else. RomM
# also exposes assets.*, devices.*, firmware.*, roms.user.*, users.* and
# tasks.run; asking for `users.*` would give the Hub's token authority over
# other people's accounts and `tasks.run` would let it drive the server's
# task runner, neither of which importing a ROM has any business doing.
REQUIRED_SCOPES = (
    "me.read "
    "roms.read roms.write "
    "platforms.read platforms.write "
    "collections.read collections.write "
    # `rom-hub firmware install` reads the platform's firmware list to
    # avoid sending a second copy, then posts the files. Added when that
    # caller appeared, not on the theory that a token might want them:
    # `backend/endpoints/firmware.py` guards `get_platform_firmware` with
    # Scope.FIRMWARE_READ and `add_firmware` with Scope.FIRMWARE_WRITE, so
    # a token without these gets the same "valid, then 403 on every call"
    # that omitting `scope` entirely produces.
    "firmware.read firmware.write"
)


class RommError(BackendError):
    """Any RomM API failure: non-2xx responses, auth failures, transport errors.

    A `BackendError` so that a caller which is deliberately backend-
    agnostic -- `rom_hub.cli.main`, for one -- can catch every backend's
    failures with one name, without importing the RomM package to learn
    what its exception is called.
    """


def _excerpt(resp: httpx.Response) -> str:
    try:
        text = resp.text
    except Exception:
        return "<unreadable response body>"
    return text[:_EXCERPT_LIMIT]


class RommClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._username = username
        self._password = password
        self._token: str | None = None
        self._platform_cache: dict[str, int] = {}
        self._platforms_loaded = False
        self._metadata_sources: dict[str, bool] | None = None
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RommClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- auth -----------------------------------------------------------

    @property
    def base_url(self) -> str:
        """The server root, without a trailing slash.

        Needed because registering an upload is not a REST operation --
        `rom_hub.backends.romm.scan` has to open a socket.io connection to the
        same server this client talks to, and must not be handed a second,
        independently-configured URL that could drift from this one.
        """
        return str(self._client.base_url).rstrip("/")

    def bearer_token(self) -> str:
        """The cached access token, authenticating first if needed.

        Exposed for the socket.io scan connection, which carries the same
        credentials as the REST calls rather than logging in a second time.
        """
        if self._token is None:
            self.authenticate()
        assert self._token is not None
        return self._token

    def authenticate(self) -> None:
        """POST /api/token (OAuth2 password grant, form-encoded) and cache
        the bearer token for subsequent requests.

        `scope` is required in practice even though the schema defaults it
        to "": omitting it yields a valid token with no scopes, which then
        403s on every call. See `REQUIRED_SCOPES`.
        """
        try:
            resp = self._client.post(
                "/api/token",
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                    "scope": REQUIRED_SCOPES,
                },
            )
        except httpx.HTTPError as exc:
            raise RommError(f"authentication request to RomM failed: {exc}") from exc

        if resp.status_code != 200:
            raise RommError(
                f"authentication failed ({resp.status_code}): {_excerpt(resp)}"
            )
        try:
            token = resp.json()["access_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RommError(
                f"authentication response did not contain an access_token: {exc}"
            ) from exc
        self._token = token

    # -- internal request plumbing ---------------------------------------

    def _authorized_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._token is None:
            self.authenticate()

        headers = kwargs.pop("headers", None) or {}
        headers = {**headers, "Authorization": f"Bearer {self._token}"}

        try:
            resp = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RommError(f"{method} {path} failed: {exc}") from exc

        if resp.status_code in (401, 403):
            # Distinct from the generic branch below: a write (or any call)
            # that gets bounced for auth reasons must say so plainly, not
            # just surface a bare status code.
            #
            # 403 especially. It is almost never a credentials problem -- the
            # token was accepted, it just is not allowed to do this -- and the
            # overwhelmingly likely cause is a token issued without the scopes
            # the pipeline needs. An operator will not guess that from "403",
            # and will go hunting for a password problem that does not exist.
            hint = ""
            if resp.status_code == 403:
                hint = (
                    " -- the token was accepted but is not authorized for this "
                    "call, which usually means it was issued without the "
                    f"required scopes ({REQUIRED_SCOPES})"
                )
            raise RommError(
                f"{method} {path} failed: authentication/authorization failed "
                f"({resp.status_code}): {_excerpt(resp)}{hint}"
            )
        if not (200 <= resp.status_code < 300):
            raise RommError(
                f"{method} {path} failed ({resp.status_code}): {_excerpt(resp)}"
            )
        return resp

    # -- what this server is configured for --------------------------------

    def metadata_sources(self) -> dict[str, bool]:
        """Which metadata providers this RomM holds credentials for.

        `GET /api/heartbeat` -> `METADATA_SOURCES`, a flat map of
        `IGDB_API_ENABLED`-style flags. Public: measured unauthenticated
        against a live 4.9.2 and answered 200, so this works before
        `authenticate()` and is not gated on a scope.

        Cached for the life of the client. An operator does not add an
        IGDB key halfway through one `rom-hub enrich`, and asking once per
        rom would put a request in front of every single write.

        Returns `{}` -- not an exception -- when the server does not answer
        or answers a shape this does not recognise. The caller is
        `provider_id_policy`, whose whole job is to be a safety net; a
        safety net that fails the operation when it cannot be consulted is
        worse than the failure it was added to prevent.
        """
        if self._metadata_sources is None:
            try:
                payload = self._authorized_request("GET", "/api/heartbeat").json()
            except (RommError, ValueError):
                return {}
            sources = (
                payload.get("METADATA_SOURCES") if isinstance(payload, dict) else None
            )
            if not isinstance(sources, dict):
                return {}
            self._metadata_sources = {
                str(key): bool(value)
                for key, value in sources.items()
                if isinstance(value, bool)
            }
        return dict(self._metadata_sources)

    # -- platforms --------------------------------------------------------

    def list_platforms(self) -> list[dict]:
        return self._authorized_request("GET", "/api/platforms").json()

    def platform_id(self, slug: str) -> int:
        """Resolve a platform slug (e.g. "dos") to RomM's integer platform id.

        PlatformSchema carries both `slug` and `fs_slug`; either may be the
        one a plugin knows about, so both are cached. Matching is
        case-insensitive. The full listing is fetched and cached on first
        use, so resolving any number of slugs afterward — including ones
        not asked for yet — costs no further HTTP requests.

        Getting this wrong means a ROM files under the wrong system, which
        is worse than a visible failure — so an unmatched slug raises
        rather than guessing.
        """
        if not self._platforms_loaded:
            for platform in self.list_platforms():
                platform_id_value = platform.get("id")
                if platform_id_value is None:
                    continue
                for key in (platform.get("slug"), platform.get("fs_slug")):
                    if key:
                        self._platform_cache[key.lower()] = platform_id_value
            self._platforms_loaded = True

        lookup = slug.lower()
        if lookup not in self._platform_cache:
            raise RommError(f"no RomM platform matches slug {slug!r}")
        return self._platform_cache[lookup]

    # -- roms ---------------------------------------------------------------

    def list_roms(self, platform_id: int) -> list[dict]:
        """Every rom on `platform_id`, as a flat list of rom dicts.

        `GET /api/roms` does **not** answer a bare array. It answers
        `CustomLimitOffsetPage_SimpleRomSchema_`:

            {"items": [...], "total": N, "limit": 50, "offset": 0,
             "char_index": {...}, "rom_id_index": [...],
             "filter_values": {...}}

        Handing that envelope back as if it were the listing is how this
        went wrong: `find_duplicate` iterates what it is given, and
        iterating a dict yields its *keys* -- seven strings. Non-dict
        entries are skipped as malformed, so the result was silently
        "no roms match", every time, on a library of any size. That broke
        both callers at once: dedup never detected a duplicate, and the
        post-upload confirmation could never find the rom it had just
        uploaded, so every import ended FAILED.

        `limit` also defaults to 50 server-side, so a platform with more
        roms than that would only ever be compared against its first page.
        Paging is therefore not optional either: a missed duplicate is a
        second copy in the user's library.

        The walk stops on a short or empty page as well as on `total`, so
        a server whose `total` disagrees with what it actually returns
        cannot spin this loop forever.
        """
        roms: list[dict] = []
        offset = 0
        while True:
            payload = self._authorized_request(
                "GET",
                "/api/roms",
                params={
                    "platform_ids": platform_id,
                    "limit": _ROMS_PAGE_SIZE,
                    "offset": offset,
                },
            ).json()

            # A bare array is not what RomM 4.9.2 sends, but accepting one
            # costs nothing and keeps this from being pinned to a single
            # server version's response shape.
            if isinstance(payload, list):
                return payload
            if not isinstance(payload, dict):
                raise RommError(
                    f"GET /api/roms returned {type(payload).__name__}, expected "
                    f"a paginated object or a list"
                )

            items = payload.get("items")
            if not isinstance(items, list):
                raise RommError(
                    "GET /api/roms response has no 'items' list; keys present: "
                    f"{sorted(payload.keys())}"
                )

            roms.extend(items)
            if not items or len(items) < _ROMS_PAGE_SIZE:
                # A short page is the last page. This is the guard that ends
                # the walk even when `total` is wrong or missing.
                return roms

            offset += len(items)
            total = payload.get("total")
            if isinstance(total, int) and offset >= total:
                return roms

    def get_rom(self, rom_id: int) -> dict:
        """`GET /api/roms/{id}` -> the full rom record.

        Used to build the `RomRef` handed to a `metadata` plugin, and to
        show an operator what an enrich actually changed.
        """
        return self._authorized_request("GET", f"/api/roms/{rom_id}").json()

    def update_rom(
        self,
        rom_id: int,
        fields: dict[str, str],
        artwork: tuple[str, bytes, str] | None = None,
    ) -> dict:
        """`PUT /api/roms/{id}` -- apply a metadata patch to one rom.

        **Only the keys in `fields` are sent.** RomM applies what it is
        given and leaves everything else alone (measured, below), so a
        partial patch is safe exactly as long as the absent field is
        genuinely absent from the request. Forwarding an unset field as an
        empty part is how a plugin that only knew the name would erase a
        user's curated ids.

        The encoding was measured against a real RomM 4.9.2 rather than
        inferred, because the schema declares `multipart/form-data` only
        and the truth is more forgiving in one direction and much less in
        another:

            multipart, name + igdb_id, no artwork part   -> 200, applied
            multipart, name only, afterwards             -> 200, igdb_id kept
            urlencoded (no multipart at all), name only  -> 200, igdb_id kept
            multipart with an EMPTY artwork part         -> **400**
            multipart with an unknown extra part         -> 200, ignored

        So this deliberately does **not** copy `ensure_collection`'s
        empty-`files=` trick. There it is the only way to make httpx
        multipart-encode a bodyless create; here the same empty part is a
        400 that fails every artwork-less update. RomM reads the body with
        `request.form()`, which parses urlencoded too, so an update with no
        artwork simply goes as urlencoded and only a real cover promotes
        the request to multipart.

        `artwork` is `(filename, bytes, content_type)`. To *remove* a cover
        RomM takes `?remove_cover=true`; an empty artwork part is not it.
        """
        unknown = sorted(set(fields) - UPDATABLE_ROM_FIELDS)
        if unknown:
            # These keys originate in a plugin's MetadataPatch. That model
            # already refuses anything unknown; this is the layer that has
            # to hold if it ever stops doing so, because some of RomM's
            # other form fields are not metadata at all -- `fs_name`
            # renames the file on disk.
            raise RommError(
                f"refusing to update rom {rom_id}: {unknown} are not metadata "
                f"fields this client may write (permitted: "
                f"{sorted(UPDATABLE_ROM_FIELDS)})"
            )

        kwargs: dict = {"data": dict(fields)}
        if artwork is not None:
            filename, data, content_type = artwork
            kwargs["files"] = {"artwork": (filename, data, content_type)}

        resp = self._authorized_request("PUT", f"/api/roms/{rom_id}", **kwargs)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    # -- firmware -------------------------------------------------------------
    #
    # Read from RomM's own `backend/endpoints/firmware.py`, not inferred.
    # Both routes hang off the `/firmware` prefix that `APIRouter` mounts
    # under `/api`, and the shapes below are the decorated signatures:
    #
    #   @protected_route(router.get,  "", [Scope.FIRMWARE_READ])
    #   def get_platform_firmware(request, platform_id: int | None = None)
    #       -> list[FirmwareSchema]
    #
    #   @protected_route(router.post, "", [Scope.FIRMWARE_WRITE])
    #   async def add_firmware(request, platform_id: int,
    #                          files: list[UploadFile] = File(...))
    #       -> AddFirmwareResponse
    #
    # Two things in those signatures decide the calls below. `platform_id`
    # is a *query* parameter on both -- it is a bare `int` with no `Body`
    # or `Form` marker, so FastAPI takes it from the query string even on
    # the POST, and sending it as a form field is a 422. And `files` is
    # `File(...)`, a repeated multipart part, so the whole set goes in one
    # request rather than one request per file.

    def list_firmware(self, platform_id: int) -> list[dict]:
        """`GET /api/firmware?platform_id=` -> this platform's firmware.

        A bare list, not a paginated envelope -- the endpoint's return type
        is `list[FirmwareSchema]` and it does no paging at all, which is
        the one way this differs from `list_roms` and the reason that
        function's page-walking is not copied here.
        """
        payload = self._authorized_request(
            "GET", "/api/firmware", params={"platform_id": platform_id}
        ).json()
        if not isinstance(payload, list):
            raise RommError(
                f"GET /api/firmware returned {type(payload).__name__}, "
                f"expected a list"
            )
        return payload

    def upload_firmware(self, paths: list[Path], platform_id: int) -> dict:
        """`POST /api/firmware?platform_id=` -- store these files as firmware.

        One request for the whole set. Each file is read into memory,
        which is safe here in a way it would not be for a ROM: firmware is
        kilobytes, and `rom_hub.firmware.MAX_FIRMWARE_BYTES` has already
        bounded every one of these at download time.

        Unlike a ROM, there is no scan afterwards. `add_firmware` writes
        the file, runs `scan_firmware` and inserts the database row inside
        the one request, so the firmware is queryable the moment this
        returns -- which is why `rom-hub firmware install` can prove its
        own work with `list_firmware`.
        """
        if not paths:
            raise RommError("refusing to upload firmware: no files were given")
        files = []
        for path in paths:
            path = Path(path)
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise RommError(f"cannot read firmware file {path}: {exc}") from exc
            if not data:
                raise RommError(f"refusing to upload empty firmware file {path}")
            # The part name is repeated, which is how `list[UploadFile]`
            # is populated; httpx encodes a list of 2-tuples that way.
            files.append(("files", (path.name, data, "application/octet-stream")))

        resp = self._authorized_request(
            "POST",
            "/api/firmware",
            params={"platform_id": platform_id},
            files=files,
        )
        try:
            return resp.json()
        except ValueError:
            return {}

    # -- collections ----------------------------------------------------------

    def list_collections(self) -> list[dict]:
        return self._authorized_request("GET", "/api/collections").json()

    def ensure_collection(self, name: str) -> int:
        """Return the id of the collection named `name`, creating it if absent.

        POST /api/collections is `multipart/form-data`
        (`Body_add_collection_api_collections_post` mixes `Form` fields with
        an optional `artwork: binary` file field, which is what makes
        FastAPI require multipart even when no file is attached), NOT JSON.
        httpx only encodes as multipart when a `files=` mapping is given —
        passing `data=` alone always produces urlencoded — so an empty
        artwork part is included to force the encoding, mirroring what a
        browser sends for an HTML file input left empty.
        """
        for collection in self.list_collections():
            if collection.get("name") == name:
                return collection["id"]
        resp = self._authorized_request(
            "POST",
            "/api/collections",
            data={"name": name, "description": "", "url_cover": ""},
            files={"artwork": ("", b"", "application/octet-stream")},
        )
        return resp.json()["id"]

    def add_to_collection(self, collection_id: int, rom_ids: list[int]) -> None:
        self._authorized_request(
            "POST",
            f"/api/collections/{collection_id}/roms",
            json={"rom_ids": rom_ids},
        )

    # -- chunked upload ---------------------------------------------------
    #
    # Three calls, orchestrated by rom_hub.backends.romm.upload.upload_file:
    #   start -> N x upload_chunk -> complete, with cancel on any failure.
    # Kept here (not just in upload.py) so every RomM HTTP call funnels
    # through the same _authorized_request auth/error handling as the rest
    # of the client.

    def start_upload(
        self, platform_id: int, filename: str, total_size: int, total_chunks: int
    ) -> dict:
        """POST /api/roms/upload/start -> the new upload session. The
        response carries the session id under the key `upload_id` (RomM's
        backend/endpoints/roms/upload.py returns
        `{"upload_id": upload_id}`) -- callers must read that key, not
        `id`. `platform_id` must be the integer id from `platform_id()`,
        never a slug."""
        resp = self._authorized_request(
            "POST",
            "/api/roms/upload/start",
            headers={
                "x-upload-platform": str(platform_id),
                "x-upload-filename": filename,
                "x-upload-total-size": str(total_size),
                "x-upload-total-chunks": str(total_chunks),
            },
        )
        return resp.json()

    def upload_chunk(self, upload_id: str, index: int, chunk: bytes) -> dict:
        """PUT /api/roms/upload/{upload_id} with the raw chunk bytes as
        the body, once per chunk. Returns the JSON body
        (`{"received": <int>, "total": <int>}`) in case a caller wants to
        track the server's own received-chunk count."""
        resp = self._authorized_request(
            "PUT",
            f"/api/roms/upload/{upload_id}",
            headers={"x-chunk-index": str(index)},
            content=chunk,
        )
        return resp.json()

    def complete_upload(self, upload_id: str) -> dict:
        """POST /api/roms/upload/{upload_id}/complete once every chunk has
        been sent.

        RomM answers with a bare `201` and **no body at all** -- its
        `complete_chunked_upload` ends
        `return Response(status_code=status.HTTP_201_CREATED)`. So there
        is no rom id here to read, and `resp.json()` on an empty body
        raises `JSONDecodeError`, which upload_file's cancel-and-re-raise
        would have reported as a failed upload on every real upload there
        has ever been.

        An empty body is therefore the expected success shape and becomes
        `{}`. A body that is present but unparseable also becomes `{}`:
        the endpoint promises nothing, no caller reads the value, and the
        upload genuinely did succeed. Callers that need the new rom's id
        look it up in the library by hash -- see `rom_hub.importer`.
        """
        resp = self._authorized_request(
            "POST", f"/api/roms/upload/{upload_id}/complete"
        )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def cancel_upload(self, upload_id: str) -> None:
        """POST /api/roms/upload/{upload_id}/cancel -- called on any
        failure so a half-uploaded file does not linger server-side."""
        self._authorized_request("POST", f"/api/roms/upload/{upload_id}/cancel")
