"""Universal-DB `metadata`: the author's own title and the icon they shipped.

    RomRef -> a Universal-DB entry -> MetadataPatch(name, artwork_url)

The plugin never fetches the picture. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist -- the same rule a FetchPlan URL follows, for the same reason.

**What this can deliver, and what it cannot, stated up front.** Every
entry in this database carries a description, a category, a version, an
update date, an author and usually a licence, and *none of that reaches
RomM*. RomM 4.9.2's update endpoint takes a name, a set of provider ids
and a cover; it accepts all eight `raw_*_metadata` fields with HTTP 200
and stores none of them -- verified against a live 4.9.2 -- so routing
this richness through one of those would be a write that reports success
and changes nothing. It stays in `SearchResult.extra`, where an operator
can see it, and this capability proposes the two things a library will
actually keep.

**`name` is worth having because a filename here is not a title.** A
Universal-DB import lands as `Universal-Updater.3dsx` or `PKCount.cia`;
the database's `title` is what the author called it. That is the same
argument the `homebrew` plugin makes and it holds for the same reason --
for homebrew the author *is* the publisher of record. `set_name` is
config, defaulting on, for the operator who has curated their spelling.

**`icon` before `image`, and the reason is not size.** 364 of 400 entries
publish a 48x48 `icon`, which is the picture the console's own home menu
shows for that title -- as close to box art as this material has. 399
publish an `image`, the 2D banner, and **26 of those are the author's
GitHub avatar**: a picture of a person or an organisation rather than of
a game. So the icon wins where there is one and the banner is the
fallback, and an entry with neither gets no `artwork_url` at all, which
`MetadataPatch` reads as "leave RomM's alone".

**Artwork on an undeclared host is skipped, not fetched.** The database
does not host these images; each points at wherever the author publishes,
and a census of all 400 entries on 2026-08-01 found two -- one
`nawiasdev.eu`, one `i.imgur.com` -- outside this plugin's allowlist.
Declaring two more hosts to reach two covers would widen where the plugin
can go for almost nothing, so those two fall through to the next
candidate and then to no artwork. The host's own check would refuse them
anyway; doing it here means the refusal is a shrug rather than an error.

**Resolution is exact or it is a refusal.** Matching a rom to the wrong
homebrew entry attaches another author's icon and another author's title,
which is the failure this codebase is built to avoid everywhere else.
"""

import posixpath
from urllib.parse import urlsplit

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .db import fetch_entries
from .filenames import safe_filename
from .platforms import system_for

DEFAULT_ARTWORK_FILENAME = "cover.png"

#: Where a Universal-DB slug may arrive. `source_id` is what the CLI's
#: `--source-id` fills in, and is the route that skips matching entirely.
SLUG_KEYS = ("universal_db_slug", "slug", "source_id")

#: Hosts this plugin's manifest declares, as a set an artwork URL can be
#: tested against before it is proposed.
#:
#: Duplicating the manifest is not free and it is the lesser evil: a
#: plugin cannot read its own manifest, the alternative is naming a URL
#: the host will refuse, and `test_the_artwork_hosts_match_the_manifest`
#: fails the build if the two ever disagree.
ARTWORK_HOSTS: frozenset[str] = frozenset(
    {
        "db.universal-team.net",
        "github.com",
        "raw.githubusercontent.com",
        "avatars.githubusercontent.com",
        "media.githubusercontent.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "gitlab.com",
        "codeberg.org",
    }
)


class NoMatch(Exception):
    """No Universal-DB entry could be identified for this rom."""


class Ambiguous(Exception):
    """Several entries match, and choosing between them is not this
    plugin's call to make."""


def match_key(label: str) -> str:
    """A title reduced to what two spellings of it have in common.

    Case, punctuation and whitespace runs are noise -- `Universal-Updater`
    and `universal updater` are the same thing -- but the comparison stays
    an equality test on the scrubbed form. A prefix or substring test
    would make `Snake` match `SnakeDS`.
    """
    if not isinstance(label, str):
        return ""
    return "".join(c for c in label.lower() if c.isalnum())


def declared_host(url: str) -> bool:
    """Whether an image URL is on a host this plugin declared.

    Wildcards are not expanded here: `*.githubusercontent.com` is spelled
    out in `ARTWORK_HOSTS` as the four names it actually resolves to in
    this database, because an unbounded suffix test is a different and
    larger claim than the manifest makes.
    """
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    return urlsplit(url).netloc.lower() in ARTWORK_HOSTS


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        entry = self._resolve(rom)

        patch: dict = {}
        if self._set_name() and entry.title:
            patch["name"] = entry.title

        artwork = self._artwork(entry)
        if artwork is not None:
            patch["artwork_url"] = artwork
            patch["artwork_filename"] = safe_filename(
                posixpath.basename(urlsplit(artwork).path),
                fallback=DEFAULT_ARTWORK_FILENAME,
            )

        if not patch:
            # An empty patch would have the host report an enrich that
            # succeeded and changed nothing, which reads as "there was
            # nothing to add" rather than "this entry has no picture and
            # you turned the title off".
            raise NoMatch(
                f"Universal-DB entry {entry.slug!r} ({entry.title!r}) has no "
                f"artwork this plugin may fetch, and `set_name` is off, so "
                f"there is nothing left to propose."
            )
        return MetadataPatch(**patch)

    # -- what to propose -------------------------------------------------

    @staticmethod
    def _artwork(entry) -> str | None:
        """The icon, else the banner, else nothing -- on a declared host."""
        for candidate in (entry.icon, entry.image):
            if candidate and declared_host(candidate):
                return candidate
        return None

    def _set_name(self) -> bool:
        return bool(self.ctx.config.get("set_name", True))

    # -- resolution ------------------------------------------------------

    def _resolve(self, rom: RomRef):
        entries = fetch_entries(self.ctx.http)

        slug = self._slug(rom)
        if slug:
            entry = next((e for e in entries if e.slug == slug), None)
            if entry is None:
                raise NoMatch(
                    f"no Universal-DB entry has the slug {slug!r}. The whole "
                    f"database was read, so this is not a paging miss -- that "
                    f"slug is not in it."
                )
            return entry

        titles = self._candidate_titles(rom)
        if not titles:
            raise NoMatch(
                f"rom {rom.rom_id} has neither a name nor a filename in the "
                f"library, so there is nothing to look up. Pass --source-id "
                f"with the Universal-DB slug instead."
            )

        title = titles[0]
        candidates: list = []
        for candidate in titles:
            wanted = match_key(candidate)
            candidates = [e for e in entries if match_key(e.title) == wanted]
            if candidates:
                title = candidate
                break

        # A rom's platform narrows before it disambiguates. Eight entries
        # are published for both 3DS and DS, and those are one entry rather
        # than two, so this cannot split them -- but it does keep a 3DS rom
        # from matching a DS-only entry that happens to share a title.
        system = system_for(rom.platform or "")
        if system and len(candidates) > 1:
            narrowed = [e for e in candidates if system in e.systems]
            if narrowed:
                candidates = narrowed

        if not candidates:
            raise NoMatch(
                f"Universal-DB has no entry titled {title!r}. Matching is "
                f"exact once case and punctuation are ignored, deliberately: "
                f"a close-enough match would attach another author's icon and "
                f"another author's title. If the database spells it "
                f"differently, pass --source-id with its slug."
            )
        if len(candidates) > 1:
            names = ", ".join(sorted(e.slug for e in candidates))
            raise Ambiguous(
                f"{len(candidates)} Universal-DB entries are titled {title!r}: "
                f"{names}. Which one this rom is, is not a choice this plugin "
                f"will make for you -- pass --source-id with the slug you want."
            )
        return candidates[0]

    @staticmethod
    def _slug(rom: RomRef) -> str:
        for key in SLUG_KEYS:
            value = (rom.extra.get(key) or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def _candidate_titles(cls, rom: RomRef) -> list[str]:
        """Spellings of this rom's title to try, best first.

        **`rom.name` is very often a filename**, and that is what made
        the first version of this miss almost everything real. RomM
        derives a rom's name from the file it was uploaded as, so a
        Universal-DB import comes back named `WordleDS.nds` -- which
        matches no title in the database, because the database calls it
        `Wordle DS`. The rom had been imported by this very plugin
        minutes earlier and enriching it still failed.

        So: the library's name as given, then the same with a trailing
        extension removed, then the filename's stem. Each is compared
        *exactly* against the database's own titles, so a wrong guess
        costs a miss rather than another author's cover -- which is what
        makes trying three of them safe rather than sloppy.

        Order-preserving and de-duplicated, because for a rom whose name
        already is its filename all three collapse to two.
        """
        raw = (rom.name or "").strip()
        candidates = [raw, cls._strip_extension(raw), cls._title_from_filename(rom.filename)]
        seen: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.append(candidate)
        return seen

    @staticmethod
    def _strip_extension(name: str) -> str:
        """`WordleDS.nds` -> `WordleDS`; `Mr. Driller` unchanged.

        Only a two-to-five character alphanumeric last segment counts as
        an extension. Both bounds earn their place against real titles: a
        single character keeps `Wolfenstein 3.D` intact, and the
        alphanumeric rule keeps `Mr. Driller` intact because its last
        segment has a space in it. The shortest extension this database
        actually uses is three (`cia`, `nds`, `zip`), so two is already
        slack.
        """
        stem, dot, extension = name.rpartition(".")
        if not dot or not stem:
            return name
        if not (2 <= len(extension) <= 5) or not extension.isalnum():
            return name
        return stem.strip()

    @staticmethod
    def _title_from_filename(filename: str) -> str:
        """A title guessed from a filename is still only used to *match*.

        Whatever comes out of here is compared exactly against the
        database's own title before anything is proposed, so a bad guess
        costs a miss rather than a wrong cover.
        """
        stem = posixpath.basename((filename or "").replace("\\", "/"))
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        return " ".join(stem.replace("_", " ").replace("-", " ").split())
