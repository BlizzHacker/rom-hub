"""Enumerating every No-Intro item on Archive.org, and proving it.

`search.py` reads twenty-five directories somebody typed into
`manifest.toml`. That is a configuration, not a corpus, and the number it
produces -- 15,165 archives -- is a fact about the list rather than about
Archive.org. This module answers the other question: **what is all of it?**

## The denominator, and where it comes from

`identifier:nointro*` matches **71 items** (2026-08-01). Note the shape:
these are *items*, not a collection -- `collection:nointro` returns zero,
because No-Intro on Archive.org is seventy-one separate uploads by
different people and not a curated collection anybody assembled.

One `advancedsearch.php` request returns, for every one of them,
`files_count` and `item_size`. `files_count` is the load-bearing field:
it is Archive.org's own count of the entries in the item, and it comes
back from the **search index**, while the enumeration below reads the
**metadata endpoint**. Two different services, two different requests --
so "we enumerated 820 files in `nointro.gg`" is checked against a number
that the enumeration had no hand in producing. Verified across all
seventy-one items: `files_count` from the search index equals the length
of `files` from the metadata endpoint, every time.

That is what makes the completeness claim mean anything. `rom_hub.census`
then requires, per item, that

    files_count  ==  records kept  +  everything skipped, by reason

## One request per item, and why that is safe here

`ctx.http` caps a response at 4 MiB. The metadata endpoint has no slicing
-- `metadata/<id>/files/0:100` answers `{"error": "File '0:100' not
found"}` -- so an item is one request or it is nothing. Measured, the
largest of the seventy-one is `NoIntro-commodore-64_202302` at 3,682 files
and **1,069,810 bytes**: about 290 bytes per entry.

So the ceiling is checked rather than hoped for. `MAX_FILES_PER_REQUEST`
is derived from that measured density with a margin, an item over it is
**refused by name** with the number in the message, and the refusal lands
as a failed unit in the report rather than as a truncated catalogue. No
item on Archive.org today comes close; the check is there because "the
largest thing that exists today" is not a bound.

## What a record carries

Every file: name, size, and Archive.org's `md5`, `sha1` and `crc32`. The
digests go into `extra` under the keys `rom_hub.grouping` reads, and they
are the reason this is a census rather than a concatenation. Measured on
the real corpus:

* `NoIntro_VirtualBoy` and `NoIntroVirtualBoy` -- two items, thirty-one
  byte-identical archives. They collapse on proof.
* `NoIntroNintendo`, titled "No Intro - Nintendo", shares thirty-one
  hashes with `NoIntroVirtualBoy`. It is a Virtual Boy set with a wrong
  title, and the hashes say so where the name does not.
* `nointro.ws` (`.7z`) and `NoIntro_BandiWonderSwan` (`.zip`) share
  **zero** hashes, because the containers differ even though the ROMs
  inside do not. The name parse decides those, which is the correct
  outcome for the correct reason -- and where neither hash nor name
  agrees, the result is *more rows*, never a wrong merge.

## Being a considerate client

Two request kinds, both GETs, both cached for the life of the process.
The scope query is paged rather than asked for in one lump, and a failed
request is retried with a widening gap rather than immediately. The
scrape API is not used at all -- see `archive_org.index` for the two
measurements that disqualified it.
"""

from __future__ import annotations

import json
import time

from rom_hub_sdk import CensusPage, CensusProvider, CensusRecord, CensusUnit

from .classify import classify, exclusion_reason
from .index import METADATA_SUFFIXES
from .platforms import platform_for

SEARCH_ENDPOINT = "https://archive.org/advancedsearch.php"
METADATA_ENDPOINT = "https://archive.org/metadata/"

#: What the census asks Archive.org for. `identifier:nointro*` rather than
#: `collection:nointro`, which matches nothing -- see the module docstring.
DEFAULT_SCOPE_QUERY = "identifier:nointro*"

#: Index fields. Exactly the five the classifier and the denominator need;
#: asking for more would only make the scope response bigger.
SCOPE_FIELDS = ("identifier", "title", "mediatype", "files_count", "item_size")

#: Items per scope request. Well inside the 4 MiB cap at ~600 bytes per
#: document, and inside `advancedsearch.php`'s deep-paging limit for any
#: plausible number of pages.
SCOPE_ROWS = 500

#: A stop on the scope paging. 100 pages of 500 is 50,000 items, which is
#: three orders of magnitude above the seventy-one that exist.
MAX_SCOPE_PAGES = 100

#: Derived from a measured 290 bytes per file entry against the host's
#: 4 MiB `ctx.http` ceiling, halved for margin: titles vary and an item of
#: unusually long filenames would beat the average. An item over this is
#: refused by name rather than truncated.
MAX_FILES_PER_REQUEST = 7000

#: Archive.org marks its own bookkeeping this way in the file list, in
#: addition to the name suffixes `index.py` already knows.
BOOKKEEPING_SOURCE = "metadata"

#: The skip reasons this plugin uses. Written down as constants because
#: they are printed in the completeness report and totalled across units --
#: a reason spelled two ways would split into two rows and look like two
#: different findings.
SKIP_BOOKKEEPING = "archive.org bookkeeping (torrent, _meta.xml, thumbnails)"
SKIP_DIRECTORY = "a directory entry, not a file"

#: Backoff between retries of one request, in seconds. Additive rather than
#: exponential: this is a free public service being asked seventy-one
#: polite questions, not a hot loop worth an aggressive curve.
RETRY_WAITS = (1.0, 3.0)


class CensusUnavailable(Exception):
    """Archive.org could not be read, and the message says which part."""


class Census(CensusProvider):
    """`identifier:nointro*`, item by item, with the arithmetic kept."""

    def scope(self) -> list[CensusUnit]:
        """Every `nointro*` item, classified, with its declared file count.

        One paged search, and no item is opened. The kind decides whether
        `rom_hub.census` will walk it, and every kind that is not `roms`
        carries the sentence explaining why -- so an item left out is left
        out in public.
        """
        query = str(self.ctx.config.get("scope_query") or DEFAULT_SCOPE_QUERY)
        units: list[CensusUnit] = []
        seen: set[str] = set()

        for page in range(1, MAX_SCOPE_PAGES + 1):
            docs, total = self._scope_page(query, page)
            for doc in docs:
                identifier = doc.get("identifier")
                if not isinstance(identifier, str) or identifier in seen:
                    continue
                seen.add(identifier)
                unit = self._unit(doc, identifier)
                if unit is not None:
                    units.append(unit)
            if len(seen) >= (total or 0) or not docs:
                break

        if not units:
            raise CensusUnavailable(
                f"the scope query {query!r} matched nothing on "
                f"advancedsearch.php. That is not an empty archive: it is a "
                f"query that found no items, and cataloguing zero of zero "
                f"would be a completeness claim about nothing."
            )
        return units

    def _unit(self, doc: dict, identifier: str) -> CensusUnit | None:
        files_count = _as_int(doc.get("files_count"))
        item_size = _as_int(doc.get("item_size"))
        kind = classify(doc.get("mediatype"), files_count or 0, item_size or 0)
        reason = exclusion_reason(kind)
        title = doc.get("title")
        label = str(title) if isinstance(title, str) and title.strip() else identifier
        try:
            return CensusUnit(
                unit_id=identifier,
                label=label[:500],
                kind=kind,
                # Archive.org's own count, from the search index. The walk
                # reads a different endpoint, so this is a denominator the
                # enumeration cannot have influenced.
                declared_total=files_count,
                # An item may hold several machines in subdirectories, so
                # the platform is a per-record fact here, not a per-unit
                # one. Stated as unknown rather than guessed at.
                platform=None,
                size_bytes=item_size,
                include=not reason,
                reason=reason,
                extra={"mediatype": str(doc.get("mediatype") or "")},
            )
        except Exception:  # noqa: BLE001 - one odd item must not cost the rest
            # Titles and mediatypes come from uploads by strangers and land
            # in constrained fields. A unit that will not validate is
            # dropped from the scope, which the host then reports as a
            # smaller denominator rather than a silently truncated walk.
            return None

    def enumerate(self, unit: CensusUnit, cursor: str | None) -> CensusPage:
        """One item's entire file list, in one request.

        There is no second page: the metadata endpoint does not slice, so
        the cursor is always `None` on the way out. The parameter is still
        honoured -- a cursor arriving here means a resumed build thinks
        this unit was partial, and the only correct response is to read it
        again from the start rather than to pretend to continue.
        """
        declared = unit.declared_total
        if declared is not None and declared > MAX_FILES_PER_REQUEST:
            raise CensusUnavailable(
                f"item {unit.unit_id!r} declares {declared:,} files and the "
                f"metadata endpoint cannot be paged -- it answers "
                f"{{'error': \"File '0:100' not found\"}} for every slice, so "
                f"an item is one response or none. At the measured ~290 bytes "
                f"per entry that response would risk the host's 4 MiB ceiling, "
                f"and a truncated file list would silently understate this "
                f"item's coverage. Refusing it by name instead."
            )

        payload = self._metadata(unit.unit_id)
        files = payload.get("files")
        if not isinstance(files, list):
            raise CensusUnavailable(
                f"the metadata for item {unit.unit_id!r} carries no file list; "
                f"it answered {sorted(payload)[:8]}"
            )

        records: list[CensusRecord] = []
        skipped: dict[str, int] = {}
        for entry in files:
            if not isinstance(entry, dict):
                skipped[SKIP_DIRECTORY] = skipped.get(SKIP_DIRECTORY, 0) + 1
                continue
            record = self._record(unit.unit_id, entry)
            if record is None:
                reason = _skip_reason(entry)
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                records.append(record)

        return CensusPage(
            records=records,
            cursor=None,
            # The metadata endpoint publishes its own count beside the
            # list. Reported so the host compares against the item's own
            # statement of its size rather than against the length of the
            # thing it just parsed.
            declared_total=_as_int(payload.get("files_count")),
            skipped=skipped,
        )

    # -- one file -------------------------------------------------------

    def _record(self, identifier: str, entry: dict) -> CensusRecord | None:
        """One catalogued file, or None for something that is not one."""
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        if entry.get("source") == BOOKKEEPING_SOURCE:
            return None
        if name.endswith(METADATA_SUFFIXES):
            return None

        # The directory a file sits in is the only statement anybody makes
        # about its platform, and in a multi-machine item that directory is
        # a subdirectory of the item. `NoIntro-Atari/Atari - Lynx` is the
        # key the existing table already uses, so the same lookup serves
        # the census and the importer.
        subdirectory, _, _ = name.rpartition("/")
        directory = f"{identifier}/{subdirectory}" if subdirectory else identifier

        try:
            return CensusRecord(
                record_id=f"{directory}/{name.rpartition('/')[2]}",
                title=name.rpartition("/")[2],
                # None when the directory is not in the table. Catalogued
                # anyway, and counted as unmapped in the report: the file
                # exists whether or not this plugin knows which machine it
                # is for, and dropping it would make the census understate
                # the source to keep its own bookkeeping tidy.
                platform=platform_for(directory),
                size_bytes=_as_int(entry.get("size")),
                url=f"https://archive.org/download/{directory}/"
                    f"{name.rpartition('/')[2]}",
                extra=_digests(entry, directory),
            )
        except Exception:  # noqa: BLE001 - one bad row, not the whole item
            return None

    # -- requests -------------------------------------------------------

    def _scope_page(self, query: str, page: int) -> tuple[list[dict], int | None]:
        body = self._json(
            SEARCH_ENDPOINT,
            {
                "q": query,
                "fl[]": list(SCOPE_FIELDS),
                "rows": SCOPE_ROWS,
                "page": page,
                "output": "json",
                "sort[]": "identifier asc",
            },
            what=f"the scope query {query!r} (page {page})",
        )
        error = body.get("error")
        if isinstance(error, str) and error:
            raise CensusUnavailable(
                f"Archive.org refused the scope query {query!r}: {error.strip()}"
            )
        response = body.get("response")
        if not isinstance(response, dict):
            raise CensusUnavailable(
                f"Archive.org's answer to {query!r} carried no result set"
            )
        docs = [d for d in (response.get("docs") or []) if isinstance(d, dict)]
        return docs, _as_int(response.get("numFound"))

    def _metadata(self, identifier: str) -> dict:
        return self._json(
            METADATA_ENDPOINT + identifier,
            None,
            what=f"the metadata for item {identifier!r}",
        )

    def _json(self, url: str, params: dict | None, *, what: str) -> dict:
        """A GET that retries politely and reports what it was asking for.

        Both a rate-limited response and a maintenance page arrive as a
        200 that is not JSON, so "did it parse?" is the real check and the
        status code is only the first one.
        """
        last = "no attempt was made"
        for index in range(len(RETRY_WAITS) + 1):
            if index:
                time.sleep(RETRY_WAITS[index - 1])
            try:
                response = self.ctx.http.get(url, params or {})
            except RuntimeError as exc:
                # What the broker reports through the SDK channel: a
                # blocked host, or a body over the host's 4 MiB budget.
                last = str(exc)
                continue
            if response.status_code != 200:
                last = f"HTTP {response.status_code}"
                continue
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                last = f"the response was not JSON ({exc})"
                continue
            if isinstance(body, dict):
                return body
            last = f"the response was {type(body).__name__}, not an object"

        raise CensusUnavailable(
            f"could not read {what} from Archive.org after "
            f"{len(RETRY_WAITS) + 1} attempts: {last}. It rate-limits, and a "
            f"rate-limited response arrives as a 200 that is not JSON."
        )


# -- helpers -------------------------------------------------------------


def _digests(entry: dict, directory: str) -> dict[str, str]:
    """Archive.org's published hashes, under the keys `grouping` reads.

    `sha1` and `md5` are strong enough to *merge* two rows outright;
    `crc32` is strong enough only to refuse a merge. That asymmetry is
    `rom_hub.grouping`'s, not this module's -- the job here is to hand over
    what the source published and let the one deduplicator in the codebase
    apply its own ladder to it.
    """
    out = {"directory": directory}
    for key in ("sha1", "md5", "crc32"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip().lower()
    return out


def _skip_reason(entry: dict) -> str:
    name = entry.get("name")
    if isinstance(name, str) and (
        entry.get("source") == BOOKKEEPING_SOURCE or name.endswith(METADATA_SUFFIXES)
    ):
        return SKIP_BOOKKEEPING
    return SKIP_DIRECTORY


def _as_int(value) -> int | None:
    """An integer from a field Archive.org spells inconsistently.

    `size` arrives as a decimal string, `files_count` as a number. A value
    that is neither becomes None, which the host reports as "no declared
    total" -- distinct from zero, and deliberately so.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None
