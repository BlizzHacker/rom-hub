"""Universal-DB's published catalogue, and what one entry means.

`https://db.universal-team.net/data/full.json` is the whole database in one
response: a JSON **list** of 400 entries, 1.66 MB, HTTP 200 with no
redirect. There is no smaller or paginated form -- `data/3ds.json`,
`data/ds.json` and `data/index.json` all 404 with an HTML body -- so a
query is answered by fetching the list and filtering it here. That is the
opposite of the Homebrew Hub plugin, which pushes the query to a server,
and it is a deliberate consequence of the source: this one publishes a
file, not a search API.

The 404-with-HTML shape is why every read checks `status_code` first. A
parser that only tried `json.loads` would report "not JSON" for what is
really "that path does not exist".

An entry is a record from an open, reviewable repository
(`Universal-Team/db`, GPL-3.0), so its fields are as complete as whoever
submitted it made them. Of the 400 live entries, 130 carry no `license`
key, 17 carry no downloads at all, and one spells its author key `Author`
rather than `author` -- so nothing here treats a field as guaranteed.

**Licence is surfaced, never invented.** It is the reason this source is
usable at all, so it belongs in front of the operator. An entry that does
not state one reads `unstated`, which is a fact about the database and not
a synonym for "public domain".
"""

import json
from dataclasses import dataclass, field

FULL_JSON = "https://db.universal-team.net/data/full.json"
SITE = "https://db.universal-team.net/"

#: What the database calls a thing that is not an installable title.
#:
#: * `plugin` -- Luma3DS `.3gx` plugins, which are injected into a running
#:   commercial game and never boot on their own.
#: * `firm` -- boot-chain firmware (`boot.firm`). Flashing one is a custom
#:   firmware install step, not adding a title to a library.
#: * `exploit` -- an entry point into another game's save data.
#:
#: Fifteen entries carry one of these; fourteen are removed, because
#: `nexus3ds` is tagged both `utility` and `firm` and ships only a
#: `boot.firm`. Everything else the database publishes -- games, apps,
#: utilities, emulators, multimedia and save tools -- boots on the console
#: as a title and stays.
NOT_TITLES: frozenset[str] = frozenset({"plugin", "firm", "exploit"})

#: What an entry with no `license` key says instead. Not a licence.
UNSTATED = "unstated"


class DatabaseError(Exception):
    """Universal-DB could not be read."""


@dataclass(frozen=True)
class Download:
    """One release asset the database names for an entry.

    `name` is the database's own key for the asset and is what the file is
    called, which is not always the last path segment of `url`: the DS
    MPEG4 player is keyed `MPEG4Player.nds.zip` and served from a GBAtemp
    attachment path with no filename in it at all. The key is the
    statement about what the file is; the URL is only where it lives.
    """

    name: str
    url: str
    size_bytes: int | None = None

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""


@dataclass(frozen=True)
class Entry:
    slug: str
    title: str
    #: Universal-DB system tokens, e.g. `["3DS"]` or `["3DS", "DS"]`.
    systems: tuple[str, ...]
    categories: tuple[str, ...] = ()
    author: str = ""
    description: str = ""
    version: str = ""
    updated: str = ""
    #: The database's SPDX-ish licence id, or None when it states none.
    license_id: str | None = None
    #: The database's human-readable licence name, or None.
    license_name: str | None = None
    downloads: tuple[Download, ...] = ()
    #: `{archive-name-pattern: {label: [paths inside]}}`. Universal-DB's
    #: own manifest of what is inside a release archive -- see
    #: `payload.py`, which is the only thing that reads it.
    archive: dict = field(default_factory=dict)
    #: The application's own 48x48 icon, as the database records it. 364
    #: of 400 entries have one; it is the closest thing this catalogue has
    #: to box art, because it is the picture the console itself shows.
    icon: str = ""
    #: The 2D banner, 400 x 120-ish. 399 of 400 entries have one. Second
    #: choice rather than first: 26 of them are the author's GitHub
    #: avatar, which is a picture of an organisation and not of a title.
    image: str = ""

    @property
    def url(self) -> str:
        """The entry's page on the database's own site.

        Shown to a person; never fetched. The path uses the first system
        the entry names, which is how the site itself routes.
        """
        section = (self.systems[0] if self.systems else "3DS").lower()
        return f"{SITE}{section}/{self.slug}"

    @property
    def license_label(self) -> str:
        """What to show a human about this entry's licence.

        The database's own human name when it has one, its id when it has
        only that, and `unstated` when it has neither. Never a guess: 130
        of 400 entries say nothing about licensing, and turning that
        silence into a licence would be the single most damaging thing
        this plugin could do, because the whole claim that this source is
        redistributable rests on the terms being the authors' own.
        """
        if isinstance(self.license_name, str) and self.license_name.strip():
            return self.license_name.strip()
        if isinstance(self.license_id, str) and self.license_id.strip():
            return self.license_id.strip()
        return UNSTATED

    @property
    def license_stated(self) -> bool:
        return self.license_label != UNSTATED

    def is_title(self) -> bool:
        """Whether this is the kind of thing a ROM library holds."""
        return not (set(self.categories) & NOT_TITLES)

    def platforms(self) -> list[str]:
        """The RomM slugs this entry is published for, in database order."""
        from .platforms import platform_for

        out = []
        for system in self.systems:
            slug = platform_for(system)
            if slug and slug not in out:
                out.append(slug)
        return out

    def unmapped_systems(self) -> list[str]:
        """System tokens this plugin has no RomM slug for."""
        from .platforms import platform_for

        return [s for s in self.systems if platform_for(s) is None]


def _text(value) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _downloads(raw) -> tuple[Download, ...]:
    """The entry's stable release assets.

    Only `downloads`. `nightly` and `prerelease` carry the same shape and
    are deliberately not read: they are untagged builds that move under
    the same URL, so a library row importing one would silently stop
    describing the bytes on disk. They are also the only place three more
    download hosts appear, so ignoring them keeps the manifest allowlist
    honest as well as the library.
    """
    if not isinstance(raw, dict):
        return ()
    out = []
    for name, meta in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(meta, dict):
            continue
        url = meta.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        size = meta.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            size = None
        out.append(Download(name=name.strip(), url=url.strip(), size_bytes=size))
    # Sorted by name so a plan built from this list is the same plan every
    # time, whatever order the JSON object happened to arrive in.
    return tuple(sorted(out, key=lambda d: d.name))


def parse_entry(raw: dict) -> Entry | None:
    """One database record, or None if it is unusable.

    Unusable means no slug, no title, or no systems: without any of those
    there is nothing to show and nowhere to file it.
    """
    if not isinstance(raw, dict):
        return None
    slug = _text(raw.get("slug"))
    title = _text(raw.get("title"))
    if not slug or not title:
        return None

    systems = tuple(
        s.strip()
        for s in (raw.get("systems") or [])
        if isinstance(s, str) and s.strip()
    )
    if not systems:
        return None

    categories = tuple(
        c.strip().lower()
        for c in (raw.get("categories") or [])
        if isinstance(c, str) and c.strip()
    )

    licence = raw.get("license")
    licence_name = raw.get("license_name")

    return Entry(
        slug=slug,
        title=title,
        systems=systems,
        categories=categories,
        # One live record spells this `Author`. Reading both costs a line
        # and is the difference between crediting somebody and not.
        author=_text(raw.get("author")) or _text(raw.get("Author")),
        description=_text(raw.get("description")),
        version=_text(raw.get("version")),
        updated=_text(raw.get("updated")),
        # Absent and null both mean "the database does not say", and both
        # have to reach the operator as `unstated` rather than as nothing.
        license_id=licence.strip() if isinstance(licence, str) and licence.strip() else None,
        license_name=(
            licence_name.strip()
            if isinstance(licence_name, str) and licence_name.strip()
            else None
        ),
        downloads=_downloads(raw.get("downloads")),
        archive=raw.get("archive") if isinstance(raw.get("archive"), dict) else {},
        icon=_text(raw.get("icon")),
        image=_text(raw.get("image")),
    )


def parse_full(payload) -> list[Entry]:
    """Every usable entry in one `full.json` body."""
    if not isinstance(payload, list):
        raise DatabaseError(
            "Universal-DB's full.json was not a list of entries"
        )
    return [e for e in (parse_entry(r) for r in payload) if e is not None]


#: The parsed database, kept for the life of the plugin process.
#:
#: `full.json` is 1.66 MB and three capabilities read it -- `search`,
#: `importer` and `metadata` -- so without this, enriching a rom the
#: operator just searched for would pull the whole catalogue a second
#: time. It changes about as often as somebody submits homebrew, and a
#: plugin process lives for one command.
_CACHE: list[Entry] | None = None


def clear_cache() -> None:
    """Forget the cached database. For tests, and for nothing else."""
    global _CACHE
    _CACHE = None


def fetch_entries(http) -> list[Entry]:
    """Read the whole database through `ctx.http`, once per process.

    One request, because the site publishes one file. 1.66 MB sits well
    inside the broker's 4 MiB response cap, but the cap is the reason this
    plugin will never grow a "fetch every entry's page too" habit.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    _CACHE = _fetch(http)
    return _CACHE


def _fetch(http) -> list[Entry]:
    response = http.get(FULL_JSON)
    if response.status_code != 200:
        # The site answers a missing path with 404 and an HTML body, so
        # the status is checked before the parse: "not JSON" would be a
        # misleading way to say "that path is gone".
        raise DatabaseError(
            f"Universal-DB returned HTTP {response.status_code} for "
            f"{FULL_JSON!r}"
        )
    try:
        payload = json.loads(response.text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DatabaseError(
            f"Universal-DB's full.json was not JSON: {exc}"
        ) from exc
    return parse_full(payload)
