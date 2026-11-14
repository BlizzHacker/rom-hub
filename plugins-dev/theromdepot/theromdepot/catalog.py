"""Read and validate a browser-saved ``/api/getContents`` document.

The live application consumes a recursive tree whose nodes carry ``type``,
``name``, ``path``, ``size``, ``mtime`` and ``children``.  The parser also
accepts ROMarr Capture's normalized ``{"items": [...]}`` export so the same
plugin works whether the browser saved the API response verbatim or exported
the already-normalized capture.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .platforms import platform_from_path

BASE = "https://theromdepot.com"
CATALOGUE_URL = f"{BASE}/api/getContents"
DOWNLOAD_PREFIX = f"{BASE}/api/download/"
MAX_CATALOG_BYTES = 128 * 1024 * 1024
MAX_TREE_DEPTH = 64
MAX_ENTRIES = 1_000_000

_KNOWN_EXTENSIONS = frozenset({
    ".zip", ".7z", ".rar", ".gz", ".z64", ".n64", ".v64", ".sfc",
    ".smc", ".nes", ".gba", ".gb", ".gbc", ".nds", ".3ds", ".cia",
    ".iso", ".chd", ".bin", ".cue", ".rom", ".md", ".gen", ".sms",
    ".gg", ".a26", ".a52", ".a78", ".lnx", ".j64", ".c64", ".d64",
    ".adf", ".gdi", ".cdi", ".pbp", ".rvz", ".wbfs", ".wad",
})
_REGION_WORDS = frozenset({
    "usa", "us", "europe", "eu", "japan", "jp", "world", "asia",
    "australia", "brazil", "canada", "france", "germany", "korea",
    "spain", "italy", "netherlands", "sweden",
})


class CatalogUnavailable(RuntimeError):
    """The operator has not supplied a readable, valid browser export."""


class InvalidEntry(ValueError):
    """A file node cannot safely become an id or download URL."""


@dataclass(frozen=True)
class Entry:
    path: str
    name: str
    title: str
    platform: str
    platform_label: str
    size_bytes: int
    region: str = ""
    mtime: str = ""

    @property
    def download_url(self) -> str:
        return download_url(self.path)

    @property
    def search_text(self) -> str:
        return " ".join((
            self.title, self.name, self.platform, self.platform_label,
            self.region, self.path,
        )).casefold()


def clean_path(value: object) -> str:
    """Validate an API path and return its canonical slash spelling."""

    raw = str(value or "").strip().replace("\\", "/")
    # A single leading slash is a harmless root marker; a network-path style
    # prefix and every other URL-shaped value are not catalogue paths.
    if raw.startswith("//") or "://" in raw:
        raise InvalidEntry("path must be relative to The ROM Depot")
    raw = raw.lstrip("/")
    if not raw or len(raw) > 3000 or "?" in raw or "#" in raw:
        raise InvalidEntry("path is empty, too long, or contains URL syntax")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise InvalidEntry("path contains a control character")
    parts = raw.split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise InvalidEntry("path contains an empty or traversal component")
    if re.fullmatch(r"[A-Za-z]:", parts[0]):
        raise InvalidEntry("path is an absolute drive path")
    return "/".join(parts)


def download_url(path: object) -> str:
    """Build the site's own authenticated download route, segment by segment."""

    clean = clean_path(path)
    return DOWNLOAD_PREFIX + "/".join(quote(part, safe="") for part in clean.split("/"))


def display_title(name: object) -> str:
    """Remove one known ROM/archive extension without rewriting the title."""

    text = str(name or "").strip()
    suffix = Path(text).suffix.casefold()
    return text[:-len(suffix)] if suffix in _KNOWN_EXTENSIONS else text


def _size(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _region(path: str, stated: object = "") -> str:
    direct = str(stated or "").strip()
    if direct:
        return direct
    for component in path.split("/")[1:-1]:
        if component.casefold() in _REGION_WORDS:
            return component
    return ""


def _entry(path: object, name: object, size: object, *, platform: object = "",
           title: object = "", region: object = "", mtime: object = "") -> Entry | None:
    try:
        safe_path = clean_path(path)
    except InvalidEntry:
        return None
    size_bytes = _size(size)
    if size_bytes is None:
        return None
    filename = str(name or safe_path.rsplit("/", 1)[-1]).strip()
    if not filename or filename != safe_path.rsplit("/", 1)[-1]:
        # A mismatched display name is untrusted decoration.  The path is what
        # the API actually downloads, so derive the filename from it.
        filename = safe_path.rsplit("/", 1)[-1]
    resolved = platform_from_path(safe_path, platform)
    if resolved is None:
        return None
    slug, source_label = resolved
    shown = str(title or "").strip() or display_title(filename)
    if not shown:
        return None
    return Entry(
        path=safe_path,
        name=filename,
        title=shown,
        platform=slug,
        platform_label=source_label,
        size_bytes=size_bytes,
        region=_region(safe_path, region),
        mtime=str(mtime or "").strip(),
    )


def _normalised_items(payload: object) -> Iterable[dict[str, Any]] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    items = payload["items"]
    if not items:
        return ()
    if not any(isinstance(item, dict) and ("id" in item or "path" in item)
               for item in items):
        return None
    return (item for item in items if isinstance(item, dict))


def entries_from(payload: object) -> list[Entry]:
    """Flatten an API tree or normalized capture into safe, mapped files."""

    entries: list[Entry] = []
    seen: set[str] = set()

    normalised = _normalised_items(payload)
    if normalised is not None:
        for item in normalised:
            found = _entry(
                item.get("id") or item.get("path"),
                item.get("name") or item.get("filename"),
                item.get("size") if "size" in item else item.get("size_bytes"),
                platform=item.get("platform") or item.get("console") or item.get("system"),
                title=item.get("title"), region=item.get("region"),
                mtime=item.get("mtime"),
            )
            if found and found.path not in seen:
                seen.add(found.path)
                entries.append(found)
        return entries

    # Iterative, bounded walk: a malicious or corrupt export cannot recurse
    # the process to death, and JSON cannot introduce object cycles.
    stack: list[tuple[object, tuple[str, ...], int]] = [(payload, (), 0)]
    while stack:
        node, trail, depth = stack.pop()
        if depth > MAX_TREE_DEPTH:
            raise CatalogUnavailable(
                f"catalogue tree exceeds the {MAX_TREE_DEPTH}-level safety limit"
            )
        if isinstance(node, list):
            for child in reversed(node):
                stack.append((child, trail, depth + 1))
            continue
        if not isinstance(node, dict):
            continue

        kind = str(node.get("type") or "").strip().casefold()
        name = str(node.get("name") or node.get("filename") or "").strip()
        raw_path = node.get("path") or node.get("fullPath") or node.get("relativePath")
        has_children = any(isinstance(node.get(key), (list, dict))
                           for key in ("children", "contents", "nodes", "entries"))
        is_file = kind == "file" or (
            kind not in ("directory", "dir", "folder")
            and bool(name or raw_path) and _size(node.get("size")) is not None
            and not has_children
        )
        if is_file:
            path = raw_path or "/".join((*trail, name))
            found = _entry(
                path, name, node.get("size"),
                platform=node.get("platform") or node.get("console") or node.get("system"),
                title=node.get("title"), region=node.get("region"),
                mtime=node.get("mtime"),
            )
            if found and found.path not in seen:
                seen.add(found.path)
                entries.append(found)
                if len(entries) > MAX_ENTRIES:
                    raise CatalogUnavailable(
                        f"catalogue exceeds the {MAX_ENTRIES}-file safety limit"
                    )
            continue

        next_trail = trail
        if kind in ("directory", "dir", "folder") and name not in ("", "/"):
            next_trail = (*trail, name)

        traversed: set[str] = set()
        for key in ("children", "contents", "items", "files", "nodes", "entries"):
            value = node.get(key)
            if isinstance(value, (list, dict)):
                traversed.add(key)
                stack.append((value, next_trail, depth + 1))
        # API wrappers are cheap to tolerate.  Descend into otherwise unknown
        # containers too, while never re-walking a declared child field.
        if not kind:
            for key, value in node.items():
                if key not in traversed and isinstance(value, (list, dict)):
                    stack.append((value, next_trail, depth + 1))

    entries.sort(key=lambda item: item.path.casefold())
    return entries


def load_catalog(path: object) -> list[Entry]:
    """Read one browser export with operator-facing failure messages."""

    raw_path = str(path or "").strip()
    if not raw_path:
        raise CatalogUnavailable(
            "catalog_path is not configured; while logged in to The ROM Depot, "
            "save https://theromdepot.com/api/getContents and point this plugin "
            "at that JSON file"
        )
    source = Path(raw_path).expanduser()
    try:
        stat = source.stat()
    except OSError as exc:
        raise CatalogUnavailable(f"cannot read catalogue {source}: {exc}") from exc
    if stat.st_size > MAX_CATALOG_BYTES:
        raise CatalogUnavailable(
            f"catalogue is {stat.st_size} bytes; limit is {MAX_CATALOG_BYTES}"
        )
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogUnavailable(f"cannot decode catalogue {source}: {exc}") from exc
    entries = entries_from(payload)
    if not entries:
        raise CatalogUnavailable(
            "catalogue contains no safe files on a recognized ROMarr platform; "
            "confirm the saved page is /api/getContents and that the browser was logged in"
        )
    return entries
