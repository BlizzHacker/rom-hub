"""Search and browse Aminet's game tree.

Two endpoints, because Aminet has two and they answer different questions.

**Browse -- `/game/think?page=N`.** A shelf listing is a real page with
the same result table the search returns, and it is scoped by the server
to that shelf. This is what an empty query now does: it walks the
fourteen game shelves, in order, paging each one, and it is the only way
to reach the tree's whole 5,737 game packages. Before this, an empty
query sent `/search?dir=game` with no `query` at all, and Aminet answered
that with its **search form** -- no count line, no rows -- which the
parser correctly refused. So `rom-hub search aminet ""` failed outright,
and there was nothing to page.

**Search -- `/search?query=<terms>&page=N`.** One request across all
85,453 packages, which is why this source was picked over walking
`/tree`. A match five thousand packages deep comes back in it.

**`dir=` was never a filter and is gone.** `?query=tetris`,
`&dir=game`, `&dir=demo` and `&dir=zzz` all return the identical 134
packages -- verified live 2026-08-01. Aminet's search form has one field
and it is `query`; `dir` was an invented parameter that HTTP 200 made
look like it worked, and this plugin's own README claimed a server-side
scoping that never happened. Rows from `comm/dlg` were arriving in every
"game" search and being dropped here, which is what the symptom looked
like from the inside.

So a *searched* result set is still filtered client-side, and that costs
rows: a page of 50 that is mostly `util/` yields a handful. The honest
mitigation is to page further rather than to claim a scope, which is what
`max_pages` at 4 (up from 2) and a cap of 20 are for. A *browsed* result
set needs no filtering at all, because the shelf is the scope.

`--platform` is applied to the architecture icon, because Aminet has no
architecture filter of any kind. A RomM platform this source has nothing
for returns an empty list **without a request**.

The walk stops at a **short page** -- fewer rows than Aminet's fixed 50
-- and on the count line, never on "nothing kept yet". `steel sky` finds
one package, that package is on the `game/hint` shelf, and the filters
drop it; a walk keyed on results would ask for page 2 of a one-result
search, which is a real page with no table on it.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .archive import (
    PAGE_ROWS,
    SEARCH,
    AminetError,
    parse_results,
    shelf_url,
    total_matches,
)
from .platforms import (
    BY_PLATFORM,
    GAME_DIRS,
    describe,
    holds_games,
    platform_for,
)

#: Pages one search or browse may fetch, in total across every shelf it
#: touches. Raised from 2: a searched page is filtered client-side, so
#: two pages of 50 could yield a dozen usable rows and then stop with no
#: sign that it had.
DEFAULT_MAX_PAGES = 4
#: One page is one round trip and the host kills a plugin at 30 seconds.
#: 20 pages of ~65 KB is about as far as that goes with room to spare.
PAGE_CAP = 20

#: The shelves a browse walks when the operator configures none: Aminet's
#: fourteen game shelves, in the archive's own order. `game/data`,
#: `game/edit`, `game/hint` and `game/patch` are absent because they hold
#: data files, level editors, walkthroughs and patches -- `include_support`
#: adds them, and they still refuse to import.
DEFAULT_SHELVES: tuple[str, ...] = tuple(
    name for name, (_, holds) in GAME_DIRS.items() if holds
)

#: The same list plus the four support shelves, for `include_support`.
ALL_SHELVES: tuple[str, ...] = tuple(GAME_DIRS)


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

        terms = (query or "").strip()
        include_support = bool(self.ctx.config.get("include_support"))
        budget = self._max_pages()

        if terms:
            pages = self._search_pages(terms, budget)
        else:
            pages = self._browse_pages(self._shelves(include_support), budget)

        results: list[SearchResult] = []
        for packages in pages:
            for package in packages:
                if len(results) >= limit:
                    return results
                if not include_support and holds_games(package.directory) is not True:
                    continue
                if accepted and package.architecture not in accepted:
                    continue
                result = self._result(package)
                if result is not None:
                    results.append(result)
            if len(results) >= limit:
                break
        return results

    # -- the two walks ---------------------------------------------------

    def _search_pages(self, terms: str, budget: int):
        """Pages of `/search?query=`, and still the fast path for a query.

        Yields lists of packages. Unscoped, because there is no scoping
        parameter -- see the module docstring -- so the caller filters.
        """
        for page in range(1, budget + 1):
            packages, total = self._page(SEARCH, {"query": terms, "page": page})
            yield packages
            if self._last_page(packages, total, page):
                return

    def _browse_pages(self, shelves: list[str], budget: int):
        """Pages of `/<tree>/<shelf>`, one shelf at a time until the budget
        is spent.

        The shelf *is* the scope, so nothing yielded here needs dropping
        for being on the wrong shelf. Shelves are walked in order and a
        shelf that runs out moves on to the next, so a browse with a
        small `limit` costs one request and a browse with a large one
        walks as deep as it is allowed.
        """
        spent = 0
        for shelf in shelves:
            page = 1
            while spent < budget:
                spent += 1
                packages, total = self._page(shelf_url(shelf), {"page": page})
                yield packages
                if self._last_page(packages, total, page):
                    break
                page += 1
            if spent >= budget:
                return

    @staticmethod
    def _last_page(packages, total, page: int) -> bool:
        """Whether the page just read was the final one.

        Two independent signals, and both are about what Aminet served
        rather than about what survived the filters:

        * the count line -- `Found 910 matching packages` -- says exactly
          where the last page is, so a walk need never ask for one past
          the end;
        * a short page is the last page, which still holds when the count
          line is missing.
        """
        if len(packages) < PAGE_ROWS:
            return True
        return total is not None and page * PAGE_ROWS >= total

    # -- configuration ---------------------------------------------------

    def _shelves(self, include_support: bool) -> list[str]:
        """The shelves a browse walks, from config or the default set.

        A configured shelf that Aminet's `game/` tree does not have is
        refused by name rather than requested: it would be a URL built
        from a typo, and the answer would be the site's themed error page
        arriving as HTTP 200.
        """
        raw = self.ctx.config.get("shelves") or []
        configured = [str(s).strip().strip("/").lower() for s in raw]
        configured = [s for s in configured if s]
        if not configured:
            return list(ALL_SHELVES if include_support else DEFAULT_SHELVES)
        unknown = [s for s in configured if s not in GAME_DIRS]
        if unknown:
            raise AminetError(
                f"`shelves` names {unknown!r}, which is not in Aminet's game "
                f"tree. The tree has: {', '.join(sorted(GAME_DIRS))}."
            )
        # Order-preserving dedupe: two entries for one shelf would page it
        # twice out of one budget.
        return list(dict.fromkeys(configured))

    def _max_pages(self) -> int:
        raw = self.ctx.config.get("max_pages", DEFAULT_MAX_PAGES)
        try:
            pages = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_PAGES
        return max(1, min(pages, PAGE_CAP))

    # -- one page --------------------------------------------------------

    def _page(self, url: str, params: dict):
        response = self.ctx.http.get(url, params=params)
        if response.status_code != 200:
            raise AminetError(
                f"Aminet returned HTTP {response.status_code} for {url!r}"
            )
        return parse_results(response.text), total_matches(response.text)

    @staticmethod
    def _result(package) -> SearchResult | None:
        try:
            return SearchResult(
                source_id=package.path,
                title=package.filename,
                # None when the architecture does not map. The importer
                # refuses rather than guessing; see platforms.py.
                platform=platform_for(package.architecture),
                url=package.page_url,
                extra={
                    "description": package.description,
                    "directory": package.directory,
                    "shelf": describe(package.directory),
                    # Aminet's own word for the target machine, carried
                    # whether or not it maps.
                    "architecture": package.architecture
                    or "|".join(package.architectures),
                    "size_text": package.size_text,
                    "date": package.date_text,
                },
            )
        except (ValidationError, TypeError, ValueError):
            # Thirty years of community uploads land some very odd text in
            # these fields. One bad row must not cost the page.
            return None
