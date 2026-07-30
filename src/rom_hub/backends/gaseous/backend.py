"""Gaseous as a `LibraryBackend`. The second implementation, and the one
that had to prove the seam was a seam rather than RomM with a wrapper.

Everything Gaseous-specific lives under this package, exactly as
everything RomM-specific lives under `backends/romm/`.

## Capabilities: two of six, and why the other four are absent

`CAPABILITIES` is `{IMPORT, SCAN}`. That is not caution; each of the
four omissions was checked against gaseous-server's source *and* against
a running v2.0.0-rc.3, and declaring any of them would turn a legible
up-front refusal into a 404 halfway through an import.

**COLLECTIONS.** `gaseous-server/Controllers/V1.0/CollectionsController.cs`
exists in the repository and is **commented out in its entirety** -- every
line of the file, imports included, is prefixed with `//`, so the class is
not compiled and the routes are not registered. The running server agrees:
its OpenAPI document lists no `/api/v1.1/Collections` path of any kind.
So `rom-hub import --collection` refuses before it downloads anything,
which is the whole point of the frozenset.

**METADATA.** There is no endpoint that writes a rom's fields. The only
verbs the live server exposes on a rom are
`GET|DELETE /Games/{MetadataMapId}/roms/{RomId}` -- read it or destroy it.
Gaseous does not model a rom's name or its provider ids as editable at
all: they are derived from the signature match and the metadata provider,
and the nearest thing to an edit
(`POST /Games/{MetadataMapId}/metadata`) re-points an entire *game* at a
different metadata source rather than applying a partial patch to a rom.
`MetadataPatch`'s absent-means-leave-alone contract cannot be honoured by
an endpoint with those semantics, and approximating it is precisely what
`LibraryBackend.update_rom` says a backend must not do.

**ARTWORK.** `ContentManagerController` looked like the artwork route --
the name and `fileupload/single` both suggest it -- and it is not. Its
`metadataid` parameter is a *metadata item* id, not a rom id; its
`contentType` is a `ContentManager.ContentType` (Screenshot, Video,
GlobalManual and similar); and it caps uploads at 50 MB. It attaches
supplementary media to a metadata record. Cover art in Gaseous comes from
the metadata provider and is served, never accepted, by
`GET /Games/{id}/{source}/cover`.

**FIRMWARE.** Gaseous knows about BIOS and will happily *serve* it --
`BiosController` has four routes and all four are reads. It has no route
that accepts one. Its only ingestion path is an MD5 allowlist of retail
dumps, which a clean-room replacement cannot match by construction. See
`upload_firmware` for the full account.

## What `IMPORT` and `SCAN` actually mean here

`SCAN` is declared for the same reason RomM declares it, and it is no
more optional: `POST /Roms` stages a file and queues it, and the
`ImportQueueProcessor` background task is what creates the database row.
See `imports.py`.

`IMPORT` is declared because upload and listing both work. But one thing
about it is worth stating plainly rather than discovering later:

**Gaseous chooses a ROM's platform itself, and ignores the one it is
given.** `RomsController.UploadRom` takes `OverridePlatformId`, stores it
on the `ImportStateItem`, and `ImportQueueProcessor` faithfully resolves
it to a `Platform` and passes it to
`ImportGame.ImportGameFile(..., OverridePlatform, ...)`. That method
never reads the parameter -- the name appears in its signature and its
doc-comment and nowhere in its body. The platform actually stored is
`discoveredSignature.Flags.PlatformId`, from the file signature lookup,
and the fallback signature plugin (`InspectFile`) never sets one. So a
ROM that is not in a signature database is filed under platform
`0`/"Unknown Platform" no matter what was asked for. Measured: a file
uploaded with `OverridePlatformId=13` (DOS) landed with
`PlatformId = 0` and `RelativePath = unknown/probe/probe.zip`.

`list_roms` is built around that fact rather than in spite of it -- see
its docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from rom_hub import env
from rom_hub.backends.base import (
    IMPORT,
    SCAN,
    BackendNotConfigured,
    CapabilityUnsupported,
)

from .client import UNKNOWN_PLATFORM_ID, GaseousClient, GaseousError
from .imports import ImportWaiter

BACKEND_NAME = "gaseous"

# The connection settings, each with the backend-neutral alias that also
# works -- the same convention `backends/romm/backend.py` uses, and for
# the same reason: `GASEOUS_*` is Gaseous' own vocabulary and correct for
# the backend it configures, while `ROM_HUB_BACKEND_*` lets a deployment
# switch backends without rewriting its unit file.
SETTING_NAMES = (
    ("GASEOUS_URL", "ROM_HUB_BACKEND_URL"),
    ("GASEOUS_USER", "ROM_HUB_BACKEND_USER"),
    ("GASEOUS_PASSWORD", "ROM_HUB_BACKEND_PASSWORD"),
)

#: Measured against Gaseous v2.0.0-rc.3, not assumed. Each omission is
#: justified in the module docstring, with the source that proves it.
CAPABILITIES = frozenset({IMPORT, SCAN})


def settings_from_env() -> tuple[str, str, str]:
    """`(base_url, username, password)` for Gaseous, from the environment.

    Names every missing variable at once rather than one per run.
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
            f"Gaseous is not configured: {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set. Set GASEOUS_URL "
            f"(e.g. http://gaseous.example:5198), GASEOUS_USER (the account's "
            f"e-mail address -- Gaseous rejects a bare username), and "
            f"GASEOUS_PASSWORD for an account with the Admin or Gamer role."
        )
    return values[0], values[1], values[2]


class GaseousBackend:
    """A `LibraryBackend` over one Gaseous server."""

    name = BACKEND_NAME

    # Mirrored onto the class so `backends.describe()` can read them
    # without opening a connection.
    #: How this backend's own project spells its name. Read by
    #: `describe()` so nothing outside this package has to keep a
    #: table of product names -- `romm`.title() is "Romm", which is
    #: wrong, and the fix belongs where the product is known.
    LABEL = "Gaseous"
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
        client: GaseousClient | None = None,
        waiter: ImportWaiter | None = None,
    ):
        self._client = client or GaseousClient(
            base_url, username, password, timeout=timeout, transport=transport
        )
        self._waiter = waiter
        # Sessions uploaded but not yet confirmed imported. `upload_rom`
        # returns nothing (the interface says so, and Gaseous' 200 carries
        # a session id rather than a rom id anyway), so the id has to be
        # remembered here for `scan_platform` to have something to wait on.
        self._pending_sessions: list[str] = []

    @classmethod
    def from_env(cls, **kwargs) -> "GaseousBackend":
        base_url, username, password = settings_from_env()
        return cls(base_url, username, password, **kwargs)

    # -- identity ----------------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES

    @property
    def base_url(self) -> str:
        return self._client.base_url

    @property
    def client(self) -> GaseousClient:
        """The underlying REST client, for Gaseous-specific tooling and tests.

        Nothing in the pipelines reaches through this -- if they did, the
        seam would not be a seam.
        """
        return self._client

    def authenticate(self) -> None:
        self._client.authenticate()

    # -- platforms ---------------------------------------------------------

    def platform_id(self, platform: str) -> int:
        return self._client.platform_id(platform)

    # -- roms --------------------------------------------------------------

    def list_roms(self, platform_id: int) -> list[dict]:
        """Every rom on `platform_id` **and on Gaseous' "unknown" platform**.

        The second half of that is not a convenience. Gaseous decides a
        ROM's platform from its file signature and discards the one the
        upload asked for (see the module docstring), so anything the Hub
        imports that is not already in a signature database lands on
        platform `0`. A listing scoped strictly to `platform_id` would
        therefore be empty of exactly the roms this Hub put there:
        `run_import` would dedup against nothing and then fail its own
        post-upload confirmation, on every import, while the ROM sat in
        the library perfectly fine.

        Widening to `{platform_id, 0}` rather than to the whole library is
        deliberate. It covers both buckets an upload can actually land in,
        and it stops there -- a whole-library listing would let
        `find_by_filename` skip an import because an unrelated ROM of the
        same name exists on a different, correctly-identified platform,
        and a false skip is the failure this codebase treats as worse than
        a visible one. Hash matches would stay exact either way; filename
        matches would not.

        The dicts are translated into the shape `rom_hub.dedup` reads --
        `id`, `fs_name`, `crc_hash`, `md5_hash`, `sha1_hash` -- because
        that vocabulary is the host's, not RomM's, and a backend that
        answered in its own spelling would be asking the shared dedup code
        to learn every backend's field names.

        One caveat that belongs with the hashes: `rom_hub.dedup.hash_file`
        reproduces *RomM's* digest, which for an archive is taken over the
        decompressed members. Gaseous hashes the raw file
        (`Classes/HashObject.cs` opens the path and digests the stream).
        For an archive the two therefore disagree by construction, and
        hash dedup silently cannot match; the filename check in
        `run_import` is what catches those, and it is reliable here for
        the same reason it is under RomM -- Gaseous stores the uploaded
        filename verbatim as the rom's `name`. Non-archive ROMs dedup by
        hash normally.
        """
        platforms = [platform_id]
        if platform_id != UNKNOWN_PLATFORM_ID:
            platforms.append(UNKNOWN_PLATFORM_ID)

        # Gaseous has no library-wide rom listing, so this is games-then-
        # roms. `platformIds` on the game record lets most of the per-game
        # calls be skipped rather than issued and thrown away.
        wanted = set(platforms)
        roms: list[dict] = []
        seen: set[int] = set()

        for game in self._client.list_games():
            map_id = game.get("metadataMapId")
            if not isinstance(map_id, int) or isinstance(map_id, bool):
                continue

            game_platforms = game.get("platformIds")
            if isinstance(game_platforms, list):
                candidates = [p for p in platforms if p in game_platforms]
            else:
                # No hint: ask for both rather than assume either.
                candidates = list(platforms)

            for candidate in candidates:
                for rom in self._client.roms_for_game(map_id, candidate):
                    if rom.get("platformId") not in wanted:
                        continue
                    rom_id = rom.get("id")
                    if isinstance(rom_id, int) and not isinstance(rom_id, bool):
                        if rom_id in seen:
                            continue
                        seen.add(rom_id)
                    roms.append(_as_hub_rom(rom))

        return roms

    def upload_rom(self, path: Path, platform_id: int) -> None:
        """Push one file into Gaseous under `platform_id` -- as a request.

        Returns nothing, matching the interface: Gaseous answers with an
        import *session* id, not a rom id, and the rom does not exist yet
        when it does. The session is remembered so `scan_platform` can
        wait for it, and the rom is identified afterwards the way
        `run_import` identifies it under any backend -- by looking it up
        in `list_roms`.

        `platform_id` is forwarded as `OverridePlatformId` but does not
        decide where the ROM lands; see the module docstring.
        """
        session = self._client.upload_rom(Path(path), platform_id)
        self._pending_sessions.append(session)

    def get_rom(self, rom_id: int) -> dict:
        """Not supported: Gaseous cannot look a rom up by id alone.

        `GET /Games/{MetadataMapId}/roms/{RomId}` needs the owning game's
        id as well, and the controller 404s when the pair does not match,
        so there is no id-only read to implement. This exists to satisfy
        the protocol and to fail with a sentence rather than an
        `AttributeError`; nothing reaches it, because it is only called
        under `METADATA`, which this backend does not declare.
        """
        raise CapabilityUnsupported(
            f"the 'gaseous' backend cannot fetch rom {rom_id} by id: Gaseous "
            f"addresses a rom as (game, rom) and exposes no id-only lookup. "
            f"This backend does not declare the 'metadata' capability."
        )

    def update_rom(
        self,
        rom_id: int,
        fields: dict[str, str],
        artwork: tuple[str, bytes, str] | None = None,
    ) -> dict:
        """Not supported. See the module docstring for the source evidence.

        Refusing is the contract: `LibraryBackend.update_rom` says a
        backend that cannot express a partial update must refuse rather
        than approximate one.
        """
        raise CapabilityUnsupported(
            f"the 'gaseous' backend cannot write metadata for rom {rom_id}: "
            f"Gaseous exposes only GET and DELETE on a rom, and models a "
            f"rom's fields as derived from its signature match rather than "
            f"as editable. Requested fields: {sorted(fields)}."
        )

    # -- firmware ----------------------------------------------------------

    def list_firmware(self, platform_id: int) -> list[dict]:
        """Not supported, and *not* because Gaseous has no BIOS support.

        Gaseous has plenty -- it just does not accept any. See
        `upload_firmware`; both halves of the `FIRMWARE` capability are
        one declaration, so neither is implemented.
        """
        raise CapabilityUnsupported(
            f"the 'gaseous' backend does not implement the 'firmware' "
            f"capability, so it cannot list firmware for platform "
            f"{platform_id}. See upload_firmware for why."
        )

    def upload_firmware(self, paths: list[Path], platform_id: int) -> None:
        """Not supported: Gaseous' BIOS API is read-only, by an allowlist.

        Verified in source rather than inferred, because the shape of the
        refusal matters here. `Controllers/V1.0/BiosController.cs` carries
        four routes and every one of them is a read: `GetBios()`,
        `GetBios(PlatformId)`, `GetBiosCompressedAsync` (a zip of a
        platform's BIOS) and `BiosFile` (one file). There is no POST, no
        PUT and no upload form -- the settings page that lists firmware
        (`wwwroot/pages/cards/settings/firmware.html`) is a table with two
        filter checkboxes and no file input.

        BIOS gets into Gaseous exactly one way, and it is not an API. A
        file arriving through the ordinary import path is hashed, and
        `ProcessQueue/Tasks/ImportQueueProcessor.cs` asks
        `Bios.BiosHashSignatureLookup(md5)` *before* treating it as a rom;
        a match moves the file into the firmware directory
        (`Classes/Bios.cs`, `ImportBiosFile`). That lookup walks
        `Support/PlatformMap.json`, a fixed table of MD5s belonging to
        specific retail BIOS dumps.

        Which makes this backend the wrong destination for this plugin's
        content twice over. There is no endpoint to call, and even the
        side door is an allowlist of *dumped* firmware hashes: a
        clean-room replacement BIOS matches nothing in it by construction,
        so it would be imported as a rom under "Unknown Platform" rather
        than stored as firmware. Better an up-front skip than that.
        """
        raise CapabilityUnsupported(
            f"the 'gaseous' backend cannot store firmware for platform "
            f"{platform_id}: Gaseous' BiosController exposes only reads "
            f"(GetBios, GetBios(PlatformId), the zip route and BiosFile), "
            f"and its only BIOS ingestion is an MD5 allowlist of retail "
            f"dumps in PlatformMap.json, which no clean-room replacement "
            f"can match. {len(paths)} file(s) were not sent."
        )

    # -- collections -------------------------------------------------------

    def ensure_collection(self, name: str) -> int:
        """Not supported: Gaseous' `CollectionsController` is commented out."""
        raise CapabilityUnsupported(
            f"the 'gaseous' backend cannot create collection {name!r}: "
            f"Gaseous' CollectionsController is commented out in its "
            f"entirety and the server registers no /Collections routes."
        )

    def add_to_collection(self, collection_id: int, rom_ids: list[int]) -> None:
        """Not supported. See `ensure_collection`."""
        raise CapabilityUnsupported(
            f"the 'gaseous' backend cannot add roms to collection "
            f"{collection_id}: Gaseous registers no /Collections routes."
        )

    # -- post-upload registration ------------------------------------------

    def scan_platform(self, platform_id: int) -> Any:
        """Wait for Gaseous to finish importing what was just uploaded.

        Not a refresh and not optional. `POST /Roms` only stages the file;
        the `ImportQueueProcessor` background task creates the database
        row, and until it has, `GET /Games/{id}/roms` cannot see the ROM.
        See `imports.py`.

        `platform_id` is ignored: Gaseous' import queue is keyed by upload
        session, not by platform, and the sessions to wait for are the
        ones this backend just created. The parameter stays because the
        `Scanner` protocol is shared with RomM, where a platform genuinely
        is the unit of a scan.

        A no-op when nothing was uploaded, so a dedup-only import does not
        block on someone else's queue.
        """
        sessions, self._pending_sessions = self._pending_sessions, []
        if not sessions:
            return None
        if self._waiter is None:
            self._waiter = ImportWaiter(self._client)
        return self._waiter.wait_for(sessions)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GaseousBackend":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _as_hub_rom(rom: dict) -> dict:
    """One Gaseous `GameRomItem` in the vocabulary `rom_hub.dedup` reads.

    Gaseous' own keys are kept alongside the translated ones. They cost
    nothing, and an operator reading a job's debug output should not have
    to guess which server a record came from.
    """
    translated = dict(rom)
    translated["fs_name"] = rom.get("name")
    for hub_key, gaseous_key in (
        ("crc_hash", "crc"),
        ("md5_hash", "md5"),
        ("sha1_hash", "sha1"),
    ):
        value = rom.get(gaseous_key)
        # dedup treats a non-string as "no match"; passing the key through
        # as None rather than omitting it keeps the shape predictable.
        translated[hub_key] = value if isinstance(value, str) else None
    return translated
