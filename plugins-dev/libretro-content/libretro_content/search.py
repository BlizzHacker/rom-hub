"""Search libretro's content buildbot.

There is no query endpoint. The buildbot is a static directory tree, so a
search here is "fetch some listings and match names in them", and the only
real design question is **how many listings**.

`--platform` answers it exactly: one RomM slug maps to one directory, so a
platform-scoped search is a single request and a platform this source has
nothing for -- Jaguar, say -- returns an empty list **without** a request.
That is not an error; it is a reasonable question with a boring answer.

**Without `--platform` the walk is now all 29 mapped directories, and
that is a measurement rather than a nerve.** The default used to be eight
systems, which reached 104 of the source's 274 files -- and the reason
given was that walking 29 "does not reliably finish" inside the host's
30-second ceiling. It does. Measured 2026-08-01: **29 listings, 131 KB**
-- 12.8 seconds fetched one connection at a time, and **2.1 seconds**
through the Hub's broker, which keeps the connection alive. The
pessimistic figure already fits with room; the real one is nowhere near
the ceiling. So the default is every directory this plugin can map, the
cap is the same 29 because there is no thirtieth, and the walk still
stops the moment `limit` is reached -- so the common case is one or two
requests and the full cost is only paid by a query that matches nothing.

**Listings are cached for the life of the process** and shared with the
importer, so an import that follows a search costs no request at all.

This source is small, and saying so is more useful than implying
otherwise: 274 files across 29 systems is the whole of libretro's free
content shelf. What changed here is that a plugin which could see 104 of
them now sees all 274.

Matching is a case-insensitive substring over the filename, with every
whitespace-separated term required. `alter ego` and `ego alter` both find
`Alter Ego.nes`; nothing is reordered or stemmed.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .buildbot import LISTINGS, directory_url
from .platforms import DIRECTORIES, directory_for, platform_for

#: File counts per system from a live walk on 2026-08-01, used only to
#: order the default walk. A directory absent from this map sorts last,
#: which is the right answer for one added later that nobody has counted.
_ORDER: dict[str, int] = {
    slug: rank
    for rank, slug in enumerate(
        (
            "wasm-4",        # 63
            "handheld-electronic-lcd",  # 59
            "vectrex",       # 44
            "nes",           # 26
            "genesis",       # 15
            "tic-80",        # 12
            "gb",            # 11
            "supergrafx",    # 5
            "tg16",          # 5
            "snes",          # 4
            "n64",           # 4
            "dos",           # 4
            "dc",            # 3
            "gba",           # 2
            "virtualboy",    # 2
            "psx",           # 2
        )
    )
}

#: Walked when the operator names no platform and configures no systems:
#: every directory this plugin can map. Ordered by where the content
#: actually is, largest shelf first, so a small `--limit` is answered from
#: the directories most likely to hold the answer -- counts from a live
#: walk on 2026-08-01.
DEFAULT_SYSTEMS = tuple(
    sorted(DIRECTORIES, key=lambda slug: _ORDER.get(slug, 99))
)

#: Every mapped directory, because a 29-listing walk is 131 KB and 2.1
#: seconds through the broker against a 30-second ceiling -- measured,
#: not assumed.
DEFAULT_MAX_SYSTEMS = 29
#: There is no thirtieth directory to reach, so the cap is the table.
MAX_SYSTEMS_CAP = 29


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        wanted = (platform or "").strip()
        if wanted:
            directory = directory_for(wanted)
            if directory is None:
                # libretro publishes free content for 29 systems and no
                # others. Asking for one of the rest costs no request.
                return []
            systems = [directory]
        else:
            systems = self._systems()

        terms = [t for t in (query or "").lower().split() if t]

        results: list[SearchResult] = []
        for directory in systems:
            if len(results) >= limit:
                break
            slug = platform_for(directory)
            if slug is None:
                # Only reachable through a configured `systems` entry that
                # is not in the table; `directory_for` cannot produce one.
                continue
            for item in self._listing(directory):
                if len(results) >= limit:
                    break
                if item.is_dir:
                    continue
                if not _matches(item.name, terms):
                    continue
                try:
                    results.append(
                        SearchResult(
                            # Both halves are needed to fetch the file, and
                            # a listing name may contain anything, so they
                            # are joined with the one character a filename
                            # on this server never contains.
                            source_id=f"{directory}/{item.name}",
                            title=item.name,
                            platform=slug,
                            url=directory_url(directory),
                            extra={
                                "system": directory,
                                "filename": item.name,
                                # h5ai's rounded display string. Never a
                                # byte count -- see buildbot.py.
                                "size_text": item.size_text,
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # A filename the wire type refuses. One bad row must
                    # not cost the rest of the listing.
                    continue
        return results

    # -- configuration ---------------------------------------------------

    def _systems(self) -> list[str]:
        """The directories to walk, bounded.

        Config carries RomM slugs rather than directory names, because a
        slug is what an operator already types at `--platform` and the
        directory spellings are libretro's (`Nintendo - GameBoy`, no
        space). A slug this source has nothing for is dropped rather than
        raising: it is the same "boring answer" as `--platform`.
        """
        raw = self.ctx.config.get("systems") or []
        slugs = [str(s).strip() for s in raw if str(s).strip()] or list(
            DEFAULT_SYSTEMS
        )
        directories: list[str] = []
        for slug in slugs:
            directory = directory_for(slug)
            if directory is not None and directory not in directories:
                directories.append(directory)
        return directories[: self._max_systems()]

    def _max_systems(self) -> int:
        raw = self.ctx.config.get("max_systems", DEFAULT_MAX_SYSTEMS)
        try:
            count = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_SYSTEMS
        return max(1, min(count, MAX_SYSTEMS_CAP))

    # -- transport -------------------------------------------------------

    def _listing(self, directory: str):
        """One directory's rows, from the process cache when it has them."""
        return LISTINGS.get(self.ctx.http, directory)


def _matches(name: str, terms: list[str]) -> bool:
    """Every term appears somewhere in the filename.

    An empty term list matches everything, which is what makes
    `rom-hub search libretro-content "" --platform vectrex` a browse.
    """
    lowered = name.lower()
    return all(term in lowered for term in terms)
