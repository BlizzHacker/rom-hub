"""itch.io search over the browse listings.

**itch.io's own search endpoint is not used, on purpose.** `/search` is
disallowed for every user-agent in https://itch.io/robots.txt, and
`api.itch.io/search/games` answers 401 without an account key. What robots
does permit is `/games/...`, so this plugin asks for a browse listing --
always scoped to free games -- and matches the query against the titles it
gets back. That is a weaker search than a server-side one and it is stated
plainly in the README rather than dressed up.

Matching is every whitespace-separated term appearing somewhere in the
cell's title, blurb, author, genre or slug. AND rather than OR, because a
listing page holds 36 games and OR would return most of them for any
two-word query.

Pages are fetched one at a time and the walk stops the moment `limit`
results exist, so the common case costs one request. The upper bound is
`max_pages`; there is no parallelism, because the plugin has no sockets --
every request is an RPC the host serves serially anyway.
"""

import json

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .browse import BrowseError, browse_url, parse_cells
from .platforms import platform_for

DEFAULT_MAX_PAGES = 4
# itch.io serves 36 cells per browse page; asking beyond the end returns an
# empty fragment, which ends the walk on its own.
PAGE_CAP = 50


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        url = browse_url(self.ctx.config.get("filters") or [])
        terms = [t for t in (query or "").lower().split() if t]
        wanted = (platform or "").strip().lower() or None

        results: list[SearchResult] = []
        for page in range(1, self._max_pages() + 1):
            if len(results) >= limit:
                break
            cells = self._page(url, page)
            if not cells:
                break
            for cell in cells:
                if len(results) >= limit:
                    break
                if terms and not all(t in cell.haystack for t in terms):
                    continue
                mapped = [platform_for(label) for label in cell.platform_labels]
                slugs = [s for s in mapped if s]
                if wanted and wanted not in slugs:
                    continue
                unmapped = [
                    label
                    for label, slug in zip(cell.platform_labels, mapped)
                    if slug is None
                ]
                try:
                    results.append(
                        SearchResult(
                            source_id=cell.source_id,
                            title=cell.title,
                            # One recognised platform is a fact; several is a
                            # choice, and this plugin does not make it -- the
                            # importer reads the actual uploads. Both are
                            # reported in `extra` either way.
                            platform=slugs[0] if len(slugs) == 1 else None,
                            url=cell.url,
                            extra={
                                "platforms": ",".join(slugs),
                                "unmapped_platforms": ",".join(unmapped),
                                "itch_game_id": cell.game_id,
                                "author": cell.author,
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # Upstream text lands in constrained fields. One bad
                    # cell must not cost the rest of the page.
                    continue
        return results

    def _max_pages(self) -> int:
        raw = self.ctx.config.get("max_pages", DEFAULT_MAX_PAGES)
        try:
            pages = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_PAGES
        return max(1, min(pages, PAGE_CAP))

    def _page(self, url: str, page: int):
        response = self.ctx.http.get(url, params={"format": "json", "page": page})
        if response.status_code != 200:
            if page == 1:
                raise BrowseError(
                    f"itch.io returned HTTP {response.status_code} for the browse "
                    f"listing {url!r}"
                )
            # A later page failing is the end of the walk, not a failure of
            # the search: whatever the earlier pages produced still stands.
            return []
        try:
            payload = json.loads(response.text)
        except (ValueError, json.JSONDecodeError):
            # Cloudflare's interstitial arrives as HTML with a 200 on some
            # browse URL shapes. Treated as "no more pages" past the first.
            if page == 1:
                raise BrowseError(
                    f"itch.io's browse listing {url!r} was not JSON; itch.io "
                    f"answers some URL shapes with a Cloudflare challenge page"
                ) from None
            return []
        if not isinstance(payload, dict):
            return []
        return parse_cells(payload.get("content") or "")
