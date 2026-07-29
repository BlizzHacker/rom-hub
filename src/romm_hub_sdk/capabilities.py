"""Capability interfaces a plugin may implement.

Declare only what you support in manifest.toml [capabilities]. All five
RPP v1 capabilities are implemented by the host.

Every one of them follows the same rule: **a plugin returns a description
and the host performs the privileged action**. A URL in any of these
return values is fetched by the host, only after it has been checked
against the plugin's own `network` allowlist -- so a `metadata` artwork
URL and a `cores` download URL are gated exactly like a `FetchPlan` URL.
"""

from abc import ABC, abstractmethod

from romm_hub.types import (
    FetchPlan,
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
