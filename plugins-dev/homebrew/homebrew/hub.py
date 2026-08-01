"""The Homebrew Hub's search API, and what one entry means.

`https://hh3.gbdev.io/api/search` takes `q`, `platform`, `typetag`, `page`,
`sort` and `order`, and answers

    {"results": 1571, "page_total": 158, "page_current": 1,
     "page_elements": 10, "entries": [...]}

Ten entries per page, and the page size is fixed -- `limit`, `per_page`,
`elements` and `page_elements` were all tried against the live API and all
ignored. That is why `max_pages` exists and why the walk stops early: the
only way to ask for more is to ask again.

An entry is a record from the gbdev/nesdev community databases, so its
fields are as complete as whoever submitted it made them. Live data
contains entries with `title: null` and entries with no `platform` key at
all, which is why nothing here treats a field as guaranteed.
"""

from dataclasses import dataclass, field

API = "https://hh3.gbdev.io/api/search"
STATIC = "https://hh3.gbdev.io/static/"

#: The human-facing site, and where an entry plays.
#:
#: **It is `/game/<slug>`, not `/g/<slug>`.** The old spelling was wrong and
#: every search result carried it. The right one is not a guess either: the
#: site is `gbdev/virens`, a Nuxt app whose file-based routing puts the page
#: at `pages/game/[slug].vue`, and whose own sitemap module builds
#: `hostname + "/game/" + slug` with `hostname: "https://hh.gbdev.io"` in
#: `nuxt.config.ts`. Read from the frontend's published source rather than
#: by fetching the site, because `hh.gbdev.io/robots.txt` disallows
#: ClaudeBot outright and working around that was not on the table.
SITE = "https://hh.gbdev.io/game/"

PAGE_ELEMENTS = 10


class HubError(Exception):
    """The Homebrew Hub could not be read."""


@dataclass
class HubFile:
    filename: str
    is_default: bool = False
    playable: bool = False


@dataclass
class Entry:
    slug: str
    title: str
    platform: str | None
    basepath: str
    developer: str = ""
    typetag: str = ""
    files: list[HubFile] = field(default_factory=list)
    #: Image names relative to the entry's static directory. The Hub's
    #: own convention puts a `cover.*` first when the submitter provided
    #: one; the rest are in-game shots.
    screenshots: list[str] = field(default_factory=list)
    #: The submitter's own licence string ("CC-BY-NC-ND-4.0", "Zlib").
    #: Empty when the record does not state one -- which is a fact about
    #: the entry, not a licence.
    license: str = ""
    #: The Hub's own category tags ("Puzzle", "Open Source", "RPG").
    tags: tuple[str, ...] = ()
    #: Release date as the record spells it, or "".
    date: str = ""

    @property
    def site_url(self) -> str:
        return SITE + self.slug

    @property
    def playable_file(self) -> HubFile | None:
        """The file the Hub's own player would load, or None.

        The gate for `stream`, and it is the frontend's rule rather than
        one invented here: `pages/game/[slug].vue` walks `game.files` and
        points its emulator at the last one flagged `playable`. An entry
        with no such file renders a details page with no player on it, so
        offering it as a stream target would be advertising help that
        never arrives.

        1,565 of the Hub's 1,571 entries have one, counted over the whole
        catalogue on 2026-08-01. The six that do not are the reason this
        returns None instead of falling back to `payload()`.
        """
        playable = [f for f in self.files if f.playable]
        return playable[-1] if playable else None

    def cover(self) -> str | None:
        """The submitter's cover image, or None when there is not one.

        **Only a file actually named `cover.*` counts.** Roughly half the
        Hub's entries have one and the rest carry in-game screenshots
        instead; promoting a screenshot to box art would fill a library
        with pictures of gameplay that an operator then has to undo one
        at a time. `MetadataPatch` treats an absent field as "leave it
        alone" precisely so this can return None.
        """
        for name in self.screenshots:
            stem = name.rsplit("/", 1)[-1].lower()
            if stem.startswith("cover."):
                return name
        return None

    def static_url(self, name: str) -> str:
        """Where the Hub serves one of this entry's files.

        The same join `download_url` performs, for the same reason: a
        screenshot name is sometimes a path within the entry
        (`screenshots/uJacb3.png`) and sometimes bare.
        """
        return f"{STATIC}{self.basepath}/entries/{self.slug}/{name}"

    def payload(self) -> HubFile | None:
        """The file to import: the Hub's own default, else the first one.

        `default: true` is the submitter saying "this is the ROM"; the rest
        of the list is alternate revisions, source drops and translations.
        Falling back to the first entry rather than to nothing keeps older
        records -- which predate the flag -- importable, and the order the
        API returns is stable, so the choice does not wander between calls.
        """
        if not self.files:
            return None
        return next((f for f in self.files if f.is_default), self.files[0])

    def download_url(self, payload: HubFile) -> str:
        """Where the Hub serves that file.

        `filename` is sometimes a path within the entry (`files/Game.nes`)
        and sometimes a bare name, so it is joined, not basenamed -- the
        path is part of where the file lives. Only `FetchFile.filename`
        needs to be bare, and that is the sanitiser's job.
        """
        return f"{STATIC}{self.basepath}/entries/{self.slug}/{payload.filename}"


def parse_entry(raw: dict) -> Entry | None:
    """One API entry, or None if it is unusable.

    Unusable means no slug, no title, or no basepath: without any of those
    there is nothing to show and nothing to fetch. Live data has entries
    with `title: null`, so this is a real path, not defensive decoration.
    """
    if not isinstance(raw, dict):
        return None
    slug = raw.get("slug")
    title = raw.get("title")
    basepath = raw.get("basepath")
    if not isinstance(slug, str) or not slug:
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(basepath, str) or not basepath:
        return None

    files = []
    for item in raw.get("files") or []:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            continue
        files.append(
            HubFile(
                filename=filename.strip(),
                is_default=bool(item.get("default")),
                playable=bool(item.get("playable")),
            )
        )

    screenshots = [
        item.strip()
        for item in (raw.get("screenshots") or [])
        if isinstance(item, str) and item.strip()
    ]

    platform = raw.get("platform")
    return Entry(
        slug=slug,
        title=title.strip(),
        # Absent and null both mean "the record does not say", and both have
        # to reach the importer as a refusal rather than as a guess.
        platform=platform.strip() if isinstance(platform, str) and platform.strip() else None,
        basepath=basepath,
        developer=str(raw.get("developer") or ""),
        typetag=str(raw.get("typetag") or ""),
        files=files,
        screenshots=screenshots,
        license=str(raw.get("license") or ""),
        tags=tuple(
            t.strip()
            for t in (raw.get("tags") or [])
            if isinstance(t, str) and t.strip()
        ),
        date=str(raw.get("date") or ""),
    )


def parse_page(payload) -> tuple[list[Entry], int]:
    """(entries, page_total) for one API response."""
    if not isinstance(payload, dict):
        raise HubError("the Homebrew Hub returned something that is not an object")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise HubError("the Homebrew Hub returned no 'entries' list")
    entries = [e for e in (parse_entry(r) for r in raw_entries) if e is not None]
    try:
        page_total = int(payload.get("page_total", 1))
    except (TypeError, ValueError):
        page_total = 1
    return entries, max(page_total, 1)
