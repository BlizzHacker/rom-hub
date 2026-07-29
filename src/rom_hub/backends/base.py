"""What the Hub needs from a library server, and nothing more.

RomM is not the only self-hosted ROM library manager; Gaseous and Retrom
exist and an operator running one of those wants the plugin ecosystem too.
The plugins were already portable -- a plugin returns a `FetchPlan` or a
`MetadataPatch`, which are *descriptions*, and nothing in a plugin has
ever known RomM exists. The only RomM-specific code was the executor, so
that is the seam this file names.

**This interface was derived, not designed.** Every method below is here
because `rom_hub.importer` or `rom_hub.metadata` already called it on
`RommClient`; nothing was added on the theory that a backend might want
it. That is deliberate: an interface invented ahead of its second
implementation is an interface shaped like its first one anyway, only
with more surface to be wrong about. Whatever Gaseous or Retrom turn out
to need that is not here will be added when there is a real caller for
it.

## Capabilities are declarations, not hopes

`capabilities()` is what makes honest degradation possible. RomM has
collections; another backend may not. Without a declaration the operator
finds out by watching `rom-hub import --collection "Shooters"` download
four gigabytes, upload them, and then fail on a 404 from an endpoint that
does not exist -- with the ROM half-filed and the message useless.

So the host asks first and says so plainly, before any work:

    collections are not supported by the 'example' backend, so
    --collection "Shooters" cannot be honoured

That is the whole point of the frozenset. It is checked at the top of the
command *and* again in the pipeline, because a `--collection` flag is not
the only way a collection gets named -- a plugin's own `FetchPlan` can
carry one, and that path must fail just as legibly.

## What is deliberately absent

There is no "create a platform" method. `platform_id()` resolves a name
and raises when there is no match, and that refusal is load-bearing:
filing a ROM under a platform the operator did not choose is worse than a
visible failure, and it is the kind of wrong that is not noticed for
months.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class BackendError(Exception):
    """Any library-backend failure: transport, auth, refusal, misconfiguration."""


class UnknownBackend(BackendError):
    """`ROM_HUB_BACKEND` names something that is not installed."""


class BackendNotConfigured(BackendError):
    """The selected backend has no usable connection settings."""


class CapabilityUnsupported(BackendError):
    """The active backend cannot do what was asked, and said so up front."""


# -- capability vocabulary -------------------------------------------------
#
# Deliberately coarse. One name per decision the host actually has to make
# about a backend, not one per HTTP endpoint: a finer vocabulary would
# have to be re-agreed by every backend author and would still not be
# checked anywhere.

#: Accept a ROM upload, and list what is already there so a duplicate is
#: found before the bytes are sent. The two are one capability because a
#: backend that cannot be listed cannot be safely uploaded to either --
#: dedup that could not run is not dedup that passed.
IMPORT = "import"

#: Group roms into a named collection.
COLLECTIONS = "collections"

#: Write a rom's metadata fields.
METADATA = "metadata"

#: Attach cover art to a rom. Separate from METADATA because a backend may
#: well accept a name and reject an image, and the artwork is fetched over
#: the network *before* the write -- an unsupported cover should cost no
#: download at all.
ARTWORK = "artwork"

#: Needs (and performs) an explicit registration step after an upload
#: before the ROM is visible. RomM does; a backend that indexes on receipt
#: does not, and declares nothing here while still implementing
#: `scan_platform` as a no-op.
SCAN = "scan"

ALL_CAPABILITIES = frozenset({IMPORT, COLLECTIONS, METADATA, ARTWORK, SCAN})

#: One line each, for `rom-hub backend info`. A capability list is only
#: useful to an operator who can tell which command each name governs.
CAPABILITY_HELP = {
    IMPORT: "accept a ROM upload, and list the library so a duplicate is caught first",
    COLLECTIONS: "group roms into a named collection (rom-hub import --collection)",
    METADATA: "write a rom's metadata fields (rom-hub enrich)",
    ARTWORK: "attach cover art to a rom",
    SCAN: "needs an explicit registration step after an upload",
}


@runtime_checkable
class Scanner(Protocol):
    """The post-upload registration step, on its own.

    Split out from the backend so `run_import` can be handed a stand-in
    that opens no socket, and so a backend that needs no scan is not
    forced to pretend it has one.
    """

    def scan_platform(self, platform_id: int) -> Any: ...


@runtime_checkable
class LibraryBackend(Protocol):
    """One library server, as the import and metadata pipelines see it."""

    #: Short identifier, as spelled in `ROM_HUB_BACKEND`.
    name: str

    def capabilities(self) -> frozenset[str]:
        """What this backend can actually do. See the module docstring."""
        ...

    def authenticate(self) -> None:
        """Establish credentials, or raise `BackendError` saying why not."""
        ...

    # -- platforms ---------------------------------------------------------

    def platform_id(self, platform: str) -> int:
        """Resolve a platform name to this backend's own id for it.

        Raises rather than guessing. See "What is deliberately absent".
        """
        ...

    # -- roms --------------------------------------------------------------

    def list_roms(self, platform_id: int) -> list[dict]:
        """Every rom on `platform_id`, for dedup and post-upload confirmation.

        A flat list, fully paged. The dicts carry at least `id`, `fs_name`
        and whatever hashes the backend records -- `rom_hub.dedup` reads
        them defensively and treats an unknown shape as "no match", never
        as an error.
        """
        ...

    def upload_rom(self, path: Path, platform_id: int) -> None:
        """Push one file into the library under `platform_id`.

        Returns nothing on purpose. RomM's completion endpoint answers a
        bare 201 with no body, so there is no id to hand back, and a
        caller that believed one would be reading a value only one
        backend could ever supply. The rom is identified afterwards by
        looking it up in `list_roms` by the digest already computed for
        dedup -- which doubles as proof it actually landed.
        """
        ...

    def get_rom(self, rom_id: int) -> dict:
        """One rom's full record, for building the `RomRef` a plugin sees."""
        ...

    def update_rom(
        self,
        rom_id: int,
        fields: dict[str, str],
        artwork: tuple[str, bytes, str] | None = None,
    ) -> dict:
        """Apply a partial metadata patch.

        **Only the keys in `fields` may be written.** Absent means leave
        alone, never means blank: a plugin that knows only the name must
        not be able to erase a curated `igdb_id`. A backend that cannot
        express a partial update must refuse rather than approximate one.

        `artwork` is `(filename, bytes, content_type)`.
        """
        ...

    # -- collections -------------------------------------------------------

    def ensure_collection(self, name: str) -> int:
        """The id of the collection called `name`, creating it if absent."""
        ...

    def add_to_collection(self, collection_id: int, rom_ids: list[int]) -> None:
        """Add roms to a collection."""
        ...

    # -- post-upload registration ------------------------------------------

    def scan_platform(self, platform_id: int) -> Any:
        """Make freshly uploaded files visible in the library.

        A no-op is a perfectly good implementation for a backend that
        indexes on receipt; such a backend simply does not declare `SCAN`.
        """
        ...

    def close(self) -> None:
        """Release connections. Idempotent."""
        ...


def require(backend: LibraryBackend, capability: str, what: str) -> None:
    """Refuse `what` up front if `backend` cannot do it.

    The message names the backend, because "collections are not
    supported" invites the reply "but I have collections" from an
    operator looking at a different server than the one the Hub is
    pointed at.
    """
    supported = capabilities_of(backend)
    if capability in supported:
        return
    raise CapabilityUnsupported(
        f"{what} needs the {capability!r} capability, which the "
        f"{getattr(backend, 'name', 'active')!r} backend does not have "
        f"(it supports: {', '.join(sorted(supported)) or 'nothing'})"
    )


def capabilities_of(backend: LibraryBackend) -> frozenset[str]:
    """`backend.capabilities()`, defensively.

    A backend that answers something other than a set of strings is a
    broken backend, and the honest response is to treat it as capable of
    nothing rather than to assume it can do everything -- the assumption
    that fails silently is the one that uploads four gigabytes first.
    """
    try:
        raw = backend.capabilities()
    except Exception:  # noqa: BLE001 - a broken backend is not a traceback
        return frozenset()
    if not isinstance(raw, (set, frozenset, list, tuple)):
        return frozenset()
    return frozenset(str(item) for item in raw)
