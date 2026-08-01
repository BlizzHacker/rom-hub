"""Search the IF Archive's game directories by title.

The archive has a search service (`search.ifarchive.org`), and this plugin
does not use it: it is a POST form whose results cover the whole archive
including tools, articles and hint files, and `ctx.http` offers GET only.
The directory indexes are a complete listing of exactly the four runtime
directories that matter, so the match runs here instead.

**Matching is on the filename, and it is a subset match on words.** An IF
Archive filename is the closest thing the archive has to a title --
`A_Beauty_Cold_and_Austere.gblorb`, `HouseOfDreamOfMoon.z8`,
`905.z5` -- so a query's terms all have to appear in the *normalised*
filename, in any order. Normalising means: percent-decoding already done
upstream, extension dropped, `_`, `-` and `.` treated as spaces, and
CamelCase split, so `dream of moon` finds `HouseOfDreamOfMoon.z8` and
`beauty austere` finds `A_Beauty_Cold_and_Austere.gblorb`.

The archive's own description is matched too, but only as a *fallback*
when the filename match found nothing, and the result says which happened
in `extra.matched_on`. Descriptions are prose -- "an MS-DOS executable is
in games/pc/905.exe" -- so folding them into the primary match would put
`dos` and `pc` results in front of anybody searching for either. That is
the same relevance bug the archive-org plugin fixed by confining its query
to the title field.

Non-story files are never returned. An index carries `.txt` notes, `.pdf`
maps, `.zip` bundles and source archives beside the games; `formats.py`
decides what counts, and a file whose extension is not a story format is
not a result. A file whose extension *is* a known story format but has no
RomM platform -- a `.blb`, a `.taf` -- is returned with `platform` unset,
because hiding a game somebody can see on the archive's own site would be
worse than showing why it will not import.
"""

import re

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .formats import PLATFORMS, format_for
from .index import DEFAULT_DIRECTORIES, Index, IndexUnavailable, clean_directory

#: Reading every configured directory costs one request each, so the list
#: is bounded: an operator who names thirty directories is asking for
#: thirty 500 KB pages inside one 30-second plugin timeout.
MAX_DIRECTORIES = 12

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_RE = re.compile(r"[^0-9a-z]+")


def normalise(text: str) -> str:
    """A filename or a query reduced to lowercase words separated by spaces.

    Three steps, and the order of all three matters. CamelCase is split
    *first*, because after `.lower()` there is nothing left to split on:
    `HouseOfDreamOfMoon` has to become `house of dream of moon`. The case
    is folded *second*, before punctuation is removed, because the
    punctuation rule is an allowlist of `[0-9a-z]` and running it on
    unfolded text deletes every capital letter -- which is exactly the bug
    the first version of this function had, and it turned
    `HouseOfDreamOfMoon` into `ouse f ream f oon`.
    """
    return " ".join(_NON_WORD_RE.sub(" ", _CAMEL_RE.sub(" ", text).lower()).split())


def matches(terms: list[str], haystack: str) -> bool:
    """True when every term appears in `haystack`, in any order.

    Substring rather than whole-word, because IF Archive filenames run
    words together in ways the CamelCase split does not always catch
    (`ditchday.zip`, `acorncourt.z5`), and a query of `acorn` finding
    `acorncourt` is the behaviour anybody expects. All terms required, so
    adding a word narrows rather than widens.
    """
    return all(term in haystack for term in terms)


class Search(SearchProvider):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._index = Index(ctx.http)

    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        wanted = (platform or "").strip().lower()
        if wanted and wanted not in PLATFORMS:
            # The IF Archive is interactive fiction. Asking it for SNES is
            # a reasonable question with a boring answer, and answering it
            # without four HTTP requests is better than answering it after.
            return []

        terms = [term for term in normalise(query or "").split() if term]
        results: list[SearchResult] = []
        fallback: list[SearchResult] = []

        for directory in self._directories():
            if len(results) >= limit:
                break
            for entry in self._index.entries(directory):
                story = format_for(entry.filename)
                if story is None:
                    continue
                if wanted and story.platform != wanted:
                    continue
                title = normalise(entry.filename.rpartition(".")[0])
                if terms and not matches(terms, title):
                    if matches(terms, normalise(entry.description)):
                        result = self._result(entry, story, "description")
                        if result is not None:
                            fallback.append(result)
                    continue
                result = self._result(entry, story, "filename" if terms else "listing")
                if result is not None:
                    results.append(result)
                if len(results) >= limit:
                    break

        if results:
            # A filename match is the answer. Topping the list up with
            # description matches whenever there is room looks generous
            # and is not: the archive's blurbs carry Inform serial numbers
            # ("Release 1 / Serial number 990905"), so a live search for
            # `905` returned `905.z5` and then four unrelated games whose
            # serials happen to contain those digits. The fallback is for
            # a query that found nothing, not for filling space.
            return results
        return fallback[:limit]

    def _directories(self) -> list[str]:
        configured = self.ctx.config.get("directories") or list(DEFAULT_DIRECTORIES)
        if isinstance(configured, str):
            configured = [configured]
        if len(configured) > MAX_DIRECTORIES:
            raise IndexUnavailable(
                f"`directories` names {len(configured)} directories and this "
                f"plugin reads at most {MAX_DIRECTORIES} per search; each one "
                f"is a separate index page of up to half a megabyte"
            )
        seen: list[str] = []
        for value in configured:
            directory = clean_directory(str(value))
            if directory not in seen:
                seen.append(directory)
        return seen

    @staticmethod
    def _result(entry, story, matched_on: str) -> SearchResult | None:
        try:
            return SearchResult(
                # The decoded archive path. It is what `plan()` needs and
                # the only thing it needs, so an import costs no request.
                source_id=entry.path,
                title=entry.filename,
                # None for a known format RomM has no platform for. The
                # importer refuses and names it; see formats.py.
                platform=story.platform,
                url=entry.page_url,
                extra={
                    "runtime": story.runtime,
                    "format": story.extension,
                    "directory": entry.directory,
                    "date": entry.date,
                    "description": entry.description,
                    "matched_on": matched_on,
                },
            )
        except (ValidationError, TypeError, ValueError):
            # Thirty-odd years of community uploads put some very odd text
            # in these fields. One bad row must not cost the directory.
            return None
