"""Parsing itch.io's browse listings.

itch.io's browse pages answer `?format=json` with
`{"page": 1, "num_items": 36, "content": "<html fragment>"}` -- the JSON is
a transport for the same HTML the page would have rendered, so the game
data still has to come out of markup. That is the whole reason this module
exists.

Two things about that markup drive the shape here:

**Attribute order is not stable.** The same `<a>` is emitted as
`<a data-label=... class="title game_link" href=...>` on one listing and
`<a class="thumb_link game_link" href=... data-label=...>` on the next.
Anything that matched a fixed attribute sequence would work in a fixture
and rot in production, so each field is located by its *class* and then the
attribute is pulled out of the tag independently.

**Cells are flat, not nested.** `<div data-game_id=...>` is the only
reliable boundary, so the fragment is split on it and each chunk parsed on
its own. A malformed chunk costs one result, never the response -- the same
posture `archive_org.search` takes towards a malformed doc.
"""

import html
import re
from dataclasses import dataclass, field

BROWSE = "https://itch.io/games/free"

# A browse facet: "tag-gameboy", "genre-puzzle", "made-with-gb-studio".
# Config-supplied, so it is validated rather than pasted into a URL -- a
# value containing "/" or ".." would address a different endpoint entirely,
# and /search is disallowed by itch.io's robots.txt.
FACET_RE = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")

_CELL_SPLIT = re.compile(r'<div\s+data-game_id="(\d+)"')
_HREF = re.compile(r'href="([^"]*)"')
_TITLE_BLOCK = re.compile(
    r'<div class="game_title">(.*?)</div>', re.DOTALL
)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.DOTALL)
_TEXT_BLOCK = re.compile(r'<div[^>]*class="game_text"[^>]*>(.*?)</div>', re.DOTALL)
_AUTHOR_BLOCK = re.compile(r'<div class="game_author">(.*?)</div>', re.DOTALL)
_GENRE_BLOCK = re.compile(r'<div class="game_genre">(.*?)</div>', re.DOTALL)
_PLATFORM_BLOCK = re.compile(r'<div class="game_platform">(.*?)</div>', re.DOTALL)
_ICON_TITLE = re.compile(r'title="([^"]*)"')
_WEB_FLAG = re.compile(r'class="web_flag"')
_TAGS = re.compile(r"<[^>]+>")

# https://<developer>.itch.io/<game>. Games on a developer's own custom
# domain are deliberately not matched: this plugin's manifest allows
# itch.io and *.itch.io only, so a result it could never fetch is worse
# than no result -- it would fail at import with a policy violation
# instead of at search with nothing.
_GAME_URL = re.compile(
    r"\Ahttps://(?P<developer>[A-Za-z0-9][A-Za-z0-9-]*)\.itch\.io/(?P<game>[^/?#]+)"
)


class BrowseError(Exception):
    """The browse listing could not be used."""


@dataclass
class GameCell:
    source_id: str
    title: str
    url: str
    author: str = ""
    description: str = ""
    genre: str = ""
    game_id: str = ""
    # Raw itch.io platform labels, before any mapping. Mapping happens in
    # platforms.py; keeping the raw strings means a refusal can name the
    # exact value itch.io used.
    platform_labels: list[str] = field(default_factory=list)

    @property
    def haystack(self) -> str:
        return " ".join(
            [self.title, self.description, self.author, self.genre, self.source_id]
        ).lower()


def browse_url(filters: list[str]) -> str:
    """The browse URL for `filters`, always under the free-games scope.

    `/games/free` is not optional and is not taken from config: this plugin
    is scoped to free games, and a config key able to drop that scoping
    would make the scope a suggestion.
    """
    segments = []
    for raw in filters or []:
        facet = str(raw).strip().strip("/")
        if not FACET_RE.match(facet):
            raise BrowseError(
                f"browse filter {raw!r} is not a valid itch.io facet: expected "
                f"something like 'tag-gameboy' or 'genre-puzzle' (lowercase "
                f"letters, digits and hyphens)"
            )
        segments.append(facet)
    return "/".join([BROWSE, *segments])


def _text(fragment: str) -> str:
    return html.unescape(_TAGS.sub("", fragment)).strip()


def _anchor(block: str) -> tuple[str, str]:
    """(href, text) of the first anchor in `block`, or ("", "")."""
    match = _ANCHOR.search(block)
    if not match:
        return "", ""
    href = _HREF.search(match.group(1))
    return (html.unescape(href.group(1)) if href else ""), _text(match.group(2))


def _platform_labels(chunk: str) -> list[str]:
    block = _PLATFORM_BLOCK.search(chunk)
    if not block:
        return []
    body = block.group(1)
    labels = [html.unescape(t) for t in _ICON_TITLE.findall(body)]
    if _WEB_FLAG.search(body):
        # The browser build carries no tooltip, only a styled span, so it
        # is normalised to the same vocabulary as the download icons.
        labels.append("Web")
    return labels


def parse_cells(fragment: str) -> list[GameCell]:
    """Every usable game cell in a browse fragment, in listing order."""
    cells: list[GameCell] = []
    parts = _CELL_SPLIT.split(fragment or "")
    # split() on a capturing pattern yields [prefix, id1, body1, id2, body2...]
    for game_id, chunk in zip(parts[1::2], parts[2::2]):
        title_block = _TITLE_BLOCK.search(chunk)
        if not title_block:
            continue
        url, title = _anchor(title_block.group(1))
        match = _GAME_URL.match(url)
        if not match or not title:
            continue
        cells.append(
            GameCell(
                source_id=f"{match.group('developer')}/{match.group('game')}",
                title=title,
                url=f"https://{match.group('developer')}.itch.io/{match.group('game')}",
                author=_anchor(m.group(1))[1]
                if (m := _AUTHOR_BLOCK.search(chunk))
                else "",
                description=_text(t.group(1))
                if (t := _TEXT_BLOCK.search(chunk))
                else "",
                genre=_text(g.group(1)) if (g := _GENRE_BLOCK.search(chunk)) else "",
                game_id=game_id,
                platform_labels=_platform_labels(chunk),
            )
        )
    return cells
