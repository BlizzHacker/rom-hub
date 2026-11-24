"""Reading a shop's index.

Detection follows CyberFoil's order. `/api/remote/sections` and then
`/api/shop/sections` are tried for a `sections` response, and the base URL
is then read as a Tinfoil JSON index whose `directories` are followed.

Every request goes through `ctx.http` with the userinfo already removed,
and every message raised from here names the source but never its
credentials.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import unquote

from .names import (
    FORMATS,
    filename_from_url,
    kind_of_title_id,
    parse_filename,
    parse_title_id,
)
from .sources import Source, strip_userinfo

_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")

SECTION_PREFIXES = ("/api/remote", "/api/shop")

#: How many nested `directories` one source may make the plugin read. Each
#: one is a request inside ROMarr's 90 second search budget.
MAX_DIRECTORIES = 32
MAX_DEPTH = 8

_SECTION_KINDS = {
    "base": "base",
    "updates": "update",
    "update": "update",
    "dlc": "dlc",
    "addon": "dlc",
    "add-on": "dlc",
    "add_ons": "dlc",
}
_APP_TYPES = {
    "base": "base",
    "application": "base",
    "upd": "update",
    "update": "update",
    "patch": "update",
    "dlc": "dlc",
    "addon": "dlc",
}
# CyberFoil accepts both the NCM content meta types and 0, 1, 2.
_APP_TYPE_NUMBERS = {0: "base", 0x80: "base", 1: "update", 0x81: "update", 2: "dlc", 0x82: "dlc"}


class SourceError(Exception):
    """One source could not be read. Other sources carry on."""


@dataclass(frozen=True)
class Entry:
    source: str
    url: str
    filename: str
    name: str
    title_id: str | None
    version: int | None
    kind: str | None
    fmt: str
    size: int | None


@dataclass
class _Walk:
    source: Source
    entries: list[Entry]
    seen_urls: set[str]
    seen_indexes: set[str]
    skipped: int = 0


def join_url(base: str, path: str) -> str:
    """Resolve an index URL the way CyberFoil does.

    An absolute URL stands. Anything else is appended to the base the index
    was read from, with one slash between them.
    """
    lowered = path.lower()
    if lowered.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return base + path
    return base + "/" + path


def _http_error(source: Source, url: str, status: int) -> SourceError:
    where = f"{source.name}: {strip_userinfo(url)}"
    if status in (401, 403):
        return SourceError(
            f"{where} answered HTTP {status}. The shop refused the request, so "
            "check the credentials in the source line and that the account has shop access"
        )
    if 300 <= status < 400:
        return SourceError(
            f"{where} answered HTTP {status}, a redirect. The plugin does not follow "
            "redirects for an index, so put the final URL in the source line"
        )
    return SourceError(f"{where} answered HTTP {status}")


def _tinfoil_kind(text: str) -> str:
    """Name a binary TINFOIL index by its flag byte, as far as text allows.

    rom-hub hands the plugin a decoded string, so the high flag bits of an
    encrypted index arrive as a replacement character.
    """
    flag = ord(text[7]) if len(text) > 7 else 0
    if flag > 0x7F:
        return "an encrypted"
    if flag == 0x0D:
        return "a zstd-compressed"
    if flag == 0x0E:
        return "a zlib-compressed"
    return "a binary"


def fetch_text(http, source: Source, url: str) -> tuple[int, str]:
    """GET one URL for this source. Transport failures become a SourceError."""
    clean = strip_userinfo(url)
    try:
        response = http.get(clean, headers=source.headers_for(clean))
    except Exception as exc:
        message = source.redact(str(exc))
        if "exceeded" in message and "limit" in message:
            raise SourceError(
                f"{source.name}: {clean} is larger than rom-hub's 4 MiB response "
                "limit, so this index cannot be read"
            ) from None
        if "allowlist" in message or "blocked request" in message:
            raise SourceError(
                f"{source.name}: rom-hub blocked {clean} because its host is not "
                "granted. Add it to ROM_HUB_NETWORK_SWITCHFOIL and restart ROMarr"
            ) from None
        raise SourceError(f"{source.name}: request to {clean} failed: {message}") from None
    return response.status_code, response.text


def parse_json(source: Source, url: str, text: str):
    body = text.lstrip("\ufeff\x00 \t\r\n")
    if body.startswith("TINFOIL"):
        raise SourceError(
            f"{source.name}: {strip_userinfo(url)} returned {_tinfoil_kind(body)} "
            "TINFOIL index, which this plugin cannot read. Serve the index as plain JSON"
        )
    try:
        data = json.loads(body)
    except ValueError:
        raise SourceError(
            f"{source.name}: {strip_userinfo(url)} did not return JSON. A shop that "
            "returns its web page here does not recognise the client"
        ) from None
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        raise SourceError(
            f"{source.name}: the shop reported an error: {source.redact(data['error'])}"
        )
    return data


def _number(value) -> int | None:
    """A non-negative integer given as a number or as digits, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _app_type(value) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _APP_TYPE_NUMBERS.get(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text.lstrip("-").isdigit():
            return _APP_TYPE_NUMBERS.get(int(text))
        return _APP_TYPES.get(text)
    return None


def _add_entry(walk: _Walk, base: str, item, section_id: str | None = None) -> None:
    """Turn one `files`, `paths` or section item into an Entry, or skip it."""
    if isinstance(item, str):
        item = {"url": item}
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        walk.skipped += 1
        return
    if item.get("app_type") == "CHEAT" or section_id == "cheats":
        return
    raw = item["url"].strip()
    path, _, fragment = raw.partition("#")
    scheme = _SCHEME.match(path)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        # gdrive:, jbod:, ftp:// and the like name storage this plugin cannot reach.
        walk.skipped += 1
        return
    absolute = join_url(base, path)
    # Userinfo written into a shop's own index is dropped. Credentials come
    # from the operator's source line and nowhere else.
    absolute = strip_userinfo(absolute)
    if absolute in walk.seen_urls:
        return
    if isinstance(item.get("name"), str) and item["name"].strip():
        filename = item["name"].strip()
    elif fragment:
        filename = unquote(fragment)
    else:
        filename = filename_from_url(absolute)
    info = parse_filename(filename)
    if info.fmt not in FORMATS:
        walk.skipped += 1
        return
    title_id = parse_title_id(item.get("title_id")) or parse_title_id(item.get("app_id"))
    title_id = title_id or info.title_id
    version = _number(item.get("app_version"))
    version = info.version if version is None else version
    kind = _app_type(item.get("app_type"))
    if kind is None and title_id:
        kind = kind_of_title_id(title_id)
    if kind is None and section_id:
        kind = _SECTION_KINDS.get(section_id.lower())
    kind = kind or info.kind
    walk.seen_urls.add(absolute)
    url = absolute + ("#" + fragment if fragment else "")
    walk.entries.append(
        Entry(
            source=walk.source.name,
            url=url,
            filename=info.filename,
            name=info.name,
            title_id=title_id,
            version=version,
            kind=kind,
            fmt=info.fmt,
            size=_number(item.get("size")),
        )
    )


def _add_files(walk: _Walk, base: str, files) -> None:
    if isinstance(files, list):
        for item in files:
            _add_entry(walk, base, item)
    elif isinstance(files, dict):
        for key, value in files.items():
            if isinstance(value, dict):
                _add_entry(walk, base, {"name": key, **value})
            elif isinstance(value, str):
                _add_entry(walk, base, {"name": key, "url": value})
            elif isinstance(value, list):
                for sub in value:
                    if isinstance(sub, str):
                        _add_entry(walk, base, {"name": key, "url": sub})


def _add_sections(walk: _Walk, base: str, sections) -> bool:
    found = False
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("items"), list):
            continue
        section_id = section.get("id") if isinstance(section.get("id"), str) else None
        found = True
        for item in section["items"]:
            _add_entry(walk, base, item, section_id)
    return found


def _directory_url(entry) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("url", "path", "directory", "href", "location"):
            if isinstance(entry.get(key), str):
                return entry[key]
    return ""


def _walk_tinfoil(http, walk: _Walk, url: str, data, depth: int) -> None:
    if not isinstance(data, dict):
        raise SourceError(f"{walk.source.name}: {strip_userinfo(url)} is not a Tinfoil index")
    base = strip_userinfo(url).rstrip("/")
    if isinstance(data.get("sections"), list):
        _add_sections(walk, base, data["sections"])
    for key in ("files", "paths"):
        if key in data:
            _add_files(walk, base, data[key])
    directories = data.get("directories")
    if not isinstance(directories, list):
        return
    if depth >= MAX_DEPTH:
        raise SourceError(f"{walk.source.name}: directories nest deeper than {MAX_DEPTH} levels")
    for directory in directories:
        path = _directory_url(directory).strip()
        if not path:
            continue
        child = strip_userinfo(join_url(base, path.split("#")[0]))
        if child in walk.seen_indexes:
            continue
        if len(walk.seen_indexes) >= MAX_DIRECTORIES:
            raise SourceError(
                f"{walk.source.name}: the index lists more than {MAX_DIRECTORIES} directories"
            )
        walk.seen_indexes.add(child)
        status, text = fetch_text(http, walk.source, child)
        if not 200 <= status < 300:
            raise _http_error(walk.source, child, status)
        _walk_tinfoil(http, walk, child, parse_json(walk.source, child, text), depth + 1)


def _read_sections(http, walk: _Walk) -> SourceError | None:
    """Try CyberFoil's two sections paths. None means one of them answered."""
    last: SourceError | None = None
    for prefix in SECTION_PREFIXES:
        url = walk.source.url + prefix + "/sections"
        status, text = fetch_text(http, walk.source, url)
        if status == 404:
            continue
        if not 200 <= status < 300:
            last = _http_error(walk.source, url, status)
            continue
        try:
            data = parse_json(walk.source, url, text)
        except SourceError as exc:
            last = exc
            continue
        found = isinstance(data, dict) and isinstance(data.get("sections"), list)
        if found and (
            _add_sections(walk, walk.source.url, data["sections"]) or not data["sections"]
        ):
            return None
        last = SourceError(f"{walk.source.name}: {url} has no sections")
    return last or SourceError(f"{walk.source.name}: neither sections path exists")


def read_source(http, source: Source) -> tuple[list[Entry], int]:
    """Every Switch file a source lists, and how many items were skipped.

    Raises SourceError when the source cannot be read at all.
    """
    walk = _Walk(source, [], set(), {source.url})
    if source.protocol != "tinfoil":
        failure = _read_sections(http, walk)
        if failure is None:
            return walk.entries, walk.skipped
        if source.protocol == "cyberfoil":
            raise failure
    status, text = fetch_text(http, source, source.url)
    if not 200 <= status < 300:
        raise _http_error(source, source.url, status)
    _walk_tinfoil(http, walk, source.url, parse_json(source, source.url, text), 0)
    return walk.entries, walk.skipped
