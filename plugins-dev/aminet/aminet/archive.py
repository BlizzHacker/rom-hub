"""Reading Aminet: its search results, its shelf listings, and one `.readme`.

Aminet answers two different pages with **the same markup**, and that is
the fact this module is built on:

    /search?query=<terms>   a server-side search across all 85,453 packages
    /<tree>/<shelf>         one shelf's own listing -- `/game/think`

Both render the result table below, both carry the `Found N matching
packages` count line, and both paginate with `page=N` at 50 rows a page.
So one parser serves both, and the plugin gets a *browse* it did not have.

**`dir=` is not a filter. It is ignored.** This is the correction that
made the second endpoint necessary. `?query=tetris` and
`?query=tetris&dir=game` return the identical 134 packages, and so do
`&dir=demo`, `&dir=mods` and `&dir=zzz` -- verified live 2026-08-01, four
spellings, one answer. Aminet's search form emits exactly one field
(`<input name="query">`); there is no directory parameter to send, and
`dir` was an invented one that HTTP 200 made look like it worked. Every
row of `comm/dlg` in a `dir=game` search was the evidence, and it was
being thrown away client-side rather than read as a symptom.

Scoping therefore happens one of two ways, and the honest one is named at
each call site: **browse a shelf** (server-side, exact, pageable) or
**search everything and filter here** (client-side, and it costs rows out
of every page it drops).

A row is a table row and every column is load-bearing:

    <tr class="lightrow pkg_row">
      <td class="name_col"><a href="/game/think/abrick.lha">abrick.lha</a>
      <td>1.12                       version
      <td><a href="/game/think">game/think</a>
      <td>10352                      downloads
      <td class="size_col">2.0M      size, rounded by Aminet
      <td>2013-10-08                 date
      <td><img src="/pics/ppc-amigaos.png" alt="ppc-amigaos icon">
      <td><a href="/package/...">Tetris clone.</a>

**The architecture icon is the platform**, and it is why this plugin can
answer at all without a second request per row. Aminet publishes for
AmigaOS/68k, AmigaOS 4, MorphOS, AROS and Amithlon out of one tree, and
nothing else in a row distinguishes them -- `abrick.lha` and
`abrick-ix48.lha` sit in the same directory and target different
computers. See `platforms.py`.

Two rendering details that a stricter parser would trip on:

* the light and dark rows are **not** the same markup --
  `<tr class="lightrow pkg_row">` against
  `<tr class="darkrow pkg_row" bgcolor="#e0e0e0">` -- so rows are found by
  the `name_col` cell rather than by the opening tag. A regex anchored on
  `pkg_row">` finds exactly half of them, which is the kind of bug that
  looks like a thin source rather than a broken parser;
* the page is `iso-8859-1`, declared in a meta tag. `ctx.http` hands the
  plugin `str`, so decoding is already the host's problem by the time
  this module runs, but a fixture read from disk has to say so.

The `.readme` beside every package is plain text with a small RFC-822-ish
header. It is what the importer reads: `Architecture:` there is the
package's own statement rather than an icon inferred from a search row,
and its presence is also the proof that the package still exists.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import quote

BASE = "https://aminet.net"
SEARCH = f"{BASE}/search"

#: Rows per page. Aminet's own, and not negotiable: `pagesize`, `limit`
#: and `rows` were all tried against the live search and all ignored.
#: The shelf listings use the same 50.
PAGE_ROWS = 50

#: The one string on every search page and on no other page Aminet
#: serves. "Found 50 matching packages", "Found 1 matching package",
#: "Found 0 matching packages" -- the count line is rendered even when the
#: result table is not, which is what makes it a better shape check than
#: the table. The themed 404 page carries neither.
SEARCH_MARKER = "matching package"

# One result row, anchored on the name cell rather than on the <tr>.
_ROW = re.compile(
    r'class="name_col">.*?<a href="/(?P<path>[^"]+)"\s*>(?P<filename>[^<]*)</a>'
    r"(?P<rest>.*?)</tr>",
    re.S,
)
_SIZE = re.compile(r'class="size_col"[^>]*>\s*(?:<[^>]*>\s*)*([^<\s][^<]*)')
_ARCH = re.compile(r'src="/pics/([a-z0-9_.+-]+)\.png"')
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DESC = re.compile(r'<a href="/package/[^"]*"\s*>([^<]*)</a>')

#: `/pics/` also serves the site's own furniture. These are never an
#: architecture, and matching one would file a package under a logo.
_NOT_ARCHITECTURES = frozenset(
    {"aminet", "aminet_sketch_64", "pix", "blank", "back", "folder", "dir"}
)

#: `Found 910 matching packages`. Read so the walk can stop at the last
#: page instead of asking for one past it, and so a browse can say how
#: much of a shelf it is showing.
_FOUND = re.compile(r"Found\s+([\d,]+)\s+matching package", re.IGNORECASE)

#: A shelf path an operator or a config may name. Two or three components
#: of the archive's own alphabet, nothing else. This becomes a URL path,
#: so it is an allowlist of what a shelf name may contain rather than a
#: denylist of what it may not -- the same posture
#: `rom_hub.types.bare_filename` takes.
_SHELF_RE = re.compile(r"\A[a-z0-9][a-z0-9_+-]*(?:/[a-z0-9][a-z0-9_+-]*)?\Z")


class AminetError(Exception):
    """Aminet could not be read."""


@dataclass(frozen=True)
class Package:
    #: `game/think/abrick.lha` -- the path under the archive root, which
    #: is both the id and the download location.
    path: str
    filename: str
    #: `game/think`. Aminet's own shelf; see `platforms.py`.
    directory: str
    #: Every architecture icon on the row. Usually one; a package built
    #: for two targets carries two, and that is a refusal rather than a
    #: coin toss.
    architectures: tuple[str, ...] = ()
    description: str = ""
    #: Aminet's rounded rendering ("2.0M", "142K"), or "". Shown, never
    #: computed with -- the same rule the other listing-based plugins in
    #: this directory apply.
    size_text: str = ""
    date_text: str = ""

    @property
    def architecture(self) -> str:
        """The single architecture, or "" when there is not exactly one."""
        return self.architectures[0] if len(self.architectures) == 1 else ""

    @property
    def readme_path(self) -> str:
        """`game/think/abrick.lha` -> `game/think/abrick.readme`.

        Aminet's convention, and it is a *stem* swap rather than an
        append: `abrick.lha.readme` does not exist.
        """
        stem = self.path.rsplit(".", 1)[0] if "." in self.filename else self.path
        return f"{stem}.readme"

    @property
    def page_url(self) -> str:
        """The human page. Shown in results; never fetched."""
        return f"{BASE}/package/{self.path.rsplit('.', 1)[0]}"


def download_url(path: str) -> str:
    """Where Aminet serves one package.

    Verified 2026-07-29: answers 200 with `application/octet-stream` and
    **no redirect** -- Aminet's mirrors are separate hostnames an operator
    chooses, not something the main host bounces you to.
    """
    return f"{BASE}/" + quote(path.strip().lstrip("/"), safe="/")


def readme_url(path: str) -> str:
    return f"{BASE}/" + quote(path.strip().lstrip("/"), safe="/")


def shelf_url(shelf: str) -> str:
    """Where one shelf lists its own packages.

    `game/think` -> `https://aminet.net/game/think`, which is a real page
    with the same result table the search returns and its own `page=N`.
    That is the whole reason a browse is possible: `?dir=` never scoped
    anything, and this does.

    Refuses a shelf name that is not two or three of Aminet's own path
    components. The value goes into a URL and the host re-checks the
    result against the allowlist, but a `..` here is a caller mistake
    worth naming rather than a string to quietly repair.
    """
    name = (shelf or "").strip().strip("/").lower()
    if not _SHELF_RE.match(name):
        raise AminetError(
            f"{shelf!r} is not an Aminet shelf: expected something like "
            f"'game/think' or 'demo' -- lowercase letters, digits, '_', '+' "
            f"and '-', in one or two '/'-joined components"
        )
    return f"{BASE}/" + quote(name, safe="/")


def total_matches(text: str) -> int | None:
    """How many packages the page says it is one page of, or None.

    None means the count line was not there, which `parse_results` has
    already refused the document for -- so in practice this is an int on
    every page the plugin acts on. It exists so a walk can stop at the
    real last page rather than discovering it by asking for one that does
    not exist.
    """
    match = _FOUND.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_results(text: str) -> list[Package]:
    """Every package row on one search page **or one shelf listing**.

    The two pages share this markup and share the count line, so they
    share the parser. A shelf listing that has been renamed or removed
    answers with the site's themed error body, which this refuses for the
    same reason a bad search does.

    Raises for a document that is not one of those pages. Aminet answers a
    missing path with a 200 and a themed "not found" body -- its
    `/robots.txt` is one -- so a status code is not evidence and the
    parser has to be.

    **The check is the count line, not the table**, and that distinction
    was paid for live: `?query=steel+sky&dir=game` finds one package, so
    page 2 of that search is a perfectly valid search page carrying "Found
    1 matching package" and no table at all. A parser keyed on the table
    calls that a dead source and takes the whole search down with it.
    """
    if not isinstance(text, str) or not text:
        raise AminetError("Aminet returned an empty document")
    if SEARCH_MARKER not in text.lower():
        raise AminetError(
            "Aminet's answer is not a search page: it carries no "
            f"{SEARCH_MARKER!r} count line. Aminet answers a bad path with "
            "HTTP 200 and a themed error body, so the status code cannot be "
            "trusted and this check is what stands in for it."
        )

    packages: list[Package] = []
    for match in _ROW.finditer(text):
        path = html.unescape(match.group("path")).strip()
        filename = html.unescape(match.group("filename")).strip()
        if not path or not filename or path.endswith("/"):
            continue
        rest = match.group("rest")

        architectures = tuple(
            dict.fromkeys(
                arch
                for arch in _ARCH.findall(rest)
                if arch not in _NOT_ARCHITECTURES
            )
        )
        size = _SIZE.search(rest)
        date = _DATE.search(rest)
        description = _DESC.search(rest)

        packages.append(
            Package(
                path=path,
                filename=filename,
                directory=path.rsplit("/", 1)[0] if "/" in path else "",
                architectures=architectures,
                description=(
                    html.unescape(description.group(1)).strip() if description else ""
                ),
                size_text=html.unescape(size.group(1)).strip() if size else "",
                date_text=date.group(1) if date else "",
            )
        )
    return packages


def parse_readme(text: str) -> dict[str, str]:
    """The header of a `.readme`, lowercased keys, in order.

    Stops at the first blank line: everything after it is prose, and a
    line in the prose that happens to read `Author: someone else` must not
    overwrite the header's. Aminet's own uploads follow that layout and
    the parser holds them to it rather than scanning the whole file.
    """
    if not isinstance(text, str) or not text.strip():
        raise AminetError("the package's .readme was empty")
    header: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        key, separator, value = line.partition(":")
        if not separator:
            # A continuation or an unlabelled first line. Aminet's headers
            # start at line 1, so this ends the header rather than being
            # skipped past.
            break
        key = key.strip().lower()
        if key and key not in header:
            header[key] = value.strip()
    if not header:
        raise AminetError(
            "the package's .readme has no header fields, so its Architecture: "
            "cannot be read. Aminet requires that header on upload, and a file "
            "without one is not a package description."
        )
    return header
