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

So the host asks first and says so plainly, before any work.

## Knowing early is not the same as refusing

The mechanism above answers *when* to check. It does not answer *what to
do about the answer*, and for a while this file only had one answer:
refuse. That was wrong often enough to be a bug. `rom-hub import
archive-org rubik_202308` against Gaseous or Retrom stopped dead with
nothing downloaded, because the archive-org plugin names a collection by
default (`... or "Archive.org"`) and neither backend has collections --
Gaseous's `CollectionsController.cs` has no non-comment lines in it, and
Retrom has no collection concept in its schema at all. The operator asked
for a ROM and got a lecture about grouping.

A collection is a *grouping nicety*. Refusing to put a ROM in a library
because the library cannot also file it under a label is refusing the job
over the garnish. So capabilities are split in two, and the split is the
policy:

* **Essential** -- without it the operation cannot happen at all. Refuse,
  before a single byte moves. `require()`.
* **Optional** -- an extra layered on top of an operation that succeeds
  without it. Do the operation, skip the extra, and *say* that it was
  skipped in the outcome the operator reads. `degrade()`.

The classification of every capability is `ESSENTIAL_CAPABILITIES` /
`OPTIONAL_CAPABILITIES` below, with the reasoning per name. It is not a
special case for collections: every capability the host gates on is
listed, `degrade()` refuses to be handed an essential one, and a test
asserts the two sets plus the ungated ones cover `ALL_CAPABILITIES`, so a
capability added later cannot be left unclassified.

**Degradation is for a default, not for a request.** A plugin naming
"Archive.org" is boilerplate the operator never typed; dropping it costs
them nothing they asked for. An operator who typed `--collection
"Shooters"` did ask, and silently not doing it is how a library ends up
quietly unsorted. So the CLI still *refuses* an explicit `--collection`
against a backend without collections -- up front, before the plugin
process starts -- and the refusal says what to re-run. The pipeline, which
cannot tell the two apart by the time it sees a `FetchPlan`, degrades.

## What is deliberately absent

There is no "create a platform" method. `platform_id()` resolves a name
and raises when there is no match, and that refusal is load-bearing:
filing a ROM under a platform the operator did not choose is worse than a
visible failure, and it is the kind of wrong that is not noticed for
months.
"""

from __future__ import annotations

from dataclasses import dataclass
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

#: Store a platform's BIOS/firmware, and list what is already stored so a
#: second copy is not sent. Separate from `IMPORT` because firmware is not
#: a rom anywhere: RomM keeps it in its own table behind its own
#: `firmware.read`/`firmware.write` scopes, and a backend can perfectly
#: well accept roms while having no concept of firmware at all -- both of
#: the other two do exactly that.
FIRMWARE = "firmware"

ALL_CAPABILITIES = frozenset(
    {IMPORT, COLLECTIONS, METADATA, ARTWORK, SCAN, FIRMWARE}
)


# -- essential or optional -------------------------------------------------
#
# Every capability the host gates on, classified. See "Knowing early is not
# the same as refusing" in the module docstring for why this split exists;
# what follows is why each name landed where it did.

#: Without these the operation is not diminished, it is impossible.
#:
#: **IMPORT** is upload *and* listing, which between them are the entire
#: import: a backend that cannot be uploaded to has nowhere to put the
#: ROM, and one that cannot be listed cannot be deduped against -- and a
#: dedup that could not run is not a dedup that passed. There is no
#: reduced import left over when this is missing.
#:
#: **METADATA** is the whole of `rom-hub enrich`. Writing a rom's fields
#: is not a step in enriching, it is the thing itself; "enriched, except
#: nothing was written" is not a degraded success, it is a lie.
ESSENTIAL_CAPABILITIES = frozenset({IMPORT, METADATA})

#: Extras. The operation is complete and correct without them, so the host
#: does them when it can, skips them when it cannot, and reports the skip.
#:
#: **COLLECTIONS** groups roms that are already in the library. It happens
#: *after* the upload, it changes nothing about the ROM, and the operator
#: can make the collection by hand later if they want one. Costing them
#: the import over it -- which is what this code did until the archive-org
#: plugin's default collection blocked every Gaseous and Retrom import --
#: is refusing the job over the garnish.
#:
#: **ARTWORK** is a cover image attached to a rom record. A patch that
#: carries a name, a release date and an igdb_id *and* a cover should not
#: lose all four to a backend that stores no images; the three it can
#: store are worth writing. The cover is fetched over the network, so the
#: skip is decided before `_artwork` runs and costs no download either.
#: (If a patch proposes nothing *but* a cover, there is no remainder to
#: write and `run_enrich` says so rather than reporting a change it did
#: not make.)
#:
#: **FIRMWARE** is the library half of `rom-hub firmware install`, and it
#: is the half that can be missing without the command losing its point.
#: The thing an operator wants from a BIOS is a *file*, in a directory an
#: emulator reads -- which is why `firmware` is modelled on `cores`, and
#: `cores` never touches a library at all. Filing it in RomM as well is
#: what makes it reachable from RomM's own browser emulator; it is a
#: second home for bytes that are already installed, not the install.
#: Refusing to fetch a legally-clean Game Boy boot ROM onto the disk
#: because the *library server* has no firmware table would be refusing
#: the job over the garnish, in exactly the way `--collection` once
#: refused every Gaseous import. So the download happens, the upload is
#: skipped, and `FirmwareInstallResult.skipped` says so in the line the
#: operator reads.
OPTIONAL_CAPABILITIES = frozenset({COLLECTIONS, ARTWORK, FIRMWARE})

#: Declared, never gated on.
#:
#: **SCAN** describes a backend rather than authorising a step: the
#: pipeline calls `scan_platform()` unconditionally after every upload,
#: and a backend that indexes on receipt implements it as a no-op. There
#: is nothing to check, because there is no branch. It is not optional
#: either -- when a backend *does* need the registration and it fails, the
#: ROM is not in the library and the import has genuinely failed, which is
#: why that failure is raised rather than noted.
UNGATED_CAPABILITIES = frozenset({SCAN})

#: **Plugin** capabilities (`manifest.KNOWN_CAPABILITIES`) that touch no
#: backend at all, and therefore cannot appear in any of the three sets
#: above -- those classify what a *backend* declares.
#:
#: This is a fourth answer to "essential or optional?", and `assets`
#: forced it to be written down. Essential means refuse before doing work;
#: optional means do the work and report the skip. Both presuppose a
#: backend method that might be missing. `rom_hub.emuassets` calls none:
#: `install_asset` takes no `backend` argument, opens no connection, and
#: the module does not import this package. A bezel lands in a directory
#: an emulator reads, and that is the entire operation. So the question
#: "what does the backend not support here?" has *no answer*, rather than
#: an answer of "nothing".
#:
#: The set divides in two, and the second half is the interesting one.
#:
#: * **`search` and `stream` install nothing.** A search returns rows and
#:   a stream resolves a target; neither was ever going to touch a
#:   library, so neither is remarkable.
#: * **`cores` and `assets` install files and still touch no library.**
#:   That is the notable property. Every other capability that puts bytes
#:   on disk ends in a backend write -- `importer` uploads the ROM,
#:   `firmware` files the BIOS when it can. These two end on the disk and
#:   stop, which means an operator can install emulator cores, shaders,
#:   bezels, cheats and controller profiles with no library server
#:   configured at all: no RomM, no Gaseous, no Retrom, nothing.
#:   `rom-hub assets install` is a command for which `rom-hub backend
#:   info` is not merely uninteresting but inapplicable.
#:
#: `cores` was already in the second group and was never recorded, because
#: a lone exception reads as an oversight. Two make a category worth
#: naming -- and naming it is what stops "absent from all three sets
#: above" being indistinguishable from "somebody forgot to classify it",
#: which is the failure the catalog's classification test exists to catch.
#: That test now asserts against this set rather than repeating its
#: members, so the two cannot drift.
#:
#: Deliberately NOT unioned into `ALL_CAPABILITIES`: that set is the
#: vocabulary of things a *backend* declares, and putting a plugin
#: capability in it would make `rom-hub backend info` print `assets` under
#: "cannot" for every backend ever written -- true only in the sense that
#: a hammer cannot tell the time.
BACKEND_INDEPENDENT_CAPABILITIES = frozenset(
    {"search", "stream", "cores", "assets"}
)

#: One line each, for `rom-hub backend info`. A capability list is only
#: useful to an operator who can tell which command each name governs.
CAPABILITY_HELP = {
    IMPORT: "accept a ROM upload, and list the library so a duplicate is caught first",
    COLLECTIONS: "group roms into a named collection (rom-hub import --collection)",
    METADATA: "write a rom's metadata fields (rom-hub enrich)",
    ARTWORK: "attach cover art to a rom",
    SCAN: "needs an explicit registration step after an upload",
    FIRMWARE: "store a platform's BIOS files (rom-hub firmware install)",
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

    # -- firmware ----------------------------------------------------------

    def list_firmware(self, platform_id: int) -> list[dict]:
        """Every firmware file stored for `platform_id`.

        Read before an upload so a BIOS already in the library is not sent
        a second time, and read after one as proof it landed -- the same
        two jobs `list_roms` does. The dicts carry at least `file_name`;
        `rom_hub.firmware` reads them defensively and treats an unknown
        shape as "not there", never as an error.
        """
        ...

    def upload_firmware(self, paths: list[Path], platform_id: int) -> None:
        """Store these firmware files under `platform_id`.

        A list rather than one path because firmware comes in sets -- a
        Game Boy Color boot ROM ships beside the Game Boy one -- and
        because RomM's own endpoint takes `files: list[UploadFile]` in a
        single request. Returns nothing, for the same reason
        `upload_rom` does: what comes back is a whole-platform listing,
        not an id for what was just sent, and `list_firmware` is the
        honest way to ask what landed.
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


def require(
    backend: LibraryBackend, capability: str, what: str, hint: str = ""
) -> None:
    """Refuse `what` up front if `backend` cannot do it.

    The message names the backend, because "collections are not
    supported" invites the reply "but I have collections" from an
    operator looking at a different server than the one the Hub is
    pointed at.

    `hint` is the way out, when there is one. A refusal an operator
    cannot act on is a dead end; `--collection` against a collection-less
    backend has an obvious next move (run it without the flag) and saying
    so is cheaper than making them find it.

    Still reachable for an *optional* capability, deliberately: the CLI
    refuses an explicitly typed `--collection` rather than degrading it.
    What must not happen is the reverse -- see `degrade()`.
    """
    supported = capabilities_of(backend)
    if capability in supported:
        return
    raise CapabilityUnsupported(
        f"{what} needs the {capability!r} capability, which the "
        f"{getattr(backend, 'name', 'active')!r} backend does not have "
        f"(it supports: {', '.join(sorted(supported)) or 'nothing'})"
        + (f". {hint}" if hint else "")
    )


@dataclass(frozen=True)
class SkippedStep:
    """One optional step that did not happen, and why.

    Carried out of the pipeline in its result and written to the job
    record, because a degradation nobody is told about is
    indistinguishable from a bug. The operator asked for an import and
    got one; they are entitled to know it is not filed under the label
    the plugin named.
    """

    capability: str
    what: str
    backend: str

    def __str__(self) -> str:
        return (
            f"{self.what} was skipped: the {self.backend!r} backend does not "
            f"support {self.capability}"
        )


def degrade(
    backend: LibraryBackend, capability: str, what: str
) -> SkippedStep | None:
    """None if `backend` can do `capability`; otherwise what will be skipped.

    The optional-capability counterpart to `require()`. Callers do the
    rest of the operation either way and report the returned skip.

    Raises `ValueError` -- not a degradation -- if handed a capability
    classified as essential. That is the guard rail on this whole policy:
    the failure mode being designed against is a future caller quietly
    turning "cannot do the job" into a note in the output, so an essential
    capability reaching this function is a programming error, and it is
    louder than the thing it prevents.
    """
    if capability in ESSENTIAL_CAPABILITIES:
        raise ValueError(
            f"{capability!r} is an essential capability and cannot be "
            f"degraded; use require() so the operation refuses before it "
            f"does any work"
        )
    if capability in capabilities_of(backend):
        return None
    return SkippedStep(
        capability=capability,
        what=what,
        backend=str(getattr(backend, "name", "") or "active"),
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
