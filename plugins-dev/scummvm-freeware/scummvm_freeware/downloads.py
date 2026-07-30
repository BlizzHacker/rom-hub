"""Reading `downloads.scummvm.org`, and deciding what counts as a game.

The host is a stock Apache `mod_autoindex` listing -- no API, no JSON, no
JavaScript. One row per file:

    <a href="soltys-en-v1.0.zip">soltys-en-v1.0.zip</a>  2011-11-14 23:43  3.3M

Two things about it are load-bearing.

**The listing is read, not assumed.** The plugin could hold a table of
filenames as well as directories, and then every ScummVM re-release would
silently stop importing. Reading the directory means `drascula-int-1.1.zip`
appearing beside `drascula-int-1.0.zip` is picked up with no code change,
while the *directory* allowlist in `games.py` still decides what may be
looked at at all.

**Not everything in a game's directory is the game.** Three kinds of file
sit beside it and none of them belongs in a ROM library:

* `*.sha256` -- 64-byte checksum sidecars, one per archive.
* manuals -- `dreamweb-manuals-en-highres.zip`,
  `nippon-manual-addons-1.0.zip`. Documentation, and a library that files
  a manual as a game now has a game that will not start.
* language and audio add-ons that are not standalone -- `lang_he.b25c`,
  the `drascula-audio-*` packs. These are *kept*, because unlike a manual
  they are part of playing the game and an operator asking for the FLAC
  audio pack knows what they asked for; they are simply not special-cased.

The size column is Apache's rounded `3.3M`, so it is display text and
never `size_bytes` -- the same rule the `libretro-content` plugin applies
to h5ai's, and for the same reason.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import quote

BASE = "https://downloads.scummvm.org/frs/extras/"

# One mod_autoindex row. The date and size columns are plain text between
# the anchor and the end of the line.
_ROW = re.compile(
    r'<a href="(?P<href>[^"/?][^"]*)">(?P<name>[^<]*)</a>'
    r"\s*(?P<rest>[^\r\n<]*)",
)

# Files that are never the game.
_SIDECAR_SUFFIXES = (".sha256", ".md5", ".sig", ".asc")
_MANUAL_MARKERS = ("manual", "manuals")


class DownloadsError(Exception):
    """The ScummVM download listing could not be read."""


@dataclass(frozen=True)
class Download:
    filename: str
    #: Apache's rounded rendering ("3.3M", "64"), or "". Shown, never
    #: computed with.
    size_text: str = ""
    date_text: str = ""


def is_payload(filename: str) -> bool:
    """True when this file is something an operator would want imported.

    Excludes checksum sidecars and manuals by name. Deliberately *not* an
    extension allowlist: the game files here are `.zip` today but
    `lang_he.b25c` is a real Broken Sword 2.5 data file, and an allowlist
    would have quietly dropped it.
    """
    name = (filename or "").strip().lower()
    if not name:
        return False
    if name.endswith(_SIDECAR_SUFFIXES):
        return False
    stem = name.rsplit(".", 1)[0]
    return not any(marker in stem for marker in _MANUAL_MARKERS)


def parse_listing(text: str) -> list[Download]:
    """Every file row of one Apache index.

    Raises rather than returning `[]` for a document that is not an index.
    A 404 body, a maintenance page and an empty directory are three very
    different things, and only one of them means "this game has no files".
    """
    if not isinstance(text, str) or not text:
        raise DownloadsError("the ScummVM download server returned an empty document")
    if "Index of /frs/extras" not in text:
        raise DownloadsError(
            "the ScummVM download server's answer is not a /frs/extras "
            "directory index. A 404 body or a maintenance page would answer "
            "with rows this parser could otherwise read past, and filing one "
            "as a game is the failure this check exists to prevent."
        )

    downloads: list[Download] = []
    for match in _ROW.finditer(text):
        href = match.group("href")
        # Sort links (`?C=N;O=D`), the parent link (`/frs/extras/`) and
        # subdirectories (trailing `/`) are all navigation, not files.
        if href.startswith(("?", "/")) or href.endswith("/"):
            continue
        name = html.unescape(match.group("name")).strip()
        if not name or name == "Parent Directory":
            continue
        # Apache pads the columns with spaces: "2011-11-14 23:43  3.3M".
        columns = match.group("rest").split()
        date_text = " ".join(columns[:2]) if len(columns) >= 2 else ""
        size_text = columns[2] if len(columns) >= 3 else ""
        downloads.append(
            Download(filename=name, size_text=size_text, date_text=date_text)
        )
    return downloads


def directory_url(directory: str) -> str:
    """Where one game's files are listed."""
    return BASE + quote(directory, safe="") + "/"


def file_url(directory: str, filename: str) -> str:
    """Where one file lives.

    Each component is quoted separately, so a `/` in either becomes `%2F`
    instead of a path segment the allowlist would have to reason about.
    """
    return BASE + quote(directory, safe="") + "/" + quote(filename, safe="")
