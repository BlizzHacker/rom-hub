"""RomM as a `LibraryBackend`. The first implementation, and the reference one.

Everything RomM-specific in the Hub lives under this package. Nothing
above it names RomM, imports `httpx` on RomM's behalf, or knows that
registering an upload takes a socket.io round trip.

This class is deliberately thin: it composes the three modules that were
already here -- `client` (REST), `upload` (the three-step chunked upload)
and `scan` (the socket.io registration) -- and presents them as the flat
set of verbs the pipelines actually use. Those modules were not merged
into it, because each of them documents a piece of RomM behaviour that
was expensive to find out and is worth reading on its own:

* `/api/token` needs an explicit `scope` or it issues a valid token that
  403s on every call (`client.REQUIRED_SCOPES`);
* `/complete` answers a bare 201 with no body, and the file is on disk
  with **no database row**, so a socket.io `scan` is what actually
  registers the ROM (`scan`);
* the server derives its own expected chunk length from the headers it
  was sent, so a mismatch hangs the upload forever (`upload`).

## Capabilities

RomM 4.9.2 supports all five. That is not a rubber stamp -- each one was
exercised against a real server, which is exactly why the set is stated
here as data rather than assumed by every caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from rom_hub import env
from rom_hub.backends.base import (
    ALL_CAPABILITIES,
    BackendNotConfigured,
)

from .client import RommClient
from .scan import Scanner, SocketIOScanner
from .upload import upload_file

BACKEND_NAME = "romm"

# The connection settings, each with the backend-neutral name that also
# works. `ROMM_URL` and friends are not deprecated and were not renamed:
# they are RomM's name, they are already in shell profiles and systemd
# units on LXC 104, and they are correct for the backend they configure.
# The `ROM_HUB_BACKEND_*` spellings exist so a deployment that switches
# backends does not have to rewrite its unit file to a different product's
# vocabulary.
SETTING_NAMES = (
    ("ROMM_URL", "ROM_HUB_BACKEND_URL"),
    ("ROMM_USER", "ROM_HUB_BACKEND_USER"),
    ("ROMM_PASSWORD", "ROM_HUB_BACKEND_PASSWORD"),
)

#: Measured against RomM 4.9.2, not assumed. See the module docstring.
CAPABILITIES = ALL_CAPABILITIES


def settings_from_env() -> tuple[str, str, str]:
    """`(base_url, username, password)` for RomM, from the environment.

    Raises naming **every** missing variable at once rather than one per
    run: an operator configuring this for the first time should need one
    attempt, not three.
    """
    values = []
    missing = []
    for primary, alias in SETTING_NAMES:
        value = env.get(primary) or env.get(alias)
        if not value:
            missing.append(primary)
        values.append(value)

    if missing:
        raise BackendNotConfigured(
            f"RomM is not configured: {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set. Set ROMM_URL "
            f"(e.g. http://romm.example:8080), ROMM_USER, and ROMM_PASSWORD "
            f"to a RomM account permitted to upload."
        )
    return values[0], values[1], values[2]


class RommBackend:
    """A `LibraryBackend` over one RomM server."""

    name = BACKEND_NAME

    # Mirrored onto the class so `backends.describe()` can read them from
    # the class alone, without a second convention for finding a module's
    # constants. (A class body's name lookup falls through to globals, so
    # each right-hand side is the module constant above.)
    #: How this backend's own project spells its name. Read by
    #: `describe()` so nothing outside this package has to keep a
    #: table of product names -- `romm`.title() is "Romm", which is
    #: wrong, and the fix belongs where the product is known.
    LABEL = "RomM"
    SETTING_NAMES = SETTING_NAMES
    CAPABILITIES = CAPABILITIES

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        client: RommClient | None = None,
        scanner: Scanner | None = None,
    ):
        self._client = client or RommClient(
            base_url, username, password, timeout=timeout, transport=transport
        )
        # Built lazily: constructing a SocketIOScanner opens nothing, but
        # not building one at all keeps an injected stand-in unambiguous.
        self._scanner = scanner

    @classmethod
    def from_env(cls, **kwargs) -> "RommBackend":
        base_url, username, password = settings_from_env()
        return cls(base_url, username, password, **kwargs)

    # -- identity ----------------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def client(self) -> RommClient:
        """The underlying REST client.

        Exposed for RomM-specific tooling and tests. Nothing in the
        pipelines reaches through this -- if they did, the seam would not
        be a seam.
        """
        return self._client

    def authenticate(self) -> None:
        self._client.authenticate()

    # -- platforms ---------------------------------------------------------

    def platform_id(self, platform: str) -> int:
        return self._client.platform_id(platform)

    # -- roms --------------------------------------------------------------

    def list_roms(self, platform_id: int) -> list[dict]:
        return self._client.list_roms(platform_id)

    def upload_rom(self, path: Path, platform_id: int) -> None:
        # The return value is discarded here rather than at the call site:
        # /complete answers 201 with no body, so there is nothing in it,
        # and letting a `{}` travel upward invites a caller to read an id
        # out of it one day.
        upload_file(self._client, path, platform_id)

    def get_rom(self, rom_id: int) -> dict:
        return self._client.get_rom(rom_id)

    def update_rom(
        self,
        rom_id: int,
        fields: dict[str, str],
        artwork: tuple[str, bytes, str] | None = None,
    ) -> dict:
        return self._client.update_rom(rom_id, fields, artwork=artwork)

    # -- collections -------------------------------------------------------

    def ensure_collection(self, name: str) -> int:
        return self._client.ensure_collection(name)

    def add_to_collection(self, collection_id: int, rom_ids: list[int]) -> None:
        self._client.add_to_collection(collection_id, rom_ids)

    # -- post-upload registration ------------------------------------------

    def scan_platform(self, platform_id: int) -> Any:
        """Register what was just uploaded. Not optional for RomM.

        `/complete` writes the file into the library directory and creates
        no database row; `GET /api/roms` does not list it and no REST
        endpoint exists that would. See `rom_hub.backends.romm.scan`.
        """
        if self._scanner is None:
            self._scanner = SocketIOScanner(self._client)
        return self._scanner.scan_platform(platform_id)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RommBackend":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
