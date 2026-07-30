"""Which of Demozoo's 386,682 productions a ROM library can actually use.

Demozoo indexes the demoscene, and the demoscene is not only software.
`GET /api/v1/production_types/` returns 57 types across three supertypes,
and a great many of them describe things that are not a file you run on a
retro machine: a `Photo` is a jpeg of a party hall, `Tracked Music` is a
`.mod`, `Performance` is something that happened on a stage, `Papermag`
was printed on paper, and `Report` is somebody's write-up of a weekend.

**`Video` is the trap, and it is the reason this is an allowlist.** Its
supertype is `production`, exactly like `Demo` -- so a filter that kept
`supertype == "production"` would keep it, and Demozoo's `download_links`
for a video entry are frequently a YouTube URL. Importing that would file
a link to a web page in a ROM library as though it were a game. There are
7,000-odd of them. So the rule is the intersection of two conditions:
supertype `production` **and** a type name spelled out below.

What is kept is everything the scene distributes as an executable or a
disk image for the machine it names: demos, the whole ladder of sized
intros from `8b intro` to `100K Intro`, cracktros, BBStros, games,
musicdisks, diskmags, packs, slideshows, invitations and the small
disk-based formats around them (`Docsdisk`, `Votedisk`, `Chip Music
Pack`).

What is deliberately left out, beyond the obvious:

* **The whole `graphics` and `music` supertypes.** `Executable Graphics`
  and `Executable Music` genuinely *are* executables -- a `.prg` that
  draws one picture, a `.prg` that plays one tune -- so this is a real
  choice rather than an oversight. They are excluded because a ROM
  library is a shelf of things you play, and filing 40,000 single-image
  and single-track entries next to games would make the shelf useless for
  the thing it is for. Somebody who wants them can say so; the exclusion
  is one set here, not a rule spread through the code.
* **`Tool`.** Development utilities for the platform. Software, not
  content.
* **`BBS Door`.** A program that runs on the BBS host rather than on the
  caller's machine.
* **`Magazine`, `Textmag`, `Papermag`, `Report`, `Performance`.**
  Publications and events. `Diskmag` is in the keep list precisely
  because it is the one of these that ships as a bootable disk.
* **`Code Challenge`.** Sometimes an executable, sometimes a source
  listing, and the type does not say which.

A production carries a *list* of types (`[Demo, Intro]` is ordinary), so
one importable type is enough. It also carries a list of platforms, and
the ones with an empty list -- Demozoo has plenty, usually old records
with a `lost` tag -- can never be placed and are dropped before anything
else is asked about them.
"""

from dataclasses import dataclass

from .platforms import NeedsMapping, require_slug, slug_for

#: Demozoo production-type name -> Demozoo's own id for it, for every type
#: that describes a runnable file. Names verbatim from
#: `GET /api/v1/production_types/` (captured in
#: `tests/fixtures/demozoo/production_types.json`); the ids are here
#: because `productions/?production_type=<id>` is a server-side filter and
#: `?production_type=<name>` is not.
IMPORTABLE_TYPE_IDS: dict[str, int] = {
    "Demo": 1,
    "Intro": 4,
    "Cracktro": 13,
    "BBStro": 41,
    "Game": 33,
    "Musicdisk": 7,
    "Diskmag": 5,
    "Docsdisk": 46,
    "Votedisk": 45,
    "Slideshow": 8,
    "Invitation": 11,
    "Pack": 9,
    "Chip Music Pack": 12,
    # The sized-intro ladder. Note `16b intro` and `32b Intro`: Demozoo
    # is inconsistent about the capital, which is why every comparison
    # below is casefolded.
    "8b intro": 54,
    "16b intro": 55,
    "32b Intro": 15,
    "64b Intro": 16,
    "128b Intro": 18,
    "256b Intro": 19,
    "512b Intro": 20,
    "1K Intro": 21,
    "2K Intro": 37,
    "4K Intro": 3,
    "8K Intro": 43,
    "16K Intro": 35,
    "32K Intro": 22,
    "40k Intro": 10,
    "64K Intro": 2,
    "96K Intro": 50,
    "100K Intro": 39,
}

#: The same set, casefolded, for testing a production's own type names.
IMPORTABLE_TYPES: frozenset[str] = frozenset(
    name.casefold() for name in IMPORTABLE_TYPE_IDS
)


class UnknownType(Exception):
    """A configured production type is not one this plugin imports."""


def type_id_for(name: str) -> int:
    """Demozoo's id for an importable type name, or a refusal naming it.

    Deliberately refuses types this plugin does not import even though
    Demozoo has ids for them: a `production_type` filter that could be set
    to `Video` would quietly turn the search into a list of things the
    importer then refuses one at a time.
    """
    wanted = (name or "").strip().casefold()
    for spelled, type_id in IMPORTABLE_TYPE_IDS.items():
        if spelled.casefold() == wanted:
            return type_id
    raise UnknownType(
        f"production type {name!r} is not one this plugin imports. It "
        f"imports: {', '.join(sorted(IMPORTABLE_TYPE_IDS))}."
    )

#: Types whose exclusion is a decision somebody might otherwise reverse by
#: accident. The value is the sentence an operator gets. Everything not in
#: `IMPORTABLE_TYPES` is excluded whether or not it is named here; these
#: are the ones worth explaining.
EXCLUDED_TYPES: dict[str, str] = {
    "video": (
        "a video recording of a production rather than the production. "
        "Demozoo's download links for these are usually a YouTube page, "
        "which is not a game and must never be filed as one"
    ),
    "tool": "a development utility for the platform rather than content",
    "bbs door": "a program that runs on a BBS host, not on the caller's machine",
    "magazine": "a publication; `Diskmag` is the bootable-disk form and does import",
    "textmag": "a publication; `Diskmag` is the bootable-disk form and does import",
    "papermag": "printed on paper -- there is no file",
    "report": "somebody's write-up of a party",
    "performance": "a live show; there is no file",
    "code challenge": (
        "sometimes an executable and sometimes a source listing, and the "
        "type does not say which"
    ),
}


class NotImportable(Exception):
    """This production is not something a ROM library can hold."""


@dataclass(frozen=True)
class Production:
    """The parts of a Demozoo production record this plugin uses."""

    id: int
    title: str
    author: str
    release_date: str
    platform_names: tuple[str, ...]
    type_names: tuple[str, ...]
    demozoo_url: str

    @property
    def importable_types(self) -> list[str]:
        return [t for t in self.type_names if t.casefold() in IMPORTABLE_TYPES]

    @property
    def mapped_platform(self) -> str | None:
        """The first platform name that maps to a RomM slug, or None."""
        for name in self.platform_names:
            if slug_for(name) is not None:
                return name
        return None


def _names(entries) -> tuple[str, ...]:
    if not isinstance(entries, list):
        return ()
    return tuple(
        e["name"]
        for e in entries
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]
    )


def _author(entries) -> str:
    """`author_nicks[].name`, joined. Demozoo lists co-authors separately
    and an intro credited to three groups is ordinary."""
    if not isinstance(entries, list):
        return ""
    names = [
        e["name"]
        for e in entries
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]
    ]
    return " & ".join(names[:4])


def parse_production(raw) -> Production | None:
    """One production record, or None if it is unusable.

    None rather than an exception: this runs over a page of a hundred
    records and one malformed row must not cost the other ninety-nine.
    """
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    title = raw.get("title")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    return Production(
        id=pid,
        title=title,
        author=_author(raw.get("author_nicks")),
        release_date=(
            raw["release_date"] if isinstance(raw.get("release_date"), str) else ""
        ),
        platform_names=_names(raw.get("platforms")),
        type_names=_names(raw.get("types")),
        demozoo_url=(
            raw["demozoo_url"] if isinstance(raw.get("demozoo_url"), str) else ""
        ),
    )


def is_importable(production: Production) -> bool:
    """True when this is a runnable file for a machine RomM knows."""
    return bool(production.importable_types) and production.mapped_platform is not None


def require_importable(production: Production) -> tuple[str, str]:
    """`(platform name, RomM slug)`, or a refusal saying which rule failed.

    Every message names the production, because these reach an operator as
    the `error` column of a failed job with nothing else around them.
    """
    if not production.type_names:
        raise NotImportable(
            f"Demozoo production {production.id} ({production.title!r}) has no "
            f"type at all, so there is no way to tell whether it is a file "
            f"that runs"
        )
    if not production.importable_types:
        listed = ", ".join(production.type_names)
        for name in production.type_names:
            reason = EXCLUDED_TYPES.get(name.casefold())
            if reason:
                raise NotImportable(
                    f"Demozoo production {production.id} ({production.title!r}) "
                    f"is a {name!r}: {reason}. It is not something a ROM "
                    f"library can hold."
                )
        raise NotImportable(
            f"Demozoo production {production.id} ({production.title!r}) is "
            f"typed {listed} -- none of which is a runnable file for the "
            f"machine it names. This plugin imports demos, intros, cracktros, "
            f"games and the disk-based formats around them; graphics and "
            f"music entries are deliberately out of scope."
        )

    if not production.platform_names:
        raise NotImportable(
            f"Demozoo production {production.id} ({production.title!r}) names "
            f"no platform, so there is no system to file it under. Demozoo "
            f"has many such records; they are usually productions nobody has "
            f"a copy of."
        )
    name = production.mapped_platform
    if name is None:
        # Ask the platform table for its own sentence -- it distinguishes
        # "needs mapping" from "not a library platform" from "ambiguous",
        # and only one of those is fixed by adding a row. Re-raised as
        # `NotImportable` so that every refusal from this function is one
        # type: a caller deciding whether to skip a row should not have to
        # know which table said no. The production is named because these
        # messages reach an operator as a job's `error` column with
        # nothing else around them.
        try:
            require_slug(production.platform_names[0])
        except NeedsMapping as exc:
            raise NotImportable(
                f"Demozoo production {production.id} ({production.title!r}): "
                f"{exc}"
            ) from exc
        raise AssertionError("unreachable: require_slug must have raised")
    return name, require_slug(name)
