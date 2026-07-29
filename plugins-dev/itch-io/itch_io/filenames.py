"""Turning an upstream file name into one `FetchFile.filename` accepts.

The host writes this string to disk, so `rom_hub.types.FetchFile` refuses
anything that is not a bare name: no separators, no `..`, no drive-relative
`C:evil.zip`, no Windows device name, nothing ending in a dot or a space,
nothing longer than 200 characters, and only characters from an allowlist.
That validator runs on the trusted side and is the real boundary. This
module's job is to make sure a legitimate file never *hits* it -- a ROM
called `Pokémon Mini/demo (v1.0).gb` is not an attack, and refusing it
would be a bug.

Two properties matter more than prettiness:

**Deterministic.** The same upstream name always produces the same result,
including when it has to be truncated, because `FetchPlan` rejects two
files that sanitise to the same name and a plan must not depend on
iteration order to be valid.

**Extension-preserving.** Truncation keeps the suffix: RomM routes on it,
and a `.gb` that became `.g` is a worse outcome than a shortened title.
"""

import posixpath
import re

# Mirrors rom_hub.types._ALLOWED_PUNCTUATION. Everything outside it --
# including the separators and the colon that make a path -- becomes "_".
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
    # Both separators, because the name may come from a URL path or from a
    # Windows-authored archive listing.
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _RUNS.sub("_", _ALLOWED.sub("_", name))
    # Leading dots and spaces make hidden or oddly-sorted files; trailing
    # ones are refused outright by the host on Windows grounds.
    name = name.strip(". ")
    if not name:
        return fallback

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED_STEMS:
        # "NUL.gb" opens the null device on Windows and hashes as empty.
        stem = "_" + stem

    if ext:
        # Keep the whole extension and give the stem whatever is left. An
        # extension long enough to fill the budget on its own is not an
        # extension, so it is cut too rather than crowding the stem out.
        ext = ext[: MAX_CHARS // 2]
        stem = stem[: MAX_CHARS - len(ext) - 1] or "file"
        name = f"{stem}.{ext}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    return name or fallback
