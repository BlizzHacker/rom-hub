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

import httpx

_EXCERPT_LIMIT = 300

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
    "collections.read collections.write"
)


class RommError(Exception):
    """Any RomM API failure: non-2xx responses, auth failures, transport errors."""


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
    # Three calls, orchestrated by romm_hub.romm.upload.upload_file:
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
        look it up in the library by hash -- see `romm_hub.importer`.
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
