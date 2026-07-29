"""Capability interfaces a plugin may implement.

Declare only what you support in manifest.toml [capabilities]. Phase 1
implements `search`; the others are defined for RPP v1 completeness and
land in later phases.
"""

from abc import ABC, abstractmethod

from romm_hub.types import FetchPlan, SearchResult

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
