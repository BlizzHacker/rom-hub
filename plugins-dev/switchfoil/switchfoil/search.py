"""The `search` capability."""

import re
import sys

from rom_hub_sdk import SearchProvider, SearchResult

from .names import KIND_LABELS, base_title_id
from .shops import Entry, SourceError, read_source
from .sources import parse_sources
from .titledb import TitleDBError, load_latest

PLATFORM = "switch"
_TITLE_ID_QUERY = re.compile(r"(?:0x)?([0-9A-Fa-f]{16})")
_KIND_ORDER = {"base": 0, "update": 1, "dlc": 2, None: 3}


class SearchRefused(Exception):
    pass


def warn(message: str) -> None:
    # rom-hub keeps the tail of plugin stderr for diagnostics.
    print(f"switchfoil: {message}", file=sys.stderr)


def matches(entry: Entry, query: str) -> bool:
    text = query.strip()
    hexmatch = _TITLE_ID_QUERY.fullmatch(text)
    if hexmatch:
        wanted = hexmatch.group(1).upper()
        if not entry.title_id:
            return False
        if entry.title_id == wanted:
            return True
        return base_title_id(wanted) == wanted and base_title_id(entry.title_id) == wanted
    name = entry.name.casefold()
    return all(word in name for word in text.casefold().split())


def human_size(size: int) -> str:
    for unit, scale in (("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} B"


def _label(entry: Entry, other: Entry) -> str:
    if entry.source == other.source:
        return other.fmt.upper()
    if entry.fmt == other.fmt:
        return other.source
    return f"{other.source} {other.fmt.upper()}"


def compare_duplicates(entries: list[Entry]) -> dict[int, str]:
    """A size comparison for each entry sharing a title ID and version with another.

    Keyed by position in `entries`. Up to two others are named per entry.
    """
    groups: dict[tuple, list[int]] = {}
    for index, entry in enumerate(entries):
        if entry.title_id and entry.version is not None:
            groups.setdefault((entry.title_id, entry.version), []).append(index)
    notes: dict[int, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        for index in members:
            entry = entries[index]
            parts = []
            for other_index in members:
                other = entries[other_index]
                if other_index == index:
                    continue
                if entry.size is None or other.size is None:
                    parts.append(f"also on {_label(entry, other)}")
                elif entry.size == other.size:
                    parts.append(f"same size as {_label(entry, other)}")
                else:
                    delta = human_size(abs(entry.size - other.size))
                    word = "smaller" if entry.size < other.size else "larger"
                    parts.append(f"{delta} {word} than {_label(entry, other)}")
            if len(parts) > 2:
                more = len(parts) - 2
                parts = [*parts[:2], f"{more} more {'copy' if more == 1 else 'copies'}"]
            notes[index] = ", ".join(parts)
    return notes


def title_for(entry: Entry, notes: list[str]) -> str:
    tags = ""
    if entry.title_id:
        tags += f"[{entry.title_id}]"
    if entry.version is not None:
        tags += f"[v{entry.version}]"
    words = [entry.name]
    if tags:
        words.append(tags)
    if entry.kind:
        words.append(KIND_LABELS[entry.kind])
    words.append(entry.fmt.upper())
    title = " ".join(words) + f" | {entry.source}"
    for note in notes:
        title += f" | {note}"
    return title


def source_id_for(entry: Entry) -> str:
    """`name|url`. The name holds no `|`, and the URL holds no credentials."""
    return f"{entry.source}|{entry.url}"


class Search(SearchProvider):
    def search(self, query: str, platform: str | None, limit: int) -> list[SearchResult]:
        if platform not in (None, "", PLATFORM):
            return []
        config = self.ctx.config or {}
        sources, problems = parse_sources(config.get("sources") or [])
        for problem in problems:
            warn(problem)
        if not sources:
            raise SearchRefused(
                "no usable source is configured. Add a line of the form "
                "name|https://your-server.example to the sources setting"
            )

        entries: list[Entry] = []
        failures: list[str] = []
        for source in sources:
            try:
                found, skipped = read_source(self.ctx.http, source)
            except SourceError as exc:
                failures.append(str(exc))
                continue
            if skipped:
                warn(
                    f"{source.name}: skipped {skipped} items that are not NSP, NSZ, XCI or XCZ "
                    "files at an http or https URL"
                )
            entries.extend(e for e in found if matches(e, query))

        for message in failures:
            warn(f"source skipped. {message}")
        if failures and len(failures) == len(sources):
            raise SearchRefused("every source failed. " + "; ".join(failures))

        entries.sort(
            key=lambda e: (
                e.name.casefold(),
                e.title_id or "",
                _KIND_ORDER.get(e.kind, 3),
                -(e.version or 0),
                e.fmt,
            )
        )

        latest: dict[str, int] = {}
        if config.get("titledb", True) and any(
            e.kind == "update" and e.title_id and e.version is not None for e in entries
        ):
            try:
                latest = load_latest(self.ctx.http)
            except TitleDBError as exc:
                warn(str(exc))

        duplicates = compare_duplicates(entries)
        source_urls = {s.name: s.url for s in sources}
        results = []
        for index, entry in enumerate(entries[: max(limit, 0)]):
            notes = []
            if index in duplicates:
                notes.append(duplicates[index])
            newest = None
            if entry.kind == "update" and entry.title_id and entry.version is not None:
                newest = latest.get(base_title_id(entry.title_id))
                if newest is not None and entry.version < newest:
                    notes.append(f"behind latest v{newest}")
            extra = {
                "source": entry.source,
                "filename": entry.filename,
                "format": entry.fmt,
            }
            if entry.title_id:
                extra["title_id"] = entry.title_id
            if entry.version is not None:
                extra["version"] = str(entry.version)
            if entry.kind:
                extra["type"] = entry.kind
            if entry.size is not None:
                extra["size"] = str(entry.size)
            if index in duplicates:
                extra["comparison"] = duplicates[index]
            if newest is not None:
                extra["latest_version"] = str(newest)
            results.append(
                SearchResult(
                    source_id=source_id_for(entry),
                    title=title_for(entry, notes),
                    platform=PLATFORM,
                    size_bytes=entry.size,
                    url=source_urls[entry.source],
                    extra=extra,
                )
            )
        return results
