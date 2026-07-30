"""Turning a GitHub asset name into one `FetchFile.filename` accepts.

Every asset name this plugin has seen is already a bare, boring name --
`melonDS-1.1-windows-x86_64.zip` -- so in practice this module changes
nothing. That is on purpose and it is not redundant. The name comes out of
a JSON document fetched from a third party, and `FetchFile.filename` is
the string the **host** opens for writing; "upstream has always been
well-behaved" is not a property this code should depend on. The host would
refuse a bad name anyway, but it would refuse it as an opaque validation
error at install time rather than as a file with a predictable name.

The two properties that matter are the ones every other plugin's
sanitiser holds to:

**Deterministic.** The same asset name always yields the same result,
including when truncated, because `FetchPlan` refuses two files whose
names collide and a plan must not depend on iteration order to be valid.

**Extension-preserving.** An operator unpacks or runs this file by its
extension. A truncation that ate `.AppImage` would leave something nobody
can identify, and `.tar.xz` is two components of one suffix -- cutting it
back to `.tar` would name a file that is not a tar.
"""

import posixpath
import re

# Mirrors rom_hub.types._ALLOWED_PUNCTUATION. Everything outside it --
# including the separators and the colon that make a path -- becomes "_".
_ALLOWED = re.compile(r"[^\w .\-()\[\]+,'!&~@#=]", re.UNICODE)

# Runs of underscores are deliberately left alone. Collapsing them is a
# cosmetic tidy-up that renames a file whose author chose that name, and
# the point of this function is safety rather than tidiness.

_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

MAX_CHARS = 200
FALLBACK = "emulator.bin"

# Kept whole when the name has to be shortened. Longest first, so
# ".tar.xz" is recognised before ".xz" would be. These are exactly the
# suffixes the projects in `projects.py` publish.
_SUFFIXES = (
    ".tar.xz",
    ".tar.gz",
    ".AppImage",
    ".appimage",
    ".zip",
    ".7z",
    ".dmg",
    ".exe",
)


def safe_filename(raw: str, fallback: str = FALLBACK) -> str:
    """A bare, host-acceptable filename derived from `raw`."""
    if not isinstance(raw, str):
        return fallback
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _ALLOWED.sub("_", name)
    # Leading dots and spaces make hidden or oddly-sorted files; trailing
    # ones are refused outright by the host on Windows grounds.
    name = name.strip(". ")
    if not name:
        return fallback

    lowered = name.lower()
    suffix = ""
    for candidate in _SUFFIXES:
        if lowered.endswith(candidate.lower()):
            suffix = name[-len(candidate):]
            break
    stem = name[: -len(suffix)] if suffix else name

    if stem.upper() in _RESERVED_STEMS:
        # "NUL.zip" opens the null device on Windows and writes nowhere.
        stem = "_" + stem

    if suffix:
        stem = stem[: MAX_CHARS - len(suffix)] or "emulator"
        name = f"{stem}{suffix}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    return name or fallback
