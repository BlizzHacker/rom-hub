"""Turn a Homebrew Hub slug into a FetchPlan.

The Hub has no fetch-one-entry endpoint, so the entry is looked up by
querying its own slug and then **matching the slug exactly** in the
results. That last part is not a formality: `?q=<slug>` is a text search,
so it can answer with a near miss, and importing "the closest thing to what
you asked for" is precisely the failure this codebase refuses everywhere
else. No exact match, no plan.

Everything else is one decision each:

* **The platform is never guessed.** Live records exist with no `platform`
  field at all. Those refuse and ask for `--platform`, because the
  alternative -- reading `basepath: database-gb` as "Game Boy" -- would
  file every Game Boy Color entry in that database under `gb`.
* **The payload is the Hub's own `default: true` file,** falling back to
  the first listed. The submitter marking a file default is a statement;
  picking the largest, as the Archive.org and itch.io plugins must, is a
  heuristic used only where no such statement exists.
* **The URL keeps the entry-relative path, the filename does not.**
  `files/Game.nes` is where the file lives; `FetchFile.filename` is what
  the host opens for writing and must be a bare name, so the two come
  apart here rather than somewhere the host has to defend against.
"""

import json

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename
from .hub import API, parse_page
from .platforms import platform_for

DEFAULT_COLLECTION = "Homebrew"


class ImportRefused(Exception):
    """This entry cannot be imported, and the message says why."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        slug = (result.source_id or "").strip()
        if not slug:
            raise ImportRefused("the search result carries no Homebrew Hub slug")

        entry = self._entry(slug)
        platform = self._platform(result, entry)

        payload = entry.payload()
        if payload is None:
            raise ImportRefused(
                f"Homebrew Hub entry {slug!r} ({entry.title!r}) lists no files, "
                f"so there is nothing to fetch."
            )

        return FetchPlan(
            files=[
                FetchFile(
                    url=entry.download_url(payload),
                    filename=safe_filename(payload.filename, fallback="rom.bin"),
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    def _entry(self, slug: str):
        response = self.ctx.http.get(API, params={"q": slug, "page": 1})
        if response.status_code != 200:
            raise ImportRefused(
                f"the Homebrew Hub returned HTTP {response.status_code} looking "
                f"up {slug!r}"
            )
        try:
            payload = json.loads(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ImportRefused(
                f"the Homebrew Hub's answer for {slug!r} was not JSON: {exc}"
            ) from exc
        entries, _ = parse_page(payload)
        entry = next((e for e in entries if e.slug == slug), None)
        if entry is None:
            raise ImportRefused(
                f"no Homebrew Hub entry has the slug {slug!r}. The Hub's lookup "
                f"is a text search, so it answers near misses; importing one of "
                f"those would file something nobody asked for."
            )
        return entry

    @staticmethod
    def _platform(result: SearchResult, entry) -> str:
        # An operator's --platform reaches the plugin on the SearchResult
        # and is authoritative.
        override = (result.platform or "").strip()
        if override:
            return override
        if not entry.platform:
            raise ImportRefused(
                f"Homebrew Hub entry {entry.slug!r} ({entry.title!r}) declares no "
                f"platform, and its database name says only which repository it "
                f"came from -- 'database-gb' holds both Game Boy and Game Boy "
                f"Color titles. Pass --platform to say where it should be filed."
            )
        platform = platform_for(entry.platform)
        if platform is None:
            raise ImportRefused(
                f"Homebrew Hub platform {entry.platform!r} (entry {entry.slug!r}) "
                f"needs mapping: it is not in this plugin's platform -> RomM "
                f"table, and guessing would file the ROM under the wrong system. "
                f"Add it to homebrew/platforms.py."
            )
        return platform
