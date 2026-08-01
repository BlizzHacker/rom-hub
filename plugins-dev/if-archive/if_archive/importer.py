"""if-archive `importer`: turn one archive path into a FetchPlan.

The plugin decides *what* should be fetched and nothing else. It opens no
socket; `ctx.http` is an RPC back to the host, and the host re-validates
every URL in the returned plan against this plugin's own manifest
allowlist before fetching any of it.

**An import costs no HTTP request at all.** A search result already
carries the archive path, and the archive path plus `formats.py` is
everything a plan needs: the URL is the path, the platform is the
extension, the filename is the last component. The 491 KB index is not
re-read to confirm what is already in hand, and this class therefore
never calls `ctx.http`.

Three decisions here are the careful half of a choice that could have gone
the other way.

**The path is re-validated, not trusted.** `source_id` normally comes from
this plugin's own search, but an operator can type one, so it goes back
through `index.archive_path`: decoded once, confined to
`if-archive/games/`, and refused if it carries a `..`, an empty component,
a backslash or a colon. The host checks the resulting URL against the
allowlist afterwards regardless; this is the layer that has to hold if
that one ever has a gap.

**The filename is decoded, never narrowed.** 40 of the 1,509 files in the
four runtime directories are percent-encoded in the index. Decoded, every
one of them passes `rom_hub.types.bare_filename` -- `Escape!.zblorb`,
`Apollo18+20.zip`, `Ancient Treasure, Secret Spider.zblorb` -- because
`!`, `+`, `,`, `(`, `)` and `'` are all permitted in a ROM filename. The
tempting shortcut is to refuse anything with a `%` in it, or to strip the
punctuation out; both would silently drop those forty files and neither
would ever produce a message. So: decode, then hand the result to the
host's validator and let a name it refuses be a *refusal that names the
file*.

**An unmapped format fails visibly.** See `formats.py`. `.blb`, `.taf`,
`.a3c` and the rest are real story files with no RomM platform, and the
refusal says which format and why rather than filing the game under a
runtime that cannot run it.
"""

import posixpath
from urllib.parse import quote

from pydantic import ValidationError

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .formats import format_for
from .index import FILE_BASE, IndexUnavailable, archive_path

#: Everything imported from here lands in one library collection by
#: default, so an operator can see at a glance what came from the IF
#: Archive and what did not.
DEFAULT_COLLECTION = "IF Archive"


class ImportRefused(Exception):
    """This file cannot be imported, and the message says why.

    Raised for every refusal -- not a story file, no RomM platform,
    unusable path, unusable filename -- because they all reach an operator
    the same way: as the `error` column of a FAILED job.
    """


def download_url(path: str) -> str:
    """The archive URL for a validated, decoded path.

    Built from the path rather than carried through from the index, so an
    operator-supplied `source_id` and a search-supplied one produce the
    same URL. `ifarchive.org` answers a Z-machine file directly and 302s a
    Glulx, TADS or Hugo one to `ukrestrict.ifarchive.org`; both hosts are
    in the manifest allowlist, because the Hub re-checks every redirect
    hop and a download that left the allowlist mid-flight would stop
    there.
    """
    return FILE_BASE + quote(path, safe="/")


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        raw = (result.source_id or "").strip()
        if not raw:
            raise ImportRefused(
                "the search result carries no IF Archive path; expected "
                "something like 'if-archive/games/zcode/905.z5'"
            )
        try:
            path = archive_path(raw)
        except IndexUnavailable as exc:
            raise ImportRefused(str(exc)) from exc

        filename = posixpath.basename(path)
        if not filename:
            raise ImportRefused(
                f"{raw!r} names a directory rather than a file, so there is "
                f"nothing to fetch"
            )

        story = format_for(filename)
        if story is None:
            raise ImportRefused(
                f"{filename!r} is not an interactive fiction story file: its "
                f"extension is not one this plugin maps. The IF Archive's game "
                f"directories hold notes, maps, source code and zipped bundles "
                f"beside the games, and only a bare story file can be imported "
                f"-- the Hub does not unpack an archive on the import path"
            )
        if story.platform is None:
            raise ImportRefused(
                f"{filename!r} needs mapping: it is a {story.runtime} file, "
                f"and {story.unmapped_reason}. Nothing was fetched"
            )

        try:
            payload = FetchFile(url=download_url(path), filename=filename)
        except (ValidationError, ValueError) as exc:
            # The host's own validator has the last word on this name.
            # Calling it here turns a name it would refuse into a message
            # naming the file, instead of a pydantic error naming a field.
            raise ImportRefused(
                f"the IF Archive lists {filename!r} under {path!r}, and that "
                f"is not a name the Hub will open for writing: {exc}"
            ) from exc

        return FetchPlan(
            files=[payload],
            platform=story.platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )
