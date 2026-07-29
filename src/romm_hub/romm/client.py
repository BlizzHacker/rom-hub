"""HTTP client for RomM 4.9.2's REST API.

Auth is OAuth2 password grant — the token endpoint takes a form-encoded
body, not JSON. Every non-2xx response is converted to a `RommError`
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

        `Body_token_api_token_post` also has a `scope` field defaulting to
        "". We deliberately do not send one — a guessed scope string could
        be silently wrong in a way that only bites at the first real write
        (e.g. an upload), so this is left to the server default and flagged
        for a live check in Task 8 rather than guessed here.
        """
        try:
            resp = self._client.post(
                "/api/token",
                data={
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
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
            raise RommError(
                f"{method} {path} failed: authentication/authorization failed "
                f"({resp.status_code}): {_excerpt(resp)}"
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
        return self._authorized_request(
            "GET", "/api/roms", params={"platform_ids": platform_id}
        ).json()

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
        """POST /api/roms/upload/start -> the new upload session (its id
        is read by the caller to address the following PUT/complete/cancel
        calls). `platform_id` must be the integer id from `platform_id()`,
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

    def upload_chunk(self, upload_id: str, index: int, chunk: bytes) -> None:
        """PUT /api/roms/upload/{upload_id} with the raw chunk bytes as
        the body, once per chunk."""
        self._authorized_request(
            "PUT",
            f"/api/roms/upload/{upload_id}",
            headers={"x-chunk-index": str(index)},
            content=chunk,
        )

    def complete_upload(self, upload_id: str) -> dict:
        """POST /api/roms/upload/{upload_id}/complete once every chunk has
        been sent."""
        resp = self._authorized_request(
            "POST", f"/api/roms/upload/{upload_id}/complete"
        )
        return resp.json()

    def cancel_upload(self, upload_id: str) -> None:
        """POST /api/roms/upload/{upload_id}/cancel -- called on any
        failure so a half-uploaded file does not linger server-side."""
        self._authorized_request("POST", f"/api/roms/upload/{upload_id}/cancel")
