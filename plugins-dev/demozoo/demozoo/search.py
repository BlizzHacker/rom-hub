"""Demozoo `search` over the public productions API.

    GET https://demozoo.org/api/v1/productions/?format=json
        [&title=<query>] [&platform=<id>] [&production_type=<id>] [&page=N]

Public, unauthenticated, 386,682 productions on 2026-07-29, one hundred
rows a page.


What `title` does, and what it does not
---------------------------------------

**`title` is an exact match, case-insensitively. It is not a substring
search.** Verified live: `title=Cracktro` and `title=CRACKTRO` both return
the same 26 productions, every one of them named exactly "Cracktro", and
`title=crackt` returns **zero**. `title=second reality` returns the seven
productions called Second Reality.

That is a real limitation and this plugin does not paper over it, for a
specific reason. Demozoo *does* have a substring search -- it is at
`/search/?q=...` and it renders HTML -- and `demozoo.org/robots.txt`
carries `Disallow: /search/` for every user agent, with `Crawl-delay: 10`.
So the substring search is exactly the thing this plugin must not use, and
the honest answer is to say what the API can do rather than to reach
around it. Everything here is `/api/v1/`, which robots.txt does not
restrict.

Every unknown query parameter is **silently ignored** by this API, which
makes guessing at one actively dangerous: `?search=cracktro`,
`?q=cracktro`, `?title__icontains=crackt` and `?page_size=5` all return
HTTP 200 and the complete unfiltered 386,682-row listing. A plugin that
hopefully sent `search=` would look like it was searching and would in
fact be returning the alphabetical head of the whole database. Only the
four parameters at the top of this file are real.


So the useful shapes are two
----------------------------

* **Exact title**, one request: `rom-hub search demozoo "second reality"`.
* **Browse a platform**, which is what a demoscene source is mostly for:
  an empty query plus `--platform c64` lists that machine's productions,
  and this plugin's `production_type` config narrows it further
  (`Cracktro`, `Demo`, `64K Intro`, ...). `--platform amiga` becomes three
  requests, because Demozoo splits the Amiga into OCS/ECS, AGA and
  PPC/RTG and RomM does not.

Results are filtered to what `importer` would actually accept -- see
`productions.py`. A row an operator can see and cannot import is worse
than one row fewer.
"""

from rom_hub_sdk import SearchProvider, SearchResult

from .platforms import demozoo_ids_for_slug, slug_for
from .productions import is_importable, parse_production, type_id_for

ENDPOINT = "https://demozoo.org/api/v1/productions/"

#: Requests one `search()` may make, across all platform ids and pages.
#: Every page is a round trip against the host's own timeout, and
#: `--platform amiga` already costs three streams before paging.
DEFAULT_MAX_REQUESTS = 6

#: Ceiling on `limit`, independent of what the host asks for. The API
#: pages at 100 and the CLI prints every row.
MAX_LIMIT = 200


class SearchError(Exception):
    """The search could not be performed, and the message says why."""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        limit = max(1, min(int(limit or 20), MAX_LIMIT))
        title = (query or "").strip()
        base = self._base_params()

        # One parameter set per Demozoo platform id, because `platform`
        # takes a single id and RomM's `amiga` is three of them.
        streams: list[dict] = []
        if platform:
            ids = demozoo_ids_for_slug(platform)
            if not ids:
                # Not an error: the operator asked for a platform Demozoo
                # does not index, and an empty result says that honestly.
                return []
            streams = [dict(base, platform=pid) for pid in ids]
        else:
            streams = [dict(base)]

        budget = self._max_requests()
        results: list[SearchResult] = []
        seen: set[int] = set()

        for stream in streams:
            page = 1
            while len(results) < limit and budget > 0:
                budget -= 1
                params = dict(stream, page=page)
                if title:
                    params["title"] = title
                body = self._page(params)

                for raw in body.get("results") or []:
                    production = parse_production(raw)
                    if production is None or production.id in seen:
                        continue
                    if not is_importable(production):
                        continue
                    seen.add(production.id)
                    results.append(self._result(production))
                    if len(results) >= limit:
                        break

                if not body.get("next"):
                    break
                page += 1

        return results[:limit]

    # -- one page --------------------------------------------------------

    def _page(self, params: dict) -> dict:
        response = self.ctx.http.get(ENDPOINT, params=params)
        if response.status_code != 200:
            raise SearchError(
                f"Demozoo answered HTTP {response.status_code} for "
                f"{ENDPOINT} with {params!r}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            # Maintenance pages and rate limiters both arrive as 200+HTML.
            raise SearchError(f"Demozoo's response was not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise SearchError("Demozoo's response was not a JSON object")
        return body

    def _result(self, production) -> SearchResult:
        platform_name = production.mapped_platform or ""
        return SearchResult(
            source_id=str(production.id),
            title=production.title,
            # The RomM slug, not Demozoo's platform name: this is the
            # column an operator scans, and it is the value `importer`
            # will actually file the production under.
            platform=slug_for(platform_name),
            # The API carries no file size anywhere -- not on the listing
            # and not on the detail record. Left unset rather than
            # estimated.
            size_bytes=None,
            url=production.demozoo_url or None,
            extra={
                "author": production.author,
                "released": production.release_date,
                "types": ", ".join(production.type_names),
                "demozoo_platform": platform_name,
            },
        )

    # -- configuration ---------------------------------------------------

    def _base_params(self) -> dict:
        params: dict = {"format": "json"}
        wanted = str(self.ctx.config.get("production_type") or "").strip()
        if wanted:
            # Refuses a type this plugin does not import, rather than
            # filtering to rows the importer would then decline one by one.
            params["production_type"] = type_id_for(wanted)
        return params

    def _max_requests(self) -> int:
        raw = self.ctx.config.get("max_requests")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_REQUESTS
        return max(1, min(value, 20))
