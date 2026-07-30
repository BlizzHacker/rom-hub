"""Search libretro's content buildbot.

There is no query endpoint. The buildbot is a static directory tree, so a
search here is "fetch some listings and match names in them", and the only
real design question is **how many listings**.

`--platform` answers it exactly: one RomM slug maps to one directory, so a
platform-scoped search is a single request and a platform this source has
nothing for -- Jaguar, say -- returns an empty list **without** a request.
That is not an error; it is a reasonable question with a boring answer.

Without `--platform` the plugin walks the systems in `systems` (RomM slugs,
defaulting to the eight below) and stops at `max_systems`. The bound is not
politeness: the host kills a plugin at 30 seconds, each listing is its own
round trip, and a walk of all 29 mapped directories does not reliably
finish inside that. An unbounded default would have made "no results" and
"timed out" look the same from the outside.

Matching is a case-insensitive substring over the filename, with every
whitespace-separated term required. `alter ego` and `ego alter` both find
`Alter Ego.nes`; nothing is reordered or stemmed.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .buildbot import BuildbotError, directory_url, parse_listing
from .platforms import directory_for, platform_for

#: Walked when the operator names no platform and configures no systems.
#: Chosen for where the free content actually is: these eight directories
#: hold the bulk of the homebrew on the buildbot.
DEFAULT_SYSTEMS = (
    "nes",
    "snes",
    "genesis",
    "gb",
    "gba",
    "sms",
    "atari2600",
    "vectrex",
)

DEFAULT_MAX_SYSTEMS = 8
#: One listing is one round trip and the host's ceiling is 30 seconds.
MAX_SYSTEMS_CAP = 24


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
        url = directory_url(directory)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise BuildbotError(
                f"the libretro buildbot returned HTTP {response.status_code} "
                f"for {url!r}"
            )
        return parse_listing(response.text)


def _matches(name: str, terms: list[str]) -> bool:
    """Every term appears somewhere in the filename.

    An empty term list matches everything, which is what makes
    `rom-hub search libretro-content "" --platform vectrex` a browse.
    """
    lowered = name.lower()
    return all(term in lowered for term in terms)
