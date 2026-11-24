"""Title IDs, versions, content types and formats from shop filenames.

Shops name files in the common `Name [0100ABCD12340000][v65536].nsz` form.
A title ID ending in `000` is a base title, `800` is its update, and any
other ending is DLC.
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

FORMATS = ("nsp", "nsz", "xci", "xcz")
KINDS = ("base", "update", "dlc")
KIND_LABELS = {"base": "BASE", "update": "UPD", "dlc": "DLC"}

_TITLE_ID = re.compile(r"\[(?:0x)?([0-9A-Fa-f]{16})\]")
_VERSION = re.compile(r"\[v(\d{1,10})\]", re.IGNORECASE)
_BRACKETS = re.compile(r"\s*\[[^\]]*\]")
_TAG_KINDS = {
    "base": "base",
    "upd": "update",
    "update": "update",
    "dlc": "dlc",
}
_HEX16 = re.compile(r"(?:0x)?([0-9A-Fa-f]{16})")

# What rom-hub's `bare_filename` accepts besides letters and digits.
_FILENAME_PUNCTUATION = frozenset(" .-_()[]+,'!&~@#=")
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_MAX_FILENAME = 200


@dataclass(frozen=True)
class FileInfo:
    filename: str
    name: str
    title_id: str | None
    version: int | None
    kind: str | None
    fmt: str | None


def last_component(path: str) -> str:
    """The final component of a path under either Windows or POSIX rules."""
    return PurePosixPath(PureWindowsPath(path).name).name


def filename_from_url(url: str) -> str:
    """The filename a shop gives a file: the fragment, else the last path segment."""
    _, _, fragment = url.partition("#")
    if fragment:
        return last_component(unquote(fragment))
    path = urlsplit(url).path
    return last_component(unquote(path))


def parse_title_id(value) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 < value < 1 << 64:
            return f"{value:016X}"
        return None
    if isinstance(value, str):
        match = _HEX16.fullmatch(value.strip())
        if match:
            return match.group(1).upper()
    return None


def kind_of_title_id(title_id: str) -> str:
    suffix = int(title_id, 16) & 0xFFF
    if suffix == 0x000:
        return "base"
    if suffix == 0x800:
        return "update"
    return "dlc"


def base_title_id(title_id: str) -> str:
    """The base title a base, update or DLC title ID belongs to."""
    value = int(title_id, 16)
    kind = kind_of_title_id(title_id)
    if kind == "update":
        value ^= 0x800
    elif kind == "dlc":
        value = (value - 0x1000) & ~0xFFF
    return f"{value:016X}"


def format_of(filename: str) -> str | None:
    suffix = filename.rpartition(".")[2].lower() if "." in filename else ""
    return suffix if suffix in FORMATS else None


def parse_filename(filename: str) -> FileInfo:
    filename = last_component(filename)
    fmt = format_of(filename)
    stem = filename[: -(len(fmt) + 1)] if fmt else filename
    title_ids = _TITLE_ID.findall(stem)
    title_id = title_ids[0].upper() if title_ids else None
    version_match = _VERSION.search(stem)
    version = int(version_match.group(1)) if version_match else None
    kind = kind_of_title_id(title_id) if title_id else None
    if kind is None:
        for tag in re.findall(r"\[([A-Za-z]+)\]", stem):
            kind = _TAG_KINDS.get(tag.lower())
            if kind:
                break
    name = _BRACKETS.sub("", stem).strip() or stem.strip() or filename
    return FileInfo(filename, name, title_id, version, kind, fmt)


def safe_filename(filename: str) -> str:
    """A name rom-hub's `bare_filename` accepts, keeping the extension.

    A path is cut to its last component, `:` becomes ` -` as Switch tools
    usually write it, and any other refused character becomes `_`.
    """
    name = last_component(filename).replace(":", " -")
    name = "".join(c if c.isalnum() or c in _FILENAME_PUNCTUATION else "_" for c in name)
    name = re.sub(r"\s+", " ", name).strip()
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.rstrip(". ")
    if len(stem) + len(dot) + len(ext) > _MAX_FILENAME:
        stem = stem[: _MAX_FILENAME - len(dot) - len(ext)].rstrip(". ")
    if not stem.strip(". ") or stem.split(".")[0].upper() in _RESERVED_STEMS:
        stem = f"_{stem}"
    return f"{stem}{dot}{ext}".rstrip(". ")
