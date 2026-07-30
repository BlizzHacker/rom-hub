"""Turning a Universal-DB download name into one `FetchFile.filename` accepts.

The host writes this string to disk, so `rom_hub.types.bare_filename`
refuses anything that is not a bare name: no separators, no `..`, no
drive-relative `C:evil.zip`, no Windows device name, nothing ending in a
dot or a space, nothing over 200 characters, and only characters from an
allowlist. That validator runs on the trusted side and is the real
boundary. This module's job is to make sure a *legitimate* name never hits
it.

That second half is not decoration. An over-strict sanitiser in this
codebase once silently dropped every GoodTools `[!]` name, because the
brackets looked like something to strip. Universal-DB has the same shape
of name -- `Open AGB Launcher.zip` has spaces, `SuperHaxagon-3DS-armhf.cia.zip`
has two dots, `3DS_1.0.zip` starts with a digit -- and none of them is an
attack. All 757 download names live in the database today pass through
here unchanged apart from case; nothing legitimate is narrowed away, and
where narrowing was ever needed it would be narrowed *here*, never in the
host's rule.

Two properties matter more than prettiness:

**Deterministic.** The same upstream name always produces the same result,
including when it has to be truncated, because `FetchPlan` rejects two
files that sanitise to the same name and a plan must not depend on
iteration order to be valid.

**Extension-preserving.** Truncation keeps the suffix: RomM routes on it,
and a `.3dsx` that became `.3ds` would be filed as a commercial cartridge
dump of something that is not one.
"""

import posixpath
import re

# Mirrors rom_hub.types._ALLOWED_PUNCTUATION. Everything outside it --
# including the separators and the colon that make a path -- becomes "_".
# The brackets and the bang are *in* the allowlist, deliberately.
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
    # Both separators, because a name may come from a URL path or from a
    # Windows-authored release listing.
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
        # "NUL.cia" opens the null device on Windows and hashes as empty.
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
