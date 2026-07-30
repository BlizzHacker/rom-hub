"""Turn a Universal-DB slug into a FetchPlan.

The plugin decides *what* should be fetched and nothing else. `ctx.http` is
an RPC back to the host, and the host re-validates every URL in the
returned plan -- and every redirect hop it takes -- against this plugin's
own manifest allowlist before opening a socket.

Lookup is exact. `full.json` is a list keyed by nothing, so the entry is
found by comparing `slug` for equality; there is no near-miss path,
because importing "the closest thing to what you asked for" is the failure
this codebase refuses everywhere else.

Three decisions carry the weight, and each is the safe half of a choice
that could have gone the other way:

**The platform is never guessed, and DS is never 3DS.** An entry naming
one system resolves to that system. An entry naming both -- eight of them
do -- is refused without `--platform`, because it ships a different file
for each console and there is no defensible way to pick. An entry naming a
system this plugin has no row for raises "needs mapping" and says which.

**The payload comes from the database's own evidence.** A bare `.cia` or
`.nds` is a title by definition; an archive is taken only when the entry's
`archive` manifest says a title is inside it. Where the database is silent
or self-contradictory the import refuses and names the files it saw. See
`payload.py`.

**The licence travels with the plan.** It is why this source is usable, so
it is stated in the refusal messages and, when `require_license` is on,
enforced -- an entry the database says nothing about is not silently
turned into one that permits redistribution.
"""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .db import fetch_entries
from .filenames import safe_filename
from .payload import check_reachable, choose
from .platforms import TITLE_FORMATS

DEFAULT_COLLECTION = "Homebrew"


class ImportRefused(Exception):
    """This entry cannot be imported, and the message says why."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        slug = (result.source_id or "").strip()
        if not slug:
            raise ImportRefused(
                "the search result carries no Universal-DB slug"
            )

        entry = self._entry(slug)

        if bool(self.ctx.config.get("require_license", False)) and not entry.license_stated:
            raise ImportRefused(
                f"Universal-DB entry {slug!r} ({entry.title!r}) states no "
                f"licence, and `require_license` is on. The database records "
                f"one for 270 of its 400 entries; for the rest it says "
                f"nothing, and this plugin will not turn that silence into "
                f"permission. Its page is {entry.url}."
            )

        platform = self._platform(result, entry)
        choice = choose(entry, platform)
        check_reachable(entry, choice.download)

        return FetchPlan(
            files=[
                FetchFile(
                    url=choice.download.url,
                    # The database's own key for the asset is the name the
                    # file should have; the URL sometimes has no filename
                    # in it at all. Only this field has to be a bare name,
                    # and making it one is this plugin's job, not the
                    # host's -- see filenames.py.
                    filename=safe_filename(
                        choice.download.name, fallback=f"{slug}.bin"
                    ),
                    size_bytes=choice.download.size_bytes,
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    # -- lookup ----------------------------------------------------------

    def _entry(self, slug: str):
        entries = fetch_entries(self.ctx.http)
        entry = next((e for e in entries if e.slug == slug), None)
        if entry is None:
            raise ImportRefused(
                f"no Universal-DB entry has the slug {slug!r}. The slug is the "
                f"last path segment of the entry's page on "
                f"db.universal-team.net, and it is matched exactly -- there is "
                f"no nearest match, because importing one would file something "
                f"nobody asked for."
            )
        if not entry.is_title():
            raise ImportRefused(
                f"Universal-DB entry {slug!r} ({entry.title!r}) is categorised "
                f"{', '.join(entry.categories)!r}, which this plugin does not "
                f"treat as an installable title: a Luma plugin is injected "
                f"into another game, a FIRM is boot-chain firmware, and an "
                f"exploit is an entry point into a save file. None of the "
                f"three is a thing a ROM library holds. Its page is "
                f"{entry.url}."
            )
        return entry

    # -- platform --------------------------------------------------------

    @staticmethod
    def _platform(result: SearchResult, entry) -> str:
        override = (result.platform or "").strip().lower()
        if override:
            # An operator's --platform is authoritative about *which* of
            # the entry's platforms is meant -- but a platform this source
            # has no title format for is a typo, not an instruction, and
            # the message downstream would talk about extensions instead
            # of saying so.
            if override not in TITLE_FORMATS:
                raise ImportRefused(
                    f"--platform {override!r} is not one this plugin can file "
                    f"a Universal-DB entry under. It carries Nintendo 3DS "
                    f"(`3ds`) and Nintendo DS (`nds`) homebrew, and those are "
                    f"different consoles with different executable formats -- "
                    f"neither is a fallback for the other."
                )
            return override

        platforms = entry.platforms()
        if len(platforms) == 1:
            return platforms[0]
        if len(platforms) > 1:
            raise ImportRefused(
                f"Universal-DB entry {entry.slug!r} ({entry.title!r}) is "
                f"published for {' and '.join(platforms)}, and it ships a "
                f"different file for each -- they are different consoles, not "
                f"two names for one. Pass --platform to say which one you "
                f"want; this plugin will not choose, because a library that "
                f"quietly merged DS and 3DS cannot be un-merged afterwards."
            )

        unmapped = entry.unmapped_systems()
        raise ImportRefused(
            f"Universal-DB system(s) {', '.join(repr(s) for s in unmapped)} "
            f"(entry {entry.slug!r}) need mapping: they are not in this "
            f"plugin's system -> RomM platform table, and guessing would file "
            f"the ROM under the wrong console. Add them to "
            f"universal_db/platforms.py."
        )
