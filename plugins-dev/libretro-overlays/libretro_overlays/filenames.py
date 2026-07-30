"""Turning an upstream filename into one `FetchFile.filename` accepts.

The same job `libretro-cores`' module of this name does, and held to the
same two properties, for the same reasons.

**Deterministic.** The same upstream name always produces the same
result, including when truncated, because `FetchPlan` refuses two files
whose names collide and a plan must not depend on iteration order to be
valid.

**Extension-preserving.** An overlay is a `.cfg` beside its `.png`, and
RetroArch loads each by extension; a truncation that ate one would leave
a file nothing opens.

What differs from the cores version is what the input looks like. A
buildbot core filename is already close to bare. These names come out of
a repository tree -- `DualShock_Full.cfg`, `flat-n64.cfg`, `gb-4k.cfg`,
and the `.png` files beside them -- and the punctuation they carry is
mostly punctuation `rom_hub.types.bare_filename` permits, which this
module therefore leaves alone. The characters it does replace are the
ones that make a path.
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


def safe_filename(raw: str, fallback: str = "overlay.cfg") -> str:
    """A bare, host-acceptable filename derived from `raw`.

    `raw` may be a path within a repository tree; only the last component
    is used. The host re-validates the result with `bare_filename` and
    `dest_in_job_dir` regardless -- this is the plugin being well-behaved,
    not the thing that makes it safe.
    """
    if not isinstance(raw, str):
        return fallback
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _RUNS.sub("_", _ALLOWED.sub("_", name))
    # Leading dots and spaces make hidden or oddly-sorted files; trailing
    # ones are refused outright by the host on Windows grounds.
    name = name.strip(". ")
    if not name:
        return fallback

    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""

    if stem.upper() in _RESERVED_STEMS:
        # "NUL.cfg" opens the null device on Windows and writes nowhere.
        stem = "_" + stem

    if suffix:
        keep = MAX_CHARS - len(suffix) - 1
        stem = stem[:keep] if keep > 0 else stem[:1]
        name = f"{stem}.{suffix}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    return name or fallback
