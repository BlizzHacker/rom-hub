"""Turning an archive's filename into one `FetchFile.filename` accepts.

This one does more work than its siblings in other plugins, because
demoscene filenames are not tidy. The sample of real scene.org paths
includes `2.8k_nuance__onslaught.zip` (fine), `__booze_design.zip`
(leading underscores), percent-encoded spaces and exclamation marks that
survive decoding, and -- on the archives this plugin does not use -- names
with no extension at all.

The properties held to are the same two every plugin's sanitiser holds to:

**Deterministic.** The same upstream name always produces the same
result, including when truncated, because `FetchPlan` refuses two files
whose names collide and a plan must not depend on iteration order.

**Extension-preserving.** A `.d64` is a disk image and a `.zip` is an
archive, and the emulator on the other end decides which it is by the
extension. Truncation keeps the suffix.

A name that sanitises away to nothing gets `FALLBACK` rather than an
exception: the production is real and downloadable, and refusing an
import because an archive named its file oddly would be a worse outcome
than a file called `production.zip`.
"""

import posixpath
import re

# Mirrors rom_hub.types._ALLOWED_PUNCTUATION. Everything outside it --
# including the separators and the colon that make a path -- becomes "_".
_ALLOWED = re.compile(r"[^\w .\-()\[\]+,'!&~@#=]", re.UNICODE)

# A run of underscores is deliberately NOT collapsed. The obvious tidy-up
# -- `_{2,}` -> `_` -- corrupts real names: scene.org carries
# `2.8k_nuance__onslaught.zip` and `__booze_design.zip`, where the double
# underscore is the archive's own separator and not an artefact of
# anything this function did. Renaming somebody's file to make it prettier
# is not this function's job; making it safe is.

_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

MAX_CHARS = 200
FALLBACK = "production.zip"

# The longest suffix worth keeping whole. Long enough for `.tar.gz`,
# short enough that a name like `game.v1.2.final` does not lose its tail
# to something that only looks like an extension.
_MAX_SUFFIX = 8


def safe_filename(raw: str, fallback: str = FALLBACK) -> str:
    """A bare, host-acceptable filename derived from `raw`."""
    if not isinstance(raw, str):
        return fallback
    # `replace("\\", "/")` first: a backslash is a separator on Windows
    # and would otherwise survive basename() on POSIX.
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _ALLOWED.sub("_", name)
    # Leading dots and spaces make hidden or oddly-sorted files; trailing
    # ones are refused outright by the host on Windows grounds.
    name = name.strip(". ")
    if not name:
        return fallback

    stem, dot, extension = name.rpartition(".")
    if dot and stem and 1 <= len(extension) <= _MAX_SUFFIX:
        suffix = "." + extension
    else:
        stem, suffix = name, ""

    if stem.upper() in _RESERVED_STEMS:
        # "NUL.zip" opens the null device on Windows and writes nowhere.
        stem = "_" + stem

    if suffix:
        stem = stem[: MAX_CHARS - len(suffix)] or "production"
        name = f"{stem}{suffix}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    return name or fallback
