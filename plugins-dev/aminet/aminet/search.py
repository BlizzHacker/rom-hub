"""Search Aminet's `game/` tree.

The query goes to the server -- `?query=<terms>&dir=game` searches all
85,449 packages at once and scopes the answer to the game shelves -- so a
walk of `/tree` never happens and a match five thousand packages deep is
found in one request.

Two filters run client-side afterwards, and both are subtractive:

* **shelves that hold no games are dropped.** `game/` has 18
  subdirectories and four of them are data files, level editors, hint
  documents and patches. Aminet's search does not distinguish them, and a
  ROM library has no use for any of them. Configurable via
  `include_support`, off by default.
* **`--platform` is applied to the architecture icon** rather than sent
  to the server, because Aminet has no architecture filter. A RomM
  platform this source has nothing for returns an empty list **without a
  request**.

The walk stops at a **short page** -- fewer rows than Aminet's fixed 50 --
rather than at an empty result list. Those are not the same thing, and the
difference was paid for live: `steel sky` finds one package, that package
is on the `game/hint` shelf, and the filters drop it, so a walk keyed on
"nothing kept yet" asks for page 2 of a one-result search. That page is
real and valid and has no result table on it at all.

Results carry `platform=None` when the architecture does not map -- a
MorphOS or AROS build, or a package with no icon at all. That is
deliberate rather than an omission: the entry is real and findable, and
the importer is where it refuses, naming the machine. Hiding it would
mean an operator searching for a game they can see on Aminet's own site
getting nothing back and no reason.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .archive import PAGE_ROWS, SEARCH, AminetError, parse_results
from .platforms import BY_PLATFORM, describe, holds_games, platform_for

DEFAULT_MAX_PAGES = 2
PAGE_CAP = 10


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        wanted = (platform or "").strip().lower()
        if wanted and wanted not in BY_PLATFORM:
            # Aminet is Amiga-family software. Asking it for SNES is a
            # reasonable question with a boring answer, and answering it
            # for free is better than answering it slowly.
            return []
        accepted = BY_PLATFORM.get(wanted, ()) if wanted else ()

        params: dict[str, str | int] = {"dir": "game"}
        terms = (query or "").strip()
        if terms:
            params["query"] = terms

        include_support = bool(self.ctx.config.get("include_support"))

        results: list[SearchResult] = []
        for page in range(1, self._max_pages() + 1):
            if len(results) >= limit:
                break
            packages = self._page({**params, "page": page})
            for package in packages:
                if len(results) >= limit:
                    break
                if not include_support and holds_games(package.directory) is not True:
                    continue
                slug = platform_for(package.architecture)
                if accepted and package.architecture not in accepted:
                    continue
                try:
                    results.append(
                        SearchResult(
                            source_id=package.path,
                            title=package.filename,
                            # None when the architecture does not map. The
                            # importer refuses rather than guessing; see
                            # platforms.py.
                            platform=slug,
                            url=package.page_url,
                            extra={
                                "description": package.description,
                                "directory": package.directory,
                                "shelf": describe(package.directory),
                                # Aminet's own word for the target machine,
                                # carried whether or not it maps.
                                "architecture": package.architecture
                                or "|".join(package.architectures),
                                "size_text": package.size_text,
                                "date": package.date_text,
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # Thirty years of community uploads land some very odd
                    # text in these fields. One bad row must not cost the
                    # page.
                    continue
            # A short page is the last page. Checked on the *rows Aminet
            # served*, never on the results kept: `steel sky` finds one
            # package, it is on a `game/hint` shelf, and the filters drop
            # it -- so "no results yet" would ask for page 2 of a
            # one-result search. That page exists, is valid, and carries
            # no table, which is exactly the shape a dead source has.
            if len(packages) < PAGE_ROWS:
                break
        return results

    def _max_pages(self) -> int:
        raw = self.ctx.config.get("max_pages", DEFAULT_MAX_PAGES)
        try:
            pages = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_PAGES
        return max(1, min(pages, PAGE_CAP))

    def _page(self, params: dict):
        response = self.ctx.http.get(SEARCH, params=params)
        if response.status_code != 200:
            raise AminetError(
                f"Aminet returned HTTP {response.status_code} for {SEARCH!r}"
            )
        return parse_results(response.text)
