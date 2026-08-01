"""Fans a search out across enabled plugins, in parallel, in isolation.

A crashed or hung plugin costs its own results and nothing else. The caller
always learns how many sources actually answered.

`limit` here is, and has always been, **per source**: it is the number
handed to each plugin's `search()`. That is the right shape for this layer
-- a plugin has to be told how much work to do before it starts -- but it
is the wrong shape for a person, because ten sources at 25 apiece is 250
rows. Merging and paging the combined set is `rom_hub.grouping`'s job, and
`fanout_limit()` below is how a caller works out what per-source number
will actually fill the page it wants.
"""

import functools
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .broker.host import PluginProcess
from .types import SearchResult

MAX_PARALLEL = 8

#: Never ask a source for less than this, however small the page. A source
#: that returns three rows for a query is not one worth a second round
#: trip, and grouping only ever *reduces* the row count -- so a page of 5
#: games can easily need 40 rows to fill.
MIN_FANOUT = 50

#: And never more than this, however large the page. Every row here is
#: work done by a subprocess against somebody else's server; a `--limit`
#: typo must not turn into a thousand-row scrape of ten catalogues.
MAX_FANOUT = 500

#: How many raw rows to ask each source for, per merged row wanted. Real
#: listings collapse hard -- eight `Batman Returns` rows become one -- so
#: asking for a page's worth would return well under a page.
FANOUT_FACTOR = 4


def fanout_limit(limit: int, offset: int = 0, per_source: int | None = None) -> int:
    """How many results to ask each source for, to fill one merged page.

    An estimate, and honestly one: nothing can know a query's collapse
    ratio before running it. What matters is that it is *stated* -- the
    caller can override it with `per_source`, and a source that returns
    exactly this many is reported as capped rather than quietly treated as
    exhaustive.
    """
    if per_source is not None:
        return max(1, per_source)
    wanted = max(0, offset) + max(0, limit)
    return max(MIN_FANOUT, min(MAX_FANOUT, wanted * FANOUT_FACTOR))


@dataclass
class PluginStatus:
    slug: str
    ok: bool
    count: int = 0
    error: str | None = None
    capped: bool = False
    """Whether this source returned exactly as many results as it was
    allowed, which means there were probably more it did not send.

    Reported rather than inferred away, for the same reason the responded
    count is: a merged listing that silently dropped a source's tail is
    indistinguishable from a complete one, and this project's rule is that
    a partial answer must say it is partial."""


@dataclass
class SearchOutcome:
    results: list[SearchResult] = field(default_factory=list)
    statuses: list[PluginStatus] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.statuses)

    @property
    def responded(self) -> int:
        return sum(1 for s in self.statuses if s.ok)

    @property
    def complete(self) -> bool:
        return self.responded == self.total

    @property
    def capped(self) -> list[str]:
        """Sources that filled their quota, and so probably had more."""
        return [s.slug for s in self.statuses if s.capped]


def _default_factory(
    plugin, fetcher, timeout, allow_unsandboxed=False, data_assets=None, secrets=None
) -> PluginProcess:
    return PluginProcess(
        plugin_dir=plugin.path,
        manifest=plugin.manifest,
        config=plugin.config,
        fetcher=fetcher,
        timeout=timeout,
        allow_unsandboxed=allow_unsandboxed,
        data_assets=data_assets,
        secrets=secrets,
    )


def search_all(
    plugins,
    fetcher,
    query: str,
    platform: str | None = None,
    limit: int = 50,
    timeout: float = 30.0,
    process_factory=None,
    allow_unsandboxed: bool = False,
    assets_for=None,
    secrets_for=None,
) -> SearchOutcome:
    # The sandbox policy is bound to the default factory here rather than
    # added to the factory signature: an injected factory stays a plain
    # (plugin, fetcher, timeout) callable, which is all a test needs to be.
    factory = process_factory or functools.partial(
        _default_factory, allow_unsandboxed=allow_unsandboxed
    )
    # Sorted, so the merged listing is reproducible: grouping's tie-breaks
    # are stable sorts over this order, and "the same query printed a
    # different order the second time" is indistinguishable from a bug in
    # the grouping itself.
    candidates = sorted(
        (p for p in plugins if p.enabled and "search" in p.manifest.capabilities),
        key=lambda p: p.slug,
    )

    def run(plugin) -> tuple[PluginStatus, list[SearchResult]]:
        try:
            # Inside `run`, so a plugin whose data asset cannot be fetched
            # or verified costs its own results and nothing else -- the
            # same isolation every other failure in this fan-out gets.
            # Only the default factory takes assets; an injected one is a
            # plain (plugin, fetcher, timeout) callable and stays that way.
            kwargs = {}
            if assets_for is not None and process_factory is None:
                kwargs["data_assets"] = assets_for(plugin)
            # Same isolation, same reason: a plugin whose secret store
            # cannot be read costs its own results and nothing else. Also
            # inside `run`, so a secret is read as late as possible and
            # only for a plugin that is actually about to be started.
            if secrets_for is not None and process_factory is None:
                kwargs["secrets"] = secrets_for(plugin)
            with factory(plugin, fetcher, timeout, **kwargs) as proc:
                results = proc.search(query, platform, limit)
            return (
                PluginStatus(
                    plugin.slug,
                    True,
                    len(results),
                    capped=limit > 0 and len(results) >= limit,
                ),
                results,
            )
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            return PluginStatus(plugin.slug, False, 0, f"{type(exc).__name__}: {exc}"), []

    outcome = SearchOutcome()
    if not candidates:
        return outcome

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(candidates))) as pool:
        for status, results in pool.map(run, candidates):
            outcome.statuses.append(status)
            outcome.results.extend(results)

    outcome.statuses.sort(key=lambda s: s.slug)
    return outcome
