"""Turning an upstream filename into one FetchFile.filename accepts."""

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
FALLBACK = "download.bin"


def safe_filename(raw: str, fallback: str = FALLBACK) -> str:
    """A bare, host-acceptable filename derived from `raw`."""
    if not isinstance(raw, str):
        return fallback
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _RUNS.sub("_", _ALLOWED.sub("_", name))
    name = name.strip(". ")
    if not name:
        return fallback

    first_stem = name.split(".")[0]
    if first_stem.upper() in _RESERVED_STEMS:
        name = "_" + name

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""

    if ext:
        ext = ext[: MAX_CHARS // 2]
        stem = stem[: MAX_CHARS - len(ext) - 1] or "file"
        name = f"{stem}.{ext}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    first_stem = name.split(".")[0]
    if first_stem.upper() in _RESERVED_STEMS:
        name = "_" + name

    return name or fallback


def make_unique_filenames(
    files: list[tuple[str, str, int | None]],
) -> list[tuple[str, str, int | None]]:
    """Ensure filenames in a list of (url, filename, size_bytes) are unique and safe."""
    seen: dict[str, int] = {}
    result = []
    for url, raw_name, size in files:
        base = safe_filename(raw_name)
        stem, dot, ext = base.rpartition(".")
        if not dot:
            stem, ext = base, ""

        cand = base
        count = seen.get(cand.casefold(), 0)
        while cand.casefold() in seen:
            count += 1
            cand = f"{stem}_{count}.{ext}" if ext else f"{stem}_{count}"
            cand = safe_filename(cand)
        seen[cand.casefold()] = count
        result.append((url, cand, size))
    return result
