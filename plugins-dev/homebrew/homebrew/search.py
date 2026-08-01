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

Pages are ten entries. `page_elements` is a real parameter -- the Hub's
own API.md documents it -- but its allowed range is 1..10 and 10 is
already the default, so there is nothing to win there and the only way to
see more is to ask again.

That is what `max_pages` bounds, and at 3 it was the plugin's real
ceiling: **30 entries out of 1,571.** A query narrow enough to answer in
thirty was fine and anything else was silently truncated. It is 20 by
default now (200 entries) and 158 by config, which is the whole
catalogue: `page_total` is 158 and the walk stops there on its own.

`tags` is the third server-side filter, alongside `q` and `platform`. It
is exact and comma-separated -- `tags=Open Source,RPG` -- and it is worth
having because the Hub's categories are the only structure over 1,571
entries that is neither a title nor a machine.

**Unknown parameters are ignored, silently and completely.** `?bogus=snake`
returns all 1,571 entries with HTTP 200, so a plugin that hopefully sent a
made-up filter would look like it was filtering and would in fact be
returning the whole database. Only `q`, `platform`, `typetag`, `tags`,
`page` and the sort pair are real here, and each was checked live rather
than read off the documentation -- API.md names the type filter `type`,
which is one of the ignored ones; the parameter that works is `typetag`.
"""

import json

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .hub import API, HubError, parse_page
from .platforms import hub_platform_for, platform_for

#: Pages one search may walk. Ten entries each, so 20 is 200 entries --
#: enough that an operator asking for a hundred results gets a hundred.
DEFAULT_MAX_PAGES = 20
#: `page_total` for an unfiltered query is 158, so this is the whole
#: catalogue and not an arbitrary ceiling. The walk stops on `page_total`
#: anyway; this is what stops a config typo asking for a thousand.
PAGE_CAP = 158


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
        tags = self._tags()
        if tags:
            # The Hub's own spelling: one parameter, comma-separated,
            # AND-ed, exact per tag.
            params["tags"] = ",".join(tags)

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
                                # What `stream` will say without a second
                                # round trip. 1,565 of 1,571 entries are
                                # playable, so the interesting value is
                                # the false one.
                                "playable": (
                                    "true" if entry.playable_file else "false"
                                ),
                                "license": entry.license,
                                "tags": ",".join(entry.tags),
                                "date": entry.date,
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # Community-submitted text landing in constrained
                    # fields. One bad record must not cost the page.
                    continue
        return results

    def _tags(self) -> list[str]:
        """Configured tag filter, as a list of exact tag names.

        Accepts a list or a single comma-separated string, because both
        spellings are what people write in a TOML config and neither is
        wrong enough to refuse.
        """
        raw = self.ctx.config.get("tags") or []
        if isinstance(raw, str):
            raw = raw.split(",")
        return [str(tag).strip() for tag in raw if str(tag).strip()]

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
