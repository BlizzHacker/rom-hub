"""Safe bare filename derivation conforming to ROM Hub requirements."""

import posixpath
import re

_ALLOWED = re.compile(r"[^\w .\-()\[\]+,'!&~@#=]", re.UNICODE)
_RUNS = re.compile(r"_{2,}")

_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

MAX_CHARS = 200
FALLBACK = "guide.json"


def safe_filename(raw: str, fallback: str = FALLBACK) -> str:
    """Derive a bare, host-acceptable filename from `raw`."""
    if not isinstance(raw, str):
        return fallback
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _RUNS.sub("_", _ALLOWED.sub("_", name))
    name = name.strip(". ")
    if not name:
        return fallback

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED_STEMS:
        stem = "_" + stem

    if ext:
        ext = ext[: MAX_CHARS // 2]
        stem = stem.strip(". ")[: MAX_CHARS - len(ext) - 1] or "file"
        name = f"{stem}.{ext}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    return name or fallback
