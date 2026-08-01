"""Reading one IF Archive directory index, and reading it once.

The archive publishes no API. What it publishes is an HTML index per
directory -- `/indexes/if-archive/games/zcode/` is 491,887 bytes and lists
811 files with their dates and the archive's own descriptions -- and that
page is the whole data source. There is a `Master-Index`, but it is
2.2 MB for the short form and 15.1 MB for the XML one, which is a worse
trade for a plugin that only ever looks at four directories.

Two things about that page are load-bearing and neither is obvious.

**The `href` is the filename. The `id` is not.** Each entry opens::

    <dt id="Apollo18=2B=20.zip" class="ParOdd">
      <a href="/if-archive/games/zcode/Apollo18%2B20.zip">Apollo18+20.zip</a>

The `id` is a fragment identifier and is escaped `=2B=`; the `href` is
percent-encoded. They are different encodings of the same name and the
`id` is the misleading one -- read as a filename it says `Apollo18+20`
where the file is `Apollo18+20.zip`, and `The=20=Cruel=20=Count=27=s`
does not round-trip through anything a URL parser knows. So this module
reads the `href`, percent-decodes it once, and never looks at the `id`.

**Percent-decoding is not optional, and it must not be replaced by
narrowing.** 40 of the 1,509 files in the four runtime directories have a
percent-encoded `href`. Decoded, every one of them is a name
`rom_hub.types.bare_filename` accepts -- `Escape!.zblorb`, `Ancient
Treasure, Secret Spider.zblorb`, `Apollo18+20.zip` -- because `!`, `,`,
`+`, `(`, `)` and `'` are all permitted in a ROM filename. A plugin that
"sanitised" by dropping names containing `%` would silently lose all
forty and nobody would ever see a message about it. Decode, then let the
host's own validator have the last word.

The index is parsed once per process and kept. A search that names no
platform reads four directories; a second search in the same process
reads none.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote

#: Where the human-readable indexes live. The tree the plugin may look at
#: at all -- see `archive_path` -- is `if-archive/games/`.
INDEX_BASE = "https://ifarchive.org/indexes/"
#: Where the files themselves live. Note that this host 302s a download to
#: `ukrestrict.ifarchive.org` for three of the four runtime directories;
#: both hosts are declared in manifest.toml because the Hub re-checks
#: every redirect hop against the allowlist.
FILE_BASE = "https://ifarchive.org/"

#: The subtree this plugin will read or plan a fetch from, ever.
GAMES_ROOT = "if-archive/games/"

#: The directories searched when the operator names none. Exactly the four
#: runtimes RomM 4.9.2 has a platform slug for; see formats.py.
DEFAULT_DIRECTORIES = ("zcode", "glulx", "tads", "hugo")

#: A directory name is a path component the plugin puts in a URL. An
#: allowlist of what one may contain, not a denylist of what it may not --
#: the same posture `rom_hub.types.bare_filename` takes, for the same
#: reason. `/` is admitted because the archive nests
#: (`zcode/german`, `zcode/french`), and `..` is refused below.
_DIRECTORY_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\Z")

#: The list this module parses. A document without it is not an index, and
#: is refused rather than read as "zero files" -- the aminet plugin was
#: bitten by a host that answers a missing path with HTTP 200 and an error
#: body, and a parser that returns an empty list for an unrecognised
#: document turns any upstream change into a silent, total failure.
_FILELIST_OPEN = '<dl id="filelist"'

_ENTRY_RE = re.compile(
    r'<dt\b[^>]*>\s*<a\s+href="(?P<href>/if-archive/[^"#?]+)"', re.IGNORECASE
)
_DATE_RE = re.compile(r'<span class="Date">\[([^\]]*)\]</span>')
_DESCRIPTION_RE = re.compile(r"<dd>\s*<p>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


class IndexUnavailable(Exception):
    """A directory index could not be fetched or was not an index."""


@dataclass(frozen=True)
class Entry:
    """One file listed in a directory index."""

    #: The archive path, percent-decoded: `if-archive/games/zcode/905.z5`.
    path: str
    #: The bare filename, percent-decoded: `905.z5`.
    filename: str
    #: The directory it was listed under, as configured: `zcode`.
    directory: str
    #: `[02-Aug-2012]` in the index, or "" when the archive lists none.
    date: str
    #: The archive's own description. Licensed CC BY 4.0 to the Interactive
    #: Fiction Technology Foundation -- see the README.
    description: str

    @property
    def url(self) -> str:
        """The download URL.

        Re-encoded from the decoded path rather than carried through from
        the `href`, so there is exactly one representation of a file in
        this plugin and the one an operator sees in `source_id` is the one
        that gets fetched. Verified round-trip-exact against all 1,509
        hrefs in the four runtime directories.
        """
        return FILE_BASE + quote(self.path, safe="/")

    @property
    def page_url(self) -> str:
        """The index entry an operator can open in a browser."""
        directory, _, name = self.path.rpartition("/")
        return INDEX_BASE + quote(directory, safe="/") + "/#" + quote(name, safe="")


def clean_directory(value: str) -> str:
    """One configured directory name, or a refusal.

    This becomes a URL path component. `..` is refused even though the
    joins below could not act on it, because a value that cannot traverse
    should not be able to look like it might.
    """
    directory = (value or "").strip().strip("/")
    if not directory:
        raise IndexUnavailable("a directory name may not be empty")
    if ".." in directory or not _DIRECTORY_RE.match(directory):
        raise IndexUnavailable(
            f"directory {value!r} is not a name this plugin will request: it "
            f"must be one or more `/`-joined components of letters, digits, "
            f"'.', '-' and '_', and must not contain '..'"
        )
    return directory


def archive_path(value: str) -> str:
    """Validate an archive path and return it decoded, or refuse.

    Accepts either form -- the percent-encoded one from an `href` or the
    decoded one a `source_id` carries -- and always returns the decoded
    one, so a round trip through a search result cannot change what gets
    fetched.

    Confined to `if-archive/games/`. That is not decoration: it is the
    only reason this plugin cannot be talked into planning a fetch for
    something outside the games tree by an operator-supplied
    `--source-id`.
    """
    path = unquote((value or "").strip()).lstrip("/")
    if not path.startswith(GAMES_ROOT):
        raise IndexUnavailable(
            f"{value!r} is not a path under {GAMES_ROOT!r}; this plugin only "
            f"reads and fetches the IF Archive's game directories"
        )
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise IndexUnavailable(
            f"{value!r} is not a usable archive path (empty, '.' or '..' "
            f"component)"
        )
    if any("\\" in part or ":" in part for part in parts):
        raise IndexUnavailable(
            f"{value!r} contains a path separator or drive marker this plugin "
            f"will not put in a URL"
        )
    return path


def parse_index(text: str, directory: str) -> list[Entry]:
    """Every file the index lists, in the order the archive lists them.

    Raises rather than returning `[]` for a document that is not an index.
    An index with a real, empty file list is a different thing and does
    return `[]`; the archive marks it with the same `<dl id="filelist">`.
    """
    start = text.find(_FILELIST_OPEN)
    if start < 0:
        raise IndexUnavailable(
            f"the page for {directory!r} carries no {_FILELIST_OPEN!r}, so it "
            f"is not an IF Archive directory index. Nothing was read from it "
            f"-- reading zero files out of an unrecognised document would look "
            f"exactly like an empty directory"
        )
    open_end = text.find(">", start)
    if open_end < 0:
        raise IndexUnavailable(f"the file list for {directory!r} is truncated")
    end = text.find("</dl>", open_end)
    body = text[open_end + 1 : end if end >= 0 else len(text)]

    marks = list(_ENTRY_RE.finditer(body))
    entries: list[Entry] = []
    for position, match in enumerate(marks):
        stop = marks[position + 1].start() if position + 1 < len(marks) else len(body)
        block = body[match.end() : stop]
        href = html.unescape(match.group("href"))
        if href.endswith("/"):
            # A subdirectory. They live in `subdirlist` rather than here,
            # but a listing that ever mixed them must not become a file.
            continue
        try:
            path = archive_path(href)
        except IndexUnavailable:
            # A link out of the games tree. The archive puts a few in
            # (cross-references to `if-archive/programming/`); they are
            # not this directory's files.
            continue
        filename = path.rpartition("/")[2]
        if not filename:
            continue
        date_match = _DATE_RE.search(block)
        entries.append(
            Entry(
                path=path,
                filename=filename,
                directory=directory,
                date=date_match.group(1).strip() if date_match else "",
                description=_description(block),
            )
        )
    return entries


def _description(block: str) -> str:
    """The archive's own blurb for one entry, as plain text.

    The first `<dd><p>` only. An entry can carry several `<dd>` blocks --
    an IFDB link, an IFWiki link, a `[linked from ...]` symlink note --
    and only the `<p>` one is prose about the game.
    """
    match = _DESCRIPTION_RE.search(block)
    if not match:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", match.group(1)))
    return " ".join(text.split())


class Index:
    """Directory indexes, fetched through `ctx.http` and kept.

    One per capability instance, which is one per plugin subprocess: the
    runner builds a capability object on first use and reuses it for every
    later call. So a `search` that reads all four runtime directories
    costs four requests, and the next `search` in the same process costs
    none.
    """

    def __init__(self, http):
        self._http = http
        self._cache: dict[str, list[Entry]] = {}

    def entries(self, directory: str) -> list[Entry]:
        directory = clean_directory(directory)
        cached = self._cache.get(directory)
        if cached is not None:
            return cached
        url = INDEX_BASE + GAMES_ROOT + quote(directory, safe="/") + "/"
        response = self._http.get(url)
        if response.status_code != 200:
            raise IndexUnavailable(
                f"the IF Archive answered HTTP {response.status_code} for the "
                f"{directory!r} index ({url})"
            )
        entries = parse_index(response.text, directory)
        self._cache[directory] = entries
        return entries

    def find(self, path: str) -> Entry | None:
        """The cached entry for an archive path, without fetching anything.

        Used only to enrich a refusal message. A `plan()` never needs the
        index -- the search result already carries the path -- and this
        returning None is not a reason to go and get one.
        """
        for entries in self._cache.values():
            for entry in entries:
                if entry.path == path:
                    return entry
        return None
