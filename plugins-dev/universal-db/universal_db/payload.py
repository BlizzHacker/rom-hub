"""Choosing which of an entry's downloads is the ROM.

Universal-DB gives an entry a *dict* of release assets, not a flagged
default the way the Homebrew Hub does, and one entry routinely holds
several things that are not interchangeable: `PKCount.3dsx`,
`PKCount.cia` and `PKCount.nds` are three consoles' worth of one app;
`SuDokuL-v1.5-gamecube.zip` sits next to `SuDokuL-v1.5-3ds-cia.zip`;
`thextech-3ds-assets-smbx13-v1.3.7.3.zip` is 48 MB of level data next to a
4 MB program. So the choice has to be made on evidence, and there are
exactly two kinds available.

**The extension, for a bare file.** A `.cia` is a 3DS title and a `.nds`
is a DS one. That is not a heuristic, it is what the formats are, and it
is also what keeps the two platforms apart on the eight entries published
for both.

**The database's own `archive` map, for an archive.** Universal-DB
publishes, per entry, a table of `{archive-name-pattern: {label: [paths
inside]}}` -- its manifest of what a release archive contains, used by
Universal-Updater to install one. An archive is a candidate only when that
table matches it *and* names a title file for the platform being imported.
`CrossCraft-3DS.zip` qualifies for `3ds` and `CrossCraft-Linux.zip` never
does, because the database says what is in each.

**Where the evidence runs out, this refuses.** `thextech` matches all
three of its archives against one pattern, so the database does not say
which is the program; the refusal names all three. Picking the largest
would have taken a 48 MB asset pack, and picking the smallest would be a
rule invented here rather than read from the source. 61 of the 394
entry-platform pairs refuse this way, every one of them by name -- which
is the point: a gap somebody can see costs a message, and a guess costs a
library row that is wrong with nothing to say so.

Nothing in this module opens a socket or reads a URL. It sorts strings the
database already published.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .db import Download, Entry
from .platforms import ARCHIVE_FORMATS, format_rank

#: A pattern this long is not an archive name pattern. Compiling arbitrary
#: text out of a remote document is cheap to bound and awkward to explain
#: afterwards.
MAX_PATTERN_CHARS = 200

#: The hosts `manifest.toml` declares. Kept here as well so a download the
#: broker would refuse is refused *here*, naming the host, instead of
#: surfacing as an opaque policy violation from inside the Hub. The
#: manifest is still the thing that is enforced -- this is a better error
#: message, not a second gate.
DECLARED_HOSTS: tuple[str, ...] = (
    "db.universal-team.net",
    "github.com",
    "*.githubusercontent.com",
    "gitlab.com",
    "codeberg.org",
    "gbatemp.net",
    "cdn.classicube.net",
    "apotrisstorage.blob.core.windows.net",
)


class NoPayload(Exception):
    """This entry has nothing this platform could boot."""


class AmbiguousPayload(Exception):
    """Several archives qualify and the database does not say which."""


class Unreachable(Exception):
    """The chosen file is somewhere this plugin may not fetch from."""


@dataclass(frozen=True)
class Choice:
    download: Download
    #: 0 for a bare title file, 1 for an archive the database vouches for.
    #: Only used to explain the choice; the ranking is done below.
    from_archive: bool


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    for pattern in DECLARED_HOSTS:
        pattern = pattern.lower().strip(".")
        if pattern.startswith("*."):
            if host.endswith("." + pattern[2:]):
                return True
        elif host == pattern:
            return True
    return False


def check_reachable(entry: Entry, download: Download) -> None:
    """Refuse a URL the broker would refuse, but say why.

    Two live entries publish over plain `http` -- `lolsnes` and
    `tasmanquest` -- and `rom_hub.netpolicy` permits `https` only. That is
    the host's rule and this does not work around it; it explains it,
    because "blocked request to ..." on its own reads like a bug in the
    plugin rather than a fact about where the author put the file.
    """
    parts = urlsplit(download.url)
    if parts.scheme.lower() != "https":
        raise Unreachable(
            f"Universal-DB entry {entry.slug!r} ({entry.title!r}) publishes "
            f"{download.name!r} over {parts.scheme or 'no'}:// -- ROM Hub "
            f"fetches over https only, and downgrading the transport for a "
            f"file nobody can verify is not something this plugin will do. "
            f"The entry page is {entry.url}."
        )
    host = parts.hostname or ""
    if not _host_allowed(host):
        raise Unreachable(
            f"Universal-DB entry {entry.slug!r} ({entry.title!r}) publishes "
            f"{download.name!r} on {host!r}, which is not in this plugin's "
            f"manifest network allowlist. Universal-DB does not host the "
            f"files -- each entry points at wherever its author publishes -- "
            f"so a new author means a new host. Add it to "
            f"`permissions.network` in manifest.toml and to DECLARED_HOSTS "
            f"in universal_db/payload.py, which must agree."
        )


def _matches(pattern: str, name: str) -> bool:
    """Whether one `archive` key matches one download name.

    The keys really are regular expressions -- `3DSQuickReboot-.*\\.zip`,
    `Apotris-(.*)?3(ds|DS)(-.*)?\\.zip` -- so they are compiled, not
    compared. `fullmatch`, because these describe a whole filename: under
    a substring match `scummvm-.*-ds.zip` would claim
    `scummvm-nightly-ds.zip.sig` too.
    """
    if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN_CHARS:
        return False
    try:
        return re.fullmatch(pattern, name) is not None
    except (re.error, RecursionError):
        # A record with a broken pattern is a record whose archives cannot
        # be vouched for. That is a refusal below, not a crash here.
        return False


def _archive_rank(entry: Entry, download: Download, platform: str) -> int | None:
    """Best title-format rank the database claims is inside this archive.

    Both halves of the map are read. The label is usually the file
    (`SuperHaxagon.cia`) but sometimes a description (`Apotris.3dsx +
    assets`), and the paths are usually the file (`3ds/Apotris/Apotris.cia`)
    but sometimes not (`scummvm.nds` is listed at a path ending `.ds`). One
    of the two says what it is on every live record; neither does alone.
    """
    best: int | None = None
    for pattern, contents in (entry.archive or {}).items():
        if not isinstance(contents, dict) or not _matches(pattern, download.name):
            continue
        for label, paths in contents.items():
            candidates = [label]
            if isinstance(paths, list):
                candidates.extend(p for p in paths if isinstance(p, str))
            for candidate in candidates:
                if not isinstance(candidate, str) or "." not in candidate:
                    continue
                rank = format_rank(platform, candidate.rsplit(".", 1)[-1])
                if rank is not None and (best is None or rank < best):
                    best = rank
    return best


def choose(entry: Entry, platform: str) -> Choice:
    """The one download to import for `entry` on `platform`.

    Bare title files first, best format first (`.cia` over `.3dsx`,
    `.nds` over `.dsi` -- see platforms.py), largest first within a
    format, then by name so the answer never depends on dict order. The
    size tie-break is the same one the Archive.org plugin uses and for the
    same reason: where two files share a format they are alternate builds
    of one program, and a stub cannot outrank the real thing.
    """
    ranked = sorted(
        (
            (rank, -(d.size_bytes or 0), d.name, d)
            for d in entry.downloads
            for rank in [format_rank(platform, d.extension)]
            if rank is not None
        ),
        key=lambda row: row[:3],
    )
    if ranked:
        return Choice(download=ranked[0][3], from_archive=False)

    # No bare title file. Fall back to what the database says is inside an
    # archive -- and only to that.
    by_rank: dict[int, list[Download]] = {}
    for download in entry.downloads:
        if download.extension not in ARCHIVE_FORMATS:
            continue
        rank = _archive_rank(entry, download, platform)
        if rank is not None:
            by_rank.setdefault(rank, []).append(download)

    if by_rank:
        best = min(by_rank)
        candidates = sorted(by_rank[best], key=lambda d: d.name)
        if len(candidates) == 1:
            return Choice(download=candidates[0], from_archive=True)
        names = ", ".join(d.name for d in candidates)
        raise AmbiguousPayload(
            f"Universal-DB entry {entry.slug!r} ({entry.title!r}) ships "
            f"{len(candidates)} archives its own manifest describes "
            f"identically for {platform}: {names}. The database does not say "
            f"which is the program and which are asset packs, and choosing by "
            f"size would file whichever happens to be bigger. Download the one "
            f"you want from {entry.url} instead."
        )

    if not entry.downloads:
        raise NoPayload(
            f"Universal-DB entry {entry.slug!r} ({entry.title!r}) lists no "
            f"downloads at all, so there is nothing to fetch. Its page is "
            f"{entry.url}."
        )
    have = ", ".join(d.name for d in entry.downloads)
    raise NoPayload(
        f"Universal-DB entry {entry.slug!r} ({entry.title!r}) has no {platform} "
        f"title among its downloads ({have}). A bare file is taken when its "
        f"extension is a title format for the platform, and an archive only "
        f"when the entry's own `archive` manifest says a title is inside it; "
        f"this entry offers neither. Its page is {entry.url}."
    )
