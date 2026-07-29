"""Search the Homebrew Hub.

The query goes to the server, which is the whole reason this source was
chosen over the alternatives: one request answers a query across all 1,571
entries, instead of a client walking listing pages hoping the match is in
the pages it fetched.

`--platform` also goes to the server. It is translated to the Hub's own
vocabulary first (`gbc` -> `GBC`; the filter is case-sensitive and returns
zero for the lowercase form), and a RomM platform this source has nothing
for -- Dreamcast, say -- returns an empty list **without a request**. That
is not an error: it is a reasonable question with a boring answer, and
answering it for free is better than answering it slowly.

Pages are ten entries and the size is not negotiable (`limit`, `per_page`,
`elements` and `page_elements` were all tried against the live API and all
ignored), so `max_pages` bounds the walk and the walk stops as soon as
`limit` results exist or the Hub says there are no more pages.
"""

import json

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .hub import API, HubError, parse_page
from .platforms import hub_platform_for, platform_for

DEFAULT_MAX_PAGES = 3
PAGE_CAP = 50


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        params: dict[str, str | int] = {}
        if (query or "").strip():
            params["q"] = query.strip()
        typetag = str(self.ctx.config.get("typetag") or "").strip()
        if typetag:
            params["typetag"] = typetag

        wanted = (platform or "").strip()
        if wanted:
            hub_platform = hub_platform_for(wanted)
            if hub_platform is None:
                # This archive holds Game Boy, Game Boy Color, Game Boy
                # Advance and NES homebrew and nothing else.
                return []
            params["platform"] = hub_platform

        results: list[SearchResult] = []
        page_total = 1
        for page in range(1, self._max_pages() + 1):
            if len(results) >= limit or page > page_total:
                break
            entries, page_total = self._page({**params, "page": page})
            if not entries:
                break
            for entry in entries:
                if len(results) >= limit:
                    break
                try:
                    results.append(
                        SearchResult(
                            source_id=entry.slug,
                            title=entry.title,
                            # None when the record does not say. The
                            # importer refuses rather than guessing; see
                            # platforms.py.
                            platform=platform_for(entry.platform or ""),
                            url=entry.site_url,
                            extra={
                                "developer": entry.developer,
                                "typetag": entry.typetag,
                                "hub_platform": entry.platform or "",
                                "files": str(len(entry.files)),
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # Community-submitted text landing in constrained
                    # fields. One bad record must not cost the page.
                    continue
        return results

    def _max_pages(self) -> int:
        raw = self.ctx.config.get("max_pages", DEFAULT_MAX_PAGES)
        try:
            pages = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_PAGES
        return max(1, min(pages, PAGE_CAP))

    def _page(self, params: dict):
        response = self.ctx.http.get(API, params=params)
        if response.status_code != 200:
            raise HubError(
                f"the Homebrew Hub returned HTTP {response.status_code} for "
                f"{API!r}"
            )
        try:
            payload = json.loads(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HubError(
                f"the Homebrew Hub's answer was not JSON: {exc}"
            ) from exc
        return parse_page(payload)
