"""Search by walking cached directory indexes.

There is no query endpoint on a file mirror, so "search" here means: read
the index for each configured directory, once, and match the query against
the file names in it. Everything interesting follows from that:

**The index is cached, not re-fetched.** A No-Intro platform directory is
hundreds of kilobytes of HTML listing thousands of files; pulling it again
for every keystroke would be rude to a mirror that is giving bandwidth away
and slow for the operator. `index.INDEXES` is process-wide and shared with
the importer, so an import that follows a search costs no extra request.

**One request at a time, and as few as possible.** The plugin adds no
concurrency -- it has no sockets, and `ctx.http` is an RPC the host serves
serially anyway -- and the walk stops the moment `limit` results exist, so
a query answered by the first directory never touches the second.

**`--platform` narrows before any request.** The platform of a file *is*
its directory here, so filtering by platform is filtering the list of
directories to open, which is the difference between one fetch and twelve.

**Every configured directory must be mappable, and that is checked first.**
An unmapped directory is a misconfiguration, not a per-result oddity: it
means every ROM found in it would be filed under a platform nobody chose.
Raising before the first request makes it cost nothing and impossible to
miss.
"""

from urllib.parse import quote

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .index import INDEXES, IndexError_
from .platforms import platform_for

DEFAULT_BASE_URL = "https://archive.org/download/"


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

    `safe="/"` because a Myrient-layout directory *is* a path
    (`No-Intro/Nintendo - Game Boy`), while spaces and parentheses in it
    still have to be encoded.
    """
    return root + quote(directory.strip().strip("/"), safe="/") + "/"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        root = base_url(self.ctx.config.get("base_url"))
        directories = self._directories()
        wanted = (platform or "").strip().lower() or None
        terms = [t for t in (query or "").lower().split() if t]

        results: list[SearchResult] = []
        for directory, slug in directories:
            if len(results) >= limit:
                break
            if wanted and slug != wanted:
                continue
            for entry in INDEXES.get(self.ctx.http, index_url(root, directory)):
                if len(results) >= limit:
                    break
                if not entry.is_payload:
                    continue
                if terms and not all(t in entry.name.lower() for t in terms):
                    continue
                try:
                    results.append(
                        SearchResult(
                            source_id=f"{directory}/{entry.name}",
                            title=entry.name,
                            platform=slug,
                            size_bytes=entry.size_bytes,
                            url=index_url(root, directory) + entry.href,
                            extra={"directory": directory},
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    # Names and sizes come from upstream markup and land in
                    # constrained fields. One bad row must not cost the
                    # rest of the directory.
                    continue
        return results

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


# Re-exported so a caller catching plugin failures has one name to catch for
# "the index could not be read" alongside ConfigError.
__all__ = ["ConfigError", "IndexError_", "Search", "base_url", "index_url"]
