"""Fans a search out across enabled plugins, in parallel, in isolation.

A crashed or hung plugin costs its own results and nothing else. The caller
always learns how many sources actually answered.
"""

import functools
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .broker.host import PluginProcess
from .types import SearchResult

MAX_PARALLEL = 8


@dataclass
class PluginStatus:
    slug: str
    ok: bool
    count: int = 0
    error: str | None = None


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


def _default_factory(plugin, fetcher, timeout, allow_unsandboxed=False) -> PluginProcess:
    return PluginProcess(
        plugin_dir=plugin.path,
        manifest=plugin.manifest,
        config=plugin.config,
        fetcher=fetcher,
        timeout=timeout,
        allow_unsandboxed=allow_unsandboxed,
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
) -> SearchOutcome:
    # The sandbox policy is bound to the default factory here rather than
    # added to the factory signature: an injected factory stays a plain
    # (plugin, fetcher, timeout) callable, which is all a test needs to be.
    factory = process_factory or functools.partial(
        _default_factory, allow_unsandboxed=allow_unsandboxed
    )
    candidates = [
        p for p in plugins if p.enabled and "search" in p.manifest.capabilities
    ]

    def run(plugin) -> tuple[PluginStatus, list[SearchResult]]:
        try:
            with factory(plugin, fetcher, timeout) as proc:
                results = proc.search(query, platform, limit)
            return PluginStatus(plugin.slug, True, len(results)), results
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
