"""Search by walking cached directory indexes.

There is no query endpoint on a file mirror, so "search" here means: read
the index for each configured directory, once, and match the query against
the file names in it. Everything interesting follows from that:

**The index is cached, not re-fetched.** A No-Intro platform directory is
hundreds of kilobytes of HTML listing thousands of files; pulling it again
for every keystroke would be rude to a mirror that is giving bandwidth away
and slow for the operator. `index.INDEXES` is process-wide and shared with
the importer, so an import that follows a search costs no extra request.

**`--platform` narrows before any request.** The platform of a file *is*
its directory here, so filtering by platform is filtering the list of
directories to open, which is the difference between one fetch and
twenty-five. It is also what makes the whole shipped set reachable: one
platform is one index, about a second, whatever else is configured.

**Every configured directory must be mappable, and that is checked first.**
An unmapped directory is a misconfiguration, not a per-result oddity: it
means every ROM found in it would be filed under a platform nobody chose.
Raising before the first request makes it cost nothing and impossible to
miss.

**A platform-less search is budgeted, and the budget is measured.**
Reading all twenty-five shipped indexes takes **34.8 seconds and 8.75 MB**
-- timed against Archive.org on 2026-08-01, index by index -- and the host
kills a plugin at 30. So `max_directories` bounds how many a search with no
`--platform` will open. That is a real limit and it is stated rather than
hidden: without a platform this plugin samples the first N directories in
configured order; with one, nothing is out of reach.

**Results are ranked, not taken in directory order.** The previous version
stopped at the first `limit` matches it happened to find, which meant a
`--limit 10` search returned ten Game Gear rows and never looked at the
other directories -- so an exact title match further down the list was
invisible behind ten near-misses at the top. Matches are now scored
(exact title, then prefix, then substring, shortest name first) across
every directory the walk opened, and the best ones are returned. Grouping
in the host then collapses regional variants of one game into one row, so
a wider net costs the operator nothing.
"""

import re
from urllib.parse import quote

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .index import INDEXES, IndexError_
from .platforms import platform_for

DEFAULT_BASE_URL = "https://archive.org/download/"

#: How many indexes a search with no `--platform` may open. Ten is about
#: fourteen seconds against the host's thirty-second ceiling, measured.
DEFAULT_MAX_DIRECTORIES = 10
#: More than the shipped set, so an operator who configures their own
#: mirror layout is not capped by a number chosen for this one.
MAX_DIRECTORIES_CAP = 32

#: Directories a platform-less walk opens before it is allowed to stop on
#: "enough results". One is not enough: the first directory would answer
#: every small-limit query on its own, which is the bias this ranking
#: exists to remove.
MIN_DIRECTORIES = 3

#: Punctuation, region tags and the extension are noise when comparing a
#: query to a No-Intro filename. `Sonic The Hedgehog (USA, Europe).zip`
#: and `sonic the hedgehog` should score as an exact match.
_NON_WORD = re.compile(r"[^0-9a-z]+")
_BRACKETED = re.compile(r"[\(\[][^\)\]]*[\)\]]")


class ConfigError(Exception):
    """The plugin's configuration cannot be used as given."""


def base_url(configured) -> str:
    """The mirror root, normalised, or an error naming what is wrong."""
    url = (configured or DEFAULT_BASE_URL).strip()
    if not url.startswith("https://"):
        # The broker refuses anything but https anyway; failing here says
        # why, instead of leaving a policy violation per request.
        raise ConfigError(
            f"base_url {url!r} must be an https:// URL -- the Hub's broker "
            f"refuses every other scheme"
        )
    return url if url.endswith("/") else url + "/"


def index_url(root: str, directory: str) -> str:
    """The URL of one directory's index.

    `safe="/"` because a directory *is* a path -- a Myrient-layout
    `No-Intro/Nintendo - Game Boy`, or an Archive.org item with a
    per-system subdirectory inside it, `NoIntro-Atari/Atari - Lynx` --
    while spaces and parentheses in it still have to be encoded.
    """
    return root + quote(directory.strip().strip("/"), safe="/") + "/"


def title_key(name: str) -> str:
    """A filename reduced to the words a person would have typed.

    Extension gone, bracketed region and revision tags gone, punctuation
    gone. `Sonic The Hedgehog (USA, Europe) (Rev A).zip` becomes
    `sonic the hedgehog`, which is what makes an exact-match score
    possible at all on a set whose every filename carries a region.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = _BRACKETED.sub(" ", stem)
    return " ".join(_NON_WORD.sub(" ", stem.lower()).split())


def score(name: str, query: str, terms: list[str]) -> int:
    """How well one filename answers one query. Higher is better.

    Three tiers rather than a similarity metric, because the useful
    distinction here is coarse and a metric would invent precision:

      3  the title *is* the query, once regions and punctuation are gone
      2  the title starts with the query
      1  every term appears somewhere in the filename

    A browse (no query) scores everything 1, so the ordering falls
    through to the tie-breaks: shorter name first, then listing order.
    """
    if not terms:
        return 1
    key = title_key(name)
    wanted = " ".join(_NON_WORD.sub(" ", query.lower()).split())
    if wanted and key == wanted:
        return 3
    if wanted and key.startswith(wanted):
        return 2
    return 1


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        root = base_url(self.ctx.config.get("base_url"))
        directories = self._directories()
        wanted = (platform or "").strip().lower() or None
        terms = [t for t in (query or "").lower().split() if t]

        if wanted:
            # One platform is one directory, so the budget does not apply:
            # asking for `--platform gb` must reach every Game Boy ROM
            # whatever `max_directories` says.
            selected = [(d, s) for d, s in directories if s == wanted]
        else:
            selected = directories[: self._max_directories()]

        # A browse has no query to rank against, so the first directory
        # answers it as well as any three would and the extra reads would
        # buy nothing. A *query* is the case where stopping early hides a
        # better match one directory further down.
        min_open = min(MIN_DIRECTORIES, len(selected)) if terms else 1

        candidates: list[tuple[int, int, int, SearchResult]] = []
        opened = 0
        for order, (directory, slug) in enumerate(selected):
            if len(candidates) >= limit and opened >= min_open:
                break
            url = index_url(root, directory)
            opened += 1
            for entry in INDEXES.get(self.ctx.http, url):
                if not entry.is_payload:
                    continue
                if terms and not all(t in entry.name.lower() for t in terms):
                    continue
                try:
                    result = SearchResult(
                        source_id=f"{directory}/{entry.name}",
                        title=entry.name,
                        platform=slug,
                        size_bytes=entry.size_bytes,
                        url=url + entry.href,
                        extra={"directory": directory},
                    )
                except (ValidationError, TypeError, ValueError):
                    # Names and sizes come from upstream markup and land in
                    # constrained fields. One bad row must not cost the
                    # rest of the directory.
                    continue
                # Negated score so a plain ascending sort puts the best
                # first; then shortest name, which prefers the base game
                # over its `(Rev 1) (Beta)` siblings; then the order the
                # operator configured, which is the only stable tie-break
                # left.
                candidates.append(
                    (-score(entry.name, query or "", terms), len(entry.name), order, result)
                )

        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        return [c[3] for c in candidates[:limit]]

    def _directories(self) -> list[tuple[str, str]]:
        """Configured directories with their platforms, or "needs mapping".

        Runs before any request: a directory nobody can map is a config
        error, and paying for a fetch to discover it helps nobody.
        """
        configured = self.ctx.config.get("collections") or []
        if not configured:
            raise ConfigError(
                "no collections configured: set `collections` to the directories "
                "to search, e.g. [\"nointro.gg\"]"
            )
        pairs = []
        for directory in configured:
            slug = platform_for(str(directory))
            if slug is None:
                raise ConfigError(
                    f"directory {directory!r} needs mapping: it is not in this "
                    f"plugin's directory -> RomM platform table, and guessing "
                    f"would file every ROM in it under the wrong system. Add it "
                    f"to nointro_archive/platforms.py."
                )
            pairs.append((str(directory), slug))
        return pairs

    def _max_directories(self) -> int:
        raw = self.ctx.config.get("max_directories", DEFAULT_MAX_DIRECTORIES)
        try:
            count = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_DIRECTORIES
        return max(1, min(count, MAX_DIRECTORIES_CAP))


# Re-exported so a caller catching plugin failures has one name to catch for
# "the index could not be read" alongside ConfigError.
__all__ = [
    "ConfigError",
    "IndexError_",
    "Search",
    "base_url",
    "index_url",
    "score",
    "title_key",
]
