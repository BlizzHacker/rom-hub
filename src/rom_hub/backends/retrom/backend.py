"""Retrom as a `LibraryBackend`.

Everything Retrom-specific in the Hub lives under this package. Nothing
above it names Retrom, speaks protobuf, or knows that filing a ROM means
writing a file and asking for a rescan.

The three modules it composes each record something that was expensive to
establish from Retrom's source and is worth reading on its own:

* `grpcweb` -- why the transport is gRPC-Web over HTTP/1.1 rather than
  REST (there are only three REST routes and all of them are reads) or
  native gRPC (which would buy nothing and cost a binary dependency);
* `client` -- the RPCs and their field numbers, why there is nothing to
  authenticate against, and why `UpdateGameMetadata` has to read before it
  writes or it erases screenshot lists;
* `upload` -- that **Retrom has no upload API at all**, that its library is
  derived from the filesystem, and that its own WebDAV service under
  `/dav` is therefore the supported way to put a ROM where a scan will
  find it.

## Capabilities: four of six

`import`, `scan`, `metadata` and `artwork`. Each was exercised against a
real Retrom 0.8.4, which is why the set is stated here as data rather than
assumed by every caller.

**`collections` is absent because Retrom has no such concept.** There is no
collection message, service or column anywhere in
`packages/codegen/protos/retrom/` -- the only match for the word in the
entire schema is IGDB's own `collection` field in `igdbapi.proto`. So
`rom-hub import --collection` is refused up front, before anything is
downloaded, rather than after four gigabytes and a 404.

**`firmware` is absent for the same reason, and even more plainly.**
There is no BIOS or firmware message, service, column or directory
anywhere in the repository. The word `bios` appears in exactly two files,
both under `packages/client-web/src/lib/emulatorjs/` -- it is
EmulatorJS's own optional `biosUrl?: string` config field, a URL the web
player is handed, not a thing Retrom stores. So there is nothing to
upload to and nothing to list.

**`scan` is declared, and it is not a nicety.** A file written into a
content directory has no database row: `GetGames` does not list it and no
RPC exists that would create one. `LibraryService/UpdateLibrary` is what
turns the file into a game, exactly as RomM needs its socket.io scan.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from rom_hub import env
from rom_hub.backends.base import (
    ARTWORK,
    COLLECTIONS,
    IMPORT,
    METADATA,
    SCAN,
    BackendNotConfigured,
    CapabilityUnsupported,
)

from .client import RetromClient, RetromError
from .upload import WebDavClient, upload_file

BACKEND_NAME = "retrom"

# Retrom has no accounts, so there is nothing here but an address. See
# `client` for the source: no auth layer, no credential on any RPC, and
# "(Multi-)User authentication" still unchecked on the project's roadmap.
# The backend-neutral alias exists so a deployment that switches backends
# does not have to rewrite its unit file into a different product's
# vocabulary.
SETTING_NAMES = (("RETROM_URL", "ROM_HUB_BACKEND_URL"),)

#: Measured against Retrom 0.8.4, not assumed. See the module docstring.
CAPABILITIES = frozenset({IMPORT, METADATA, ARTWORK, SCAN})

#: Where a cover written by the Hub lives inside Retrom's public directory.
#: Namespaced so it can never collide with Retrom's own media cache, which
#: manages `public/media` and prunes it on metadata updates.
COVER_DIR = "public/rom-hub/covers"

#: `/rest/public` serves the same directory the DAV path above writes to
#: (`RetromDirs::public_dir()` is `data_dir/public`), so a cover put over
#: WebDAV is immediately fetchable over HTTP.
PUBLIC_URL_PREFIX = "/rest/public/rom-hub/covers"

#: Cover extensions by content type. A deterministic name -- `cover-<id>`
#: plus one of these -- keeps a plugin-supplied filename out of a URL path
#: entirely, and makes a re-enrich overwrite its own previous cover rather
#: than accumulate one file per attempt.
_COVER_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}
_DEFAULT_COVER_EXTENSION = ".img"

#: How long `scan_platform` waits for an asynchronous library scan to show
#: up in the listing, and how often it looks. `UpdateLibrary` answers
#: immediately with job ids and does the work in the background, so a
#: caller that listed the library the moment it returned would be racing
#: it. See `scan_platform`.
SCAN_TIMEOUT = 180.0
SCAN_POLL_SECONDS = 1.0


def settings_from_env() -> str:
    """Retrom's base URL, from the environment."""
    for primary, alias in SETTING_NAMES:
        value = env.get(primary) or env.get(alias)
        if value:
            return value
    raise BackendNotConfigured(
        "Retrom is not configured: RETROM_URL is not set. Set it to the "
        "Retrom service's base URL (e.g. http://retrom.example:5101) -- the "
        "port that serves the API, not a reverse proxy that only forwards "
        "the web client. Retrom has no accounts, so there is no user or "
        "password to set."
    )


class RetromBackend:
    """A `LibraryBackend` over one Retrom server."""

    name = BACKEND_NAME

    # Mirrored onto the class so `backends.describe()` can read them from
    # the class alone, without a second convention for finding a module's
    # constants.
    #: How this backend's own project spells its name. Read by
    #: `describe()` so nothing outside this package has to keep a
    #: table of product names -- `romm`.title() is "Romm", which is
    #: wrong, and the fix belongs where the product is known.
    LABEL = "Retrom"
    SETTING_NAMES = SETTING_NAMES
    CAPABILITIES = CAPABILITIES

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        client: RetromClient | None = None,
        dav: WebDavClient | None = None,
        scan_timeout: float = SCAN_TIMEOUT,
        scan_poll_seconds: float = SCAN_POLL_SECONDS,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = client or RetromClient(
            base_url, timeout=timeout, transport=transport
        )
        # Built lazily: an import that dedups or fails early should open no
        # second connection, and a test that injects one must be unambiguous.
        self._dav = dav
        self._transport = transport
        self._scan_timeout = scan_timeout
        self._scan_poll_seconds = scan_poll_seconds

    @classmethod
    def from_env(cls, **kwargs) -> "RetromBackend":
        return cls(settings_from_env(), **kwargs)

    # -- identity ----------------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def client(self) -> RetromClient:
        """The underlying gRPC-Web client.

        Exposed for Retrom-specific tooling and tests. Nothing in the
        pipelines reaches through this -- if they did, the seam would not
        be a seam.
        """
        return self._client

    @property
    def dav(self) -> WebDavClient:
        if self._dav is None:
            self._dav = WebDavClient(self._base_url, transport=self._transport)
        return self._dav

    def authenticate(self) -> None:
        """Retrom has no accounts; this proves it is reachable. See `client`."""
        self._client.authenticate()

    # -- platforms ---------------------------------------------------------

    def platform_id(self, platform: str) -> int:
        return self._client.platform_id(platform)

    # -- roms --------------------------------------------------------------

    def list_roms(self, platform_id: int) -> list[dict]:
        return self._client.list_games(platform_id)

    def upload_rom(self, path: Path, platform_id: int) -> None:
        # The return value (the DAV path written) is discarded here rather
        # than at the call site: it is not a rom id and nothing above this
        # package should be able to mistake it for one. The ROM is
        # identified afterwards by looking it up in `list_roms`, which is
        # also what proves it landed.
        upload_file(self._client, self.dav, Path(path), platform_id)

    def get_rom(self, rom_id: int) -> dict:
        return self._client.get_game(rom_id)

    def update_rom(
        self,
        rom_id: int,
        fields: dict[str, str],
        artwork: tuple[str, bytes, str] | None = None,
    ) -> dict:
        """Apply a partial metadata patch, and a cover if one was fetched.

        The cover is written into Retrom's public directory over WebDAV and
        `cover_url` is pointed at the `/rest/public` route that serves the
        same directory -- Retrom's metadata model stores a cover as a URL,
        not as bytes, so this is the only way to attach one that Retrom
        will actually display.

        The URL is absolute, built from the configured base URL. A relative
        one would be resolved differently by each client, and the desktop
        client is not the web client.
        """
        cover_url = None
        if artwork is not None:
            _filename, data, content_type = artwork
            extension = _COVER_EXTENSIONS.get(
                (content_type or "").split(";")[0].strip().lower(),
                _DEFAULT_COVER_EXTENSION,
            )
            name = f"cover-{int(rom_id)}{extension}"
            # WebDAV's MKCOL has no `-p`, and Retrom creates only
            # `public/` itself -- so the cover directory is made here,
            # every level of it, before the first cover is written.
            self.dav.makedirs(COVER_DIR)
            self.dav.put_bytes(
                f"{COVER_DIR}/{name}", data, content_type or "application/octet-stream"
            )
            cover_url = f"{self._base_url}{PUBLIC_URL_PREFIX}/{name}"

        return self._client.update_game_metadata(
            rom_id, fields, cover_url=cover_url
        )

    # -- firmware ----------------------------------------------------------
    #
    # Retrom has none either. Same reason these exist: a caller that got
    # here past the capability check gets a sentence, not an
    # AttributeError.

    def list_firmware(self, platform_id: int) -> list[dict]:
        raise CapabilityUnsupported(
            f"the {BACKEND_NAME!r} backend cannot list firmware for platform "
            f"{platform_id}: Retrom has no firmware concept. There is no "
            f"BIOS or firmware message, service or column anywhere in its "
            f"schema."
        )

    def upload_firmware(self, paths: list[Path], platform_id: int) -> None:
        raise CapabilityUnsupported(
            f"the {BACKEND_NAME!r} backend cannot store firmware: Retrom has "
            f"no firmware concept, so there is nowhere for a BIOS to go and "
            f"nothing that would index it. {len(paths)} file(s) were not "
            f"sent."
        )

    # -- collections -------------------------------------------------------
    #
    # Retrom has none. These exist so a caller that reached them past the
    # capability check gets the same sentence the check would have given,
    # rather than an AttributeError.

    def ensure_collection(self, name: str) -> int:
        raise CapabilityUnsupported(
            f"the {BACKEND_NAME!r} backend cannot create collection {name!r}: "
            f"Retrom has no collections. There is no collection message, "
            f"service or column anywhere in its schema."
        )

    def add_to_collection(self, collection_id: int, rom_ids: list[int]) -> None:
        raise CapabilityUnsupported(
            f"the {BACKEND_NAME!r} backend cannot add to a collection: "
            f"Retrom has no collections."
        )

    # -- post-upload registration ------------------------------------------

    def scan_platform(self, platform_id: int) -> Any:
        """Register what was just written. Not optional for Retrom.

        A file placed in a content directory has no database row until
        `LibraryService/UpdateLibrary` walks the filesystem and inserts
        one. `GetGames` does not list it before that, and no RPC exists
        that would create the row directly.

        **The scan is asynchronous.** `UpdateLibrary` answers immediately
        with a list of job ids and does the work in the background, so
        returning the moment it replies would leave the caller's
        confirmation racing the scan and reporting a ROM as missing that
        was merely not indexed yet. This therefore polls the platform's
        listing until it changes, or until `scan_timeout`.

        Returning after the timeout without a change is deliberate and is
        not a claim of success. `run_import` confirms the ROM by finding it
        in the library afterwards; that is the post-condition, and this
        method's only job is to not make it race. Raising here instead
        would turn "the scan is slow" into "the upload failed", which is
        the one message that makes an operator upload the file again.
        """
        before = self._count(platform_id)
        job_ids = self._client.update_library()

        deadline = time.monotonic() + self._scan_timeout
        while time.monotonic() < deadline:
            time.sleep(self._scan_poll_seconds)
            now = self._count(platform_id)
            if now is not None and now != before:
                break

        return {"job_ids": job_ids}

    def _count(self, platform_id: int) -> int | None:
        """How many games the platform holds, or `None` if it cannot be read.

        A failed listing must not be mistaken for "it changed" -- that
        would end the wait on the first transient error and hand
        `run_import` an un-scanned library. `None` is therefore skipped by
        the poll rather than compared, so a blip costs one interval and
        not the scan.
        """
        try:
            return len(self._client.list_games(platform_id))
        except RetromError:
            return None

    def close(self) -> None:
        self._client.close()
        if self._dav is not None:
            self._dav.close()

    def __enter__(self) -> "RetromBackend":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
