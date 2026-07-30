"""Capability interfaces a plugin may implement.

Declare only what you support in manifest.toml [capabilities]. All seven
RPP v1 capabilities are implemented by the host.

Every one of them follows the same rule: **a plugin returns a description
and the host performs the privileged action**. A URL in any of these
return values is fetched by the host, only after it has been checked
against the plugin's own `network` allowlist -- so a `metadata` artwork
URL, a `cores` download URL, a `firmware` download URL and an `assets`
download URL are gated exactly like a `FetchPlan` URL.
"""

from abc import ABC, abstractmethod

from rom_hub.types import (
    AssetArtifact,
    CoreArtifact,
    FetchPlan,
    FirmwareArtifact,
    MetadataPatch,
    RomRef,
    SearchResult,
    StreamTarget,
)

from .context import PluginContext


class Capability(ABC):
    def __init__(self, ctx: PluginContext):
        self.ctx = ctx


class SearchProvider(Capability):
    @abstractmethod
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        """Return results for a query. Raise for a hard failure."""


class ImportProvider(Capability):
    @abstractmethod
    def plan(self, result: SearchResult) -> FetchPlan:
        """Describe what to fetch for this result. The HOST performs the fetch."""


class MetadataProvider(Capability):
    @abstractmethod
    def enrich(self, rom: RomRef) -> MetadataPatch:
        """Describe metadata for a rom already in RomM.

        Return only what you actually know: an unset field means "leave
        RomM's value alone", so a patch that sets `name` alone will not
        disturb ids or artwork the user has already curated.

        Artwork may be given as `artwork_url` -- which the HOST fetches,
        after checking it against this plugin's `network` allowlist -- or
        as `artwork_base64` if the bytes are already in hand.

        Raise for a hard failure. Returning an empty `MetadataPatch()` is
        the way to say "I know nothing about this rom"; the host then
        leaves RomM untouched.
        """


class StreamProvider(Capability):
    @abstractmethod
    def resolve(self, result: SearchResult) -> StreamTarget:
        """Say where this item can be played.

        Return `kind="url"` for something that will be fetched -- the host
        checks it against this plugin's `network` allowlist -- or
        `kind="handle"` for an identifier another service understands. A
        handle may not itself be a URL; that would be a way around the
        check.

        Raise for an item that cannot be streamed. The host does not build
        any streaming transport of its own: it validates this answer and
        hands it on.
        """


class CoreProvider(Capability):
    @abstractmethod
    def list(self) -> list[CoreArtifact]:
        """The emulator cores this plugin can install. A catalogue only."""

    @abstractmethod
    def plan(self, core: CoreArtifact) -> FetchPlan:
        """Describe what to fetch for one core. The HOST performs the fetch.

        The same `FetchPlan` an importer returns, and gated identically:
        every URL is checked against this plugin's `network` allowlist and
        every filename must be a bare name. A core is a binary landing on
        the operator's disk, which is exactly as privileged as a ROM.

        `platform` is a label for the operator here rather than a RomM
        platform slug -- name the system the core emulates.
        """


class FirmwareProvider(Capability):
    """BIOS and boot ROMs, listed by the plugin and installed by the host.

    Shaped exactly like `CoreProvider`, because it is the same privileged
    act: a plugin names URLs and the host fetches them. What differs is
    what the answer has to carry.

    **Name the licence, on every item.** `FirmwareArtifact.license` is a
    required field. Console firmware is the one artifact class where the
    usual answer to "may I have this?" is no, and an operator installing a
    BIOS is entitled to read what they are installing without leaving the
    terminal. Say `"MIT"`, `"GPL-2.0"`, `"CC0-1.0"` -- an SPDX identifier
    where there is one, plain words where there is not.

    **Do not offer firmware you are not permitted to redistribute.** The
    Hub cannot check this and does not pretend to: nothing here inspects
    the bytes, and a dumped console BIOS looks exactly like a clean-room
    one on the wire. The rule is a rule about what a plugin lists.

    **Name a real platform.** `FirmwareArtifact.platform` is required, and
    the backend upload resolves it against the library's own platforms.
    A plugin that cannot map an item to a platform should raise "needs
    mapping" naming it, never guess -- firmware filed under the wrong
    system is worse than a visible failure, because the emulator that
    needed it simply keeps saying it has no BIOS.
    """

    @abstractmethod
    def list(self) -> list[FirmwareArtifact]:
        """The firmware this plugin can install. A catalogue only."""

    @abstractmethod
    def plan(self, firmware: FirmwareArtifact) -> FetchPlan:
        """Describe what to fetch for one item. The HOST performs the fetch.

        The same `FetchPlan` an importer returns, and gated identically:
        every URL is checked against this plugin's `network` allowlist and
        every filename must be a bare name.

        `FetchPlan.platform` is not read for a firmware install -- the
        `FirmwareArtifact`'s own `platform` is, because that is the value
        the operator saw in `rom-hub firmware list` before choosing. Set
        it to the same thing anyway; `FetchPlan` requires it.

        When the artifact declares `archive = "zip"`, this plan names the
        *archive*: one file, which the host unpacks, keeping exactly the
        declared `members`.
        """


class AssetProvider(Capability):
    """Emulator support files: shaders, overlays, cheats, controller profiles.

    Shaped exactly like `CoreProvider` and `FirmwareProvider`, because it
    is the same privileged act: a plugin names URLs and the host fetches
    them. Three things are worth knowing before implementing it.

    **No library is involved, at all.** A core is installed for an
    emulator; firmware is installed for an emulator *and* filed in the
    library when the backend can hold it. An asset is only ever the
    former. Nothing in this capability reads, writes or even opens a
    library backend, which makes it the first RPP capability that works
    identically whether the operator runs RomM, Gaseous, Retrom or nothing
    at all. See `rom_hub.emuassets`.

    **`kind` chooses the destination.** The host owns the kind ->
    directory map and the operator configures it; a plugin says *what* a
    file is and never *where* it goes. This is why one capability covers
    four kinds of file instead of four capabilities covering one each --
    the only thing that varies between them is a directory lookup.

    **Name the licence, on every item.** `AssetArtifact.license` is
    required, for the reason `FirmwareArtifact.license` is. These sources
    are community repositories of contributed files, and the terms really
    do vary between them; two of the five sources surveyed when this
    capability was built were dropped precisely because their terms could
    not be established. Say `"CC-BY-4.0"`, `"MIT"`, `"CC-BY-SA-4.0"` -- an
    SPDX identifier where there is one, plain words where there is not.
    **Do not offer files you cannot establish the terms for.** The Hub
    cannot check this and does not pretend to.
    """

    @abstractmethod
    def list(self) -> list[AssetArtifact]:
        """The support files this plugin can install. A catalogue only.

        Keep it cheap. These sources are large -- the repositories behind
        them run to hundreds of megabytes -- and a catalogue is what an
        operator asks for before they have decided on anything, so it must
        never be answered by downloading the corpus. List from an index,
        an API listing or a manifest; download in `plan()` and only the
        item that was chosen.

        A catalogue over `rom_hub.types.MAX_ASSETS_PER_PLUGIN` is refused
        by the host. A plugin whose source is naturally bigger than that
        should raise a message naming the config key that narrows it,
        rather than truncating and leaving the operator to wonder what is
        missing.
        """

    @abstractmethod
    def plan(self, asset: AssetArtifact) -> FetchPlan:
        """Describe what to fetch for one asset. The HOST performs the fetch.

        The same `FetchPlan` an importer returns, and gated identically:
        every URL is checked against this plugin's `network` allowlist and
        every filename must be a bare name.

        **Every filename must be a bare name**, which is a real constraint
        rather than a formality here: a RetroArch overlay `.cfg` may
        reference its images as `img/button.png`, and there is no way to
        express that subdirectory in a `FetchPlan` -- deliberately, since
        it is the same rule that stops a plugin writing outside the
        directory chosen for it. A plugin whose item genuinely needs a
        subdirectory should not offer that item, and should say why.

        `FetchPlan.platform` is not read for an asset install -- the
        `AssetArtifact`'s own `system` is what the operator saw in
        `rom-hub assets list`. Set it to something honest anyway;
        `FetchPlan` requires it.
        """
