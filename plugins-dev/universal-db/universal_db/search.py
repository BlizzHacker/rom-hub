"""Search Universal-DB.

The whole database arrives in one 1.66 MB response and the query is
applied here, because Universal-DB publishes a file and not a search API.
That is the opposite trade to the Homebrew Hub plugin, which pushes `q` to
a server, and it is the right one for a source this size: 400 entries
filtered locally answers substring queries the site's own search cannot,
and costs one request instead of one per page.

**A result is one entry on one platform.** The eight entries published for
both DS and 3DS produce two rows, because they *are* two importable
things -- `PKCount.cia` and `PKCount.nds` are different files for
different consoles -- and a single row would have to pick one or claim
neither. Both rows carry the same `source_id`, which is the entry's slug;
`--platform` is what tells the importer which of the two you meant, and
without it that import is refused rather than guessed.

**Every row carries its licence.** `extra["license"]` is the database's own
human-readable name where it has one, its identifier where it has only
that, and `unstated` for the 130 entries of 400 that say nothing.
`unstated` is a fact about the database, not a licence, and it is shown
rather than hidden because the reason this source can be redistributed at
all is that the material carries its authors' own terms. `require_license`
turns the unstated ones off for an operator who wants only the stated
ones.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .db import NOT_TITLES, fetch_entries
from .platforms import system_for

#: Cap on rows scanned per query. The database is 400 entries and has
#: grown by roughly that much in six years, so this is headroom rather
#: than a limit anybody will meet -- but an unbounded loop over a remote
#: document is the kind of thing that is fine until the document changes.
MAX_ENTRIES = 20_000


def matches(entry, needles: list[str]) -> bool:
    """Whether every word of the query appears in the entry's name line.

    Title **and** author, because homebrew is known by both -- "Epicpkmn11"
    and "DS-Homebrew" are how a good deal of this catalogue is found -- and
    not description, which is a sentence of prose per entry and would make
    a two-letter query return most of the database. The Archive.org plugin
    learned the same lesson the expensive way: a bare term matched its
    default field and answered `Die Hard` for `sonic`.
    """
    if not needles:
        return True
    haystack = f"{entry.title} {entry.author}".lower()
    return all(needle in haystack for needle in needles)


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        wanted_system = None
        wanted = (platform or "").strip()
        if wanted:
            wanted_system = system_for(wanted)
            if wanted_system is None:
                # This database holds Nintendo 3DS and Nintendo DS
                # homebrew and nothing else. A reasonable question with a
                # boring answer, and answering it without a request is
                # better than answering it slowly.
                return []

        category = str(self.ctx.config.get("category") or "").strip().lower()
        require_license = bool(self.ctx.config.get("require_license", False))
        needles = [w for w in (query or "").lower().split() if w]

        results: list[SearchResult] = []
        for entry in fetch_entries(self.ctx.http)[:MAX_ENTRIES]:
            if len(results) >= limit:
                break
            if not entry.is_title():
                # Luma plugins, boot firmware and save exploits. Not
                # things a ROM library holds; see db.NOT_TITLES.
                continue
            if category and category not in entry.categories:
                continue
            if require_license and not entry.license_stated:
                continue
            if not matches(entry, needles):
                continue

            for slug in entry.platforms():
                if len(results) >= limit:
                    break
                if wanted_system is not None and slug != wanted:
                    continue
                try:
                    results.append(
                        SearchResult(
                            source_id=entry.slug,
                            title=entry.title,
                            platform=slug,
                            url=entry.url,
                            extra={
                                # The reason this source is usable. Shown
                                # on every row, `unstated` included.
                                "license": entry.license_label,
                                "license_id": entry.license_id or "",
                                "author": entry.author,
                                "category": ",".join(entry.categories),
                                "systems": ",".join(entry.systems),
                                "version": entry.version,
                                "updated": entry.updated,
                                "downloads": str(len(entry.downloads)),
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # Community-submitted text landing in constrained
                    # fields. One bad record must not cost the query.
                    continue
        return results
