"""Reading a plain HTTP directory index, and remembering what it said.

There is no API here. A mirror answers a directory with whatever its web
server renders -- Apache's `<pre>` block, nginx's fancyindex table, lighttpd's
table, Archive.org's petabox table -- and the only thing all of them agree on
is that every entry is an `<a href>` with a size somewhere to its right. So
that is what this parses, and nothing narrower: a parser tuned to one server's
markup would have to be rewritten the first time `base_url` is repointed, and
repointing is the whole reason `base_url` is config.

The two rules that make a generic parse safe:

**Only same-directory relative links are entries.** Anything absolute, any
`?C=N&O=A` sort link, any `#anchor`, any `../` -- those are chrome, and every
index format has some. Filtering by *shape* rather than by a list of known
chrome strings is what lets one parser handle four servers.

**A duplicate href is chrome too.** Archive.org emits each file twice:
`<a href="Game.7z">Game.7z</a> (<a href="Game.7z/">View Contents</a>)`. The
second differs only by a trailing slash, so deduplicating on the slash-stripped
href drops it without this module having to know the words "View Contents".

Sizes are a display hint and nothing more. The host learns the real length
from the response; a listing that prints `35.9 KiB` where the file is 36,712
bytes must not be able to fail a plan, so a size that will not parse becomes
`None` rather than an error.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

_ANCHOR = re.compile(
    r'<a\s[^>]*?href="([^"]*)"[^>]*>.*?</a>', re.DOTALL | re.IGNORECASE
)
_TAGS = re.compile(r"<[^>]+>")
# "35.9 KiB", "1.0M", "898.0B", "70.2K". First match in the tail wins: a date
# such as "26-Jan-2016 00:38" cannot match, because a digit run there is never
# followed by a size unit.
_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGT]i?B|[KMGT]|B)\b")
_UNITS = {"": 1, "B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
# How much of the markup after an entry may hold its size. Long enough to
# clear Archive.org's "(View Contents)" twin and its date column, which sit
# between a file's name and its size; short enough that the last entry in a
# listing -- whose tail is the rest of the page -- cannot pick up a number
# out of a footer or a timing table.
_TAIL_CHARS = 400

# Archive.org adds these to every item. They are not ROMs, and an operator
# searching for a game should never be offered one.
METADATA_SUFFIXES = (
    "_archive.torrent",
    "_files.xml",
    "_meta.xml",
    "_meta.sqlite",
    "_reviews.xml",
    "_rules.conf",
)

# A directory listing this plugin can use has entries in it. A page with none
# is not an empty directory -- it is a different page, and saying so out loud
# is the difference between "no results" and "this mirror is gone".
#
# THIS IS THE ONLY THING THAT DETECTS A DEAD MIRROR. Status codes cannot do
# it, and myrient.erista.me is the proof: since it shut down on 2026-03-31 it
# answers **HTTP 200** for every path it ever served *and* for paths it never
# served, with the same 2,334-byte shutdown notice each time -- verified by
# fetching both a real deep file URL and a nonsense one and getting byte-
# identical bodies. There is no status code, no header and no length change
# to key off. `response.status_code != 200` above will therefore never fire
# for this failure mode; only "did anything parse as an entry?" will.
#
# Delete this check and the failure becomes silent in both directions: search
# reports "no results" forever without ever saying why, and an importer that
# trusted the same 200 would download the notice, hash it, upload it and
# report DONE with an HTML page filed as a ROM.
MIN_USABLE_ENTRIES = 1


class IndexError_(Exception):
    """A directory index could not be read."""


@dataclass(frozen=True)
class Entry:
    name: str
    href: str
    size_bytes: int | None
    is_dir: bool

    @property
    def is_payload(self) -> bool:
        """A file worth offering: not a directory, not server bookkeeping."""
        return not self.is_dir and not self.name.endswith(METADATA_SUFFIXES)


def parse_size(tail: str) -> int | None:
    """Bytes for the first size printed after an entry, or None.

    Binary units throughout: Myrient printed `KiB`, Archive.org's petabox
    prints `K` for the same thing. Both are 1024-based, and this is a hint
    either way.
    """
    match = _SIZE.search(_TAGS.sub(" ", tail or ""))
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if value < 0:
        return None
    unit = match.group(2).rstrip("iB") or match.group(2)
    return int(value * _UNITS.get(unit if unit in _UNITS else "B", 1))


def _usable(href: str) -> bool:
    if not href or href in ("./", "../", ".", ".."):
        return False
    # Absolute, protocol-relative, scheme-bearing, query or fragment: chrome
    # in every index format, and in this one's case a way out of the
    # directory being listed.
    return not (
        href.startswith(("/", "#", "?"))
        or href.startswith("//")
        or "://" in href
        or "?" in href
        or "#" in href
    )


def _display_name(href: str) -> str:
    """The real file name behind an href, entities and all.

    Unescaping happens **twice**, on either side of the percent-decode,
    because mirrors disagree about the order they encode in and only one
    of the two orders was handled:

        Sonic%20%26%20Tails.zip        percent-encoded `&`
        Sonic%20&amp;%20Tails.zip      HTML-escaped `&`, space encoded
        Sonic%20%26amp%3B%20Tails.zip  HTML-escaped FIRST, then encoded

    The first two are handled by the unescape in `parse_index`, which
    runs on the raw href before it is used as a URL. The third is not:
    `%26amp%3B` contains no `&` for that pass to see, so the entity only
    exists once `unquote` has run, and the title then comes out as
    `Sonic &amp; Tails` verbatim -- in search results, in the source id an
    operator pastes into `rom-hub import`, and in the file written to
    disk (`&` survives `safe_filename`, `;` becomes `_`, so the ROM lands
    as `Sonic &amp_ Tails.zip`).

    **Measured, so the scope is not overstated:** as of 2026-07-29 every
    directory this plugin ships pointed at -- all twelve `nointro.*`
    items on Archive.org -- emits the *first* form, which already worked.
    None currently emits the third. So this closes a gap rather than
    fixing a title that is wrong today, and it closes it in the direction
    that matters: `base_url` is configuration, the whole point of the
    generic parser is that it survives being repointed at another
    mirror, and an encoding order that is one server away should not
    reach an operator as `&amp;` in a filename.

    The href is deliberately *not* re-derived from this. It stays exactly
    what the server said, because that is the string that fetches the
    file; this is the name a human reads and the id the importer matches.

    The cost, stated: a file genuinely named `Sonic &amp; Tails.zip` --
    with those five characters in it on purpose -- would be titled
    `Sonic & Tails.zip`. That is the wrong answer to a case nobody has
    ever seen, in exchange for the right answer to one that is one mirror
    away.
    """
    return html.unescape(unquote(href))


def parse_index(document: str) -> list[Entry]:
    """Every same-directory entry in a directory index, in listing order."""
    entries: list[Entry] = []
    seen: set[str] = set()
    for match in _ANCHOR.finditer(document or ""):
        href = html.unescape(match.group(1)).strip()
        if not _usable(href):
            continue
        key = href.rstrip("/")
        if key in seen:
            # Archive.org's "(View Contents)" twin. See module docstring.
            continue
        seen.add(key)
        name = _display_name(key)
        if "/" in name or "\\" in name:
            # A nested path is not an entry of *this* directory. Walking
            # into subdirectories is a deliberate non-feature: see README.
            continue
        entries.append(
            Entry(
                name=name,
                href=href,
                size_bytes=parse_size(document[match.end() : match.end() + _TAIL_CHARS]),
                is_dir=href.endswith("/"),
            )
        )
    return entries


class IndexCache:
    """Directory indexes already fetched in this process.

    A No-Intro platform directory is hundreds of kilobytes of HTML and
    changes about as often as the set is rebuilt, so re-fetching one per
    query would be rude to the mirror and slow for the operator. Bounded so
    a long-lived host cannot accumulate every directory it has ever seen;
    eviction is oldest-first, which for this access pattern is the same as
    least-recently-used because an index is parsed once and then read from
    the dict.
    """

    def __init__(self, max_indexes: int = 32):
        self.max_indexes = max_indexes
        self._entries: dict[str, list[Entry]] = {}
        self.fetches = 0

    def get(self, http, url: str) -> list[Entry]:
        cached = self._entries.get(url)
        if cached is not None:
            return cached
        entries = self._fetch(http, url)
        if len(self._entries) >= self.max_indexes:
            self._entries.pop(next(iter(self._entries)))
        self._entries[url] = entries
        return entries

    def _fetch(self, http, url: str) -> list[Entry]:
        self.fetches += 1
        try:
            response = http.get(url)
        except RuntimeError as exc:
            # What the broker reports through the SDK channel: a blocked
            # host, or a body over the host's buffering budget.
            raise IndexError_(f"cannot read the index at {url!r}: {exc}") from exc
        if response.status_code != 200:
            raise IndexError_(
                f"mirror returned HTTP {response.status_code} for the directory "
                f"index {url!r}"
            )
        entries = parse_index(response.text)
        if len(entries) < MIN_USABLE_ENTRIES:
            raise IndexError_(
                f"{url!r} answered {response.status_code} but is not a directory "
                f"index: no entries could be parsed from it. A mirror that has "
                f"been retired often keeps answering 200 with a static notice "
                f"for every path, which is indistinguishable from an empty "
                f"directory unless it is checked for."
            )
        return entries

    def clear(self) -> None:
        self._entries.clear()


# One cache per plugin process, shared by `search` and `importer`: the runner
# loads both capabilities into the same interpreter, and an import that
# follows a search should not pay for the index a second time.
INDEXES = IndexCache()
