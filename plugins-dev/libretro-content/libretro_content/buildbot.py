"""Reading libretro's content buildbot, and what one listing means.

`https://buildbot.libretro.com/assets/cores/<system>/` is the directory
RetroArch's own **Content Downloader** reads: one directory per system,
one file per game, no API and no index file. `.index-extended` -- which
the sibling `libretro-cores` plugin uses for cores -- does **not** exist
here (checked live: HTTP 404), so the listing itself is the catalogue.

The listing is served by h5ai, which renders with JavaScript and ships a
plain `<table>` fallback in the HTML for clients that do not run any.
This module reads the fallback, which is the whole reason this source is
scriptable at all. Each row is one of:

    <td class="fb-i"><img ... alt="file"/></td>
    <td class="fb-n"><a href="/assets/cores/DOS/Doom.zip">Doom.zip</a></td>
    <td class="fb-d">2026-07-30 00:00</td><td class="fb-s">65 KB</td>

`alt` is the discriminator: `folder`, `file`, or `folder-parent` for the
`..` row. Nothing here infers a type from the extension -- `Quake II` is
a directory and `Break An Egg.md` is a Mega Drive ROM, so an extension
test would get both wrong.

**The size column is not a size.** h5ai prints `65 KB`, rounded, with no
byte count anywhere in the document. It is carried as display text in
`extra` and never as `size_bytes`: a rounded number in a field the host
uses to check a download would turn every import into a mismatch.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import quote

BASE = "https://buildbot.libretro.com/assets/cores/"

# One h5ai fallback row. Non-greedy and anchored on the two classes h5ai
# emits, so a change in the icon path or the date format does not break it.
_ROW = re.compile(
    r'alt="(?P<kind>folder|file|folder-parent)"\s*/?>\s*</td>\s*'
    r'<td class="fb-n">\s*<a href="(?P<href>[^"]*)"\s*>(?P<name>[^<]*)</a>'
    r".*?"
    r'<td class="fb-s">(?P<size>[^<]*)</td>',
    re.S,
)


class BuildbotError(Exception):
    """The buildbot listing could not be read."""


@dataclass(frozen=True)
class Item:
    """One row of a listing: a file, or a subdirectory."""

    name: str
    is_dir: bool
    #: h5ai's own rendering of the size ("65 KB", "1.2 MB"), or "".
    #: Rounded, so it is shown and never computed with.
    size_text: str = ""


def parse_listing(text: str) -> list[Item]:
    """Every row of one h5ai directory index.

    Raises rather than returning `[]` when the document is not a listing
    at all. That distinction is the lesson `nointro-archive` paid for:
    Myrient answered HTTP 200 with a shutdown notice for every path, so a
    parser that treated "no rows" and "not a listing" alike could not
    tell a dead source from an empty directory.
    """
    if not isinstance(text, str) or not text:
        raise BuildbotError("the buildbot returned an empty document")
    if 'class="fb-n"' not in text:
        # h5ai emits this class on every row of every listing it renders.
        raise BuildbotError(
            "the buildbot's answer is not a directory listing (no h5ai table "
            "in it). A 200 that is a maintenance page, a login wall or a "
            "redirect body would look exactly like this, and filing one as a "
            "ROM is the failure this check exists to prevent."
        )

    items: list[Item] = []
    for match in _ROW.finditer(text):
        kind = match.group("kind")
        if kind == "folder-parent":
            continue
        name = html.unescape(match.group("name")).strip()
        if not name or name == "Parent Directory":
            continue
        items.append(
            Item(
                name=name,
                is_dir=kind == "folder",
                size_text=html.unescape(match.group("size")).strip(),
            )
        )
    return items


def directory_url(system: str) -> str:
    """Where one system's listing lives.

    `quote` with no safe characters, so a directory whose name contains a
    space (`Sega - Mega Drive - Genesis`) or an apostrophe
    (`Jump 'n Bump`) is encoded once and exactly once.
    """
    return BASE + quote(system, safe="") + "/"


def file_url(system: str, filename: str) -> str:
    """Where one file lives.

    The two components are quoted separately so that a `/` in either --
    which there never legitimately is -- becomes `%2F` rather than a path
    segment the allowlist would then have to reason about.
    """
    return BASE + quote(system, safe="") + "/" + quote(filename, safe="")
