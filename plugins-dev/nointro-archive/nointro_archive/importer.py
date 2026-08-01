"""Turn `<directory>/<file>` into a FetchPlan.

The plugin decides *what* to fetch and nothing else: `ctx.http` is an RPC
back to the host, and the host re-validates every URL in the returned plan
against this plugin's manifest allowlist before fetching any of it --
including each hop of the redirect Archive.org answers with, which is why
`*.archive.org` is declared alongside `archive.org`.

Three decisions here are load-bearing:

**The file is confirmed to exist in the index, not assumed.** A URL could
be built from the source id by string concatenation alone, and it would be
wrong the moment a set is rebuilt and a file renamed -- the host would
fetch the mirror's 404 page, hash it, upload it, and report DONE. So the
directory index is read (from the cache the search already filled, usually
at no cost) and the entry is matched by name. No entry, no plan.

**The URL is built from the index's own href, not from the name.** Mirrors
percent-encode differently, and a name round-tripped through a guess at
encoding is a name that sometimes 404s. The href is what the server said.

**The platform comes from the directory, and is never guessed.** See
platforms.py.
"""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename
from .index import INDEXES
from .platforms import known_directories, platform_for
from .search import base_url, index_url

DEFAULT_COLLECTION = "No-Intro"


class ImportRefused(Exception):
    """This file cannot be imported, and the message says why."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        root = base_url(self.ctx.config.get("base_url"))
        directory, name = self._split(result.source_id)

        # 1. Which platform? Asked before any request, so a source id from a
        #    directory nobody mapped costs nothing to refuse.
        platform = platform_for(directory)
        if platform is None:
            raise ImportRefused(
                f"directory {directory!r} needs mapping: it is not in this "
                f"plugin's directory -> RomM platform table, and guessing would "
                f"file the ROM under the wrong system. Add it to "
                f"nointro_archive/platforms.py."
            )

        # 2. Does the file actually exist there?
        listing = index_url(root, directory)
        entries = INDEXES.get(self.ctx.http, listing)
        entry = next((e for e in entries if e.name == name), None)
        if entry is None:
            raise ImportRefused(
                f"{name!r} is not in the directory index at {listing!r}; the "
                f"mirror lists {len(entries)} entries there. Sets get rebuilt "
                f"and files get renamed, so a stale source id has to fail here "
                f"rather than fetch the mirror's 404 page."
            )
        if entry.is_dir:
            raise ImportRefused(
                f"{name!r} is a directory in {listing!r}, not a file. This "
                f"plugin imports files; it does not descend into subdirectories."
            )
        if not entry.is_payload:
            raise ImportRefused(
                f"{name!r} is mirror bookkeeping (its name ends in one of the "
                f"suffixes Archive.org appends to every item), not a ROM."
            )

        return FetchPlan(
            files=[
                FetchFile(
                    # The server's own href: encoding is its business, not a
                    # guess made here.
                    url=listing + entry.href,
                    # What the host opens for writing. FetchFile rejects
                    # anything but a bare name, so the mirror's name is
                    # sanitised into one deterministically.
                    filename=safe_filename(entry.name, fallback="rom.zip"),
                    size_bytes=entry.size_bytes,
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    def _split(self, source_id: str | None) -> tuple[str, str]:
        """Split `<directory>/<file>` at a boundary that is never guessed.

        A Myrient-layout directory contains slashes of its own
        (`No-Intro/Nintendo - Game Boy`), so the split cannot be "last
        slash wins" and inventing a separator would make source ids that no
        longer look like the paths they are. The boundary is therefore
        matched against a list of directories this plugin knows, and the
        list is consulted in two parts:

        **The configured `collections` first.** That is what a search
        returned, and it is the common case.

        **Then every directory in the platform table.** This is what the
        census made necessary. `rom-hub census build` enumerates all 71
        `identifier:nointro*` items -- roughly twice what `collections`
        lists -- and refusing to import a row the Hub itself just
        catalogued, on the grounds that nobody had typed its directory into
        a *search* config key, would be the Hub arguing with its own
        catalogue. A directory in the table is one this plugin can name the
        machine for, which is the only thing the import actually needs.

        Not a loosening of the "no guessing" rule: `platform_for` is an
        exact match against a written table, so a directory nobody has
        mapped still fails, and it fails here with its name in the message.
        """
        raw = (source_id or "").strip().strip("/")
        if not raw:
            raise ImportRefused("the search result carries no source id")

        configured = [
            str(d).strip().strip("/") for d in (self.ctx.config.get("collections") or [])
        ]
        folded = raw.lower()
        # Longest first, because `NoIntro-Atari` and
        # `NoIntro-Atari/Atari - Lynx` are both real directories and the
        # shorter one is a prefix of the longer. Matching the short one on
        # a Lynx source id leaves a name with a slash in it, which is
        # rejected below -- but only after the right answer has been passed
        # over, so the order is what makes this correct rather than lucky.
        for directory in sorted(
            configured + known_directories(), key=len, reverse=True
        ):
            prefix = directory.lower() + "/"
            if folded.startswith(prefix):
                name = raw[len(prefix) :]
                if not name or "/" in name:
                    continue
                # The caller's own spelling, not the table's. The table is
                # case-folded for lookup; the URL is built from this, and
                # Archive.org's paths are case-sensitive.
                return raw[: len(prefix) - 1], name
        raise ImportRefused(
            f"{source_id!r} does not name a file in any directory this plugin "
            f"knows. Expected '<directory>/<file>', where <directory> is one "
            f"of the configured collections ({configured!r}) or a directory in "
            f"nointro_archive/platforms.py."
        )
