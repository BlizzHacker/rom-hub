"""Turning a buildbot filename into one `FetchFile.filename` accepts.

`index.parse_index` already refuses anything that is not a plain bare
name, so in practice every name reaching this module passes it unchanged.
That is on purpose and it is not redundant: validation and sanitisation
answer different questions, and this codebase keeps the answer to "what
does the host actually open for writing?" in one obvious place per plugin
rather than spread across a parser's regex.

The properties that matter are the same two every other plugin's
sanitiser holds to:

**Deterministic.** The same upstream name always produces the same
result, including when truncated, because `FetchPlan` refuses two files
whose names collide and a plan must not depend on iteration order to be
valid.

**Extension-preserving.** A core arrives as `<name>.so.zip`; the host
writes it and the operator unzips it. A truncation that ate `.zip` would
leave a file nothing opens.
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
FALLBACK = "core.zip"

# Kept whole when the name has to be shortened. A libretro core file ends
# in one of these three; losing the ".zip" would leave the operator a file
# their unzipper does not recognise.
_COMPOUND_SUFFIXES = (".so.zip", ".dll.zip", ".dylib.zip")


def safe_filename(raw: str, fallback: str = FALLBACK) -> str:
    """A bare, host-acceptable filename derived from `raw`."""
    if not isinstance(raw, str):
        return fallback
    name = posixpath.basename(raw.replace("\\", "/").strip())
    name = _RUNS.sub("_", _ALLOWED.sub("_", name))
    # Leading dots and spaces make hidden or oddly-sorted files; trailing
    # ones are refused outright by the host on Windows grounds.
    name = name.strip(". ")
    if not name:
        return fallback

    suffix = next((s for s in _COMPOUND_SUFFIXES if name.endswith(s)), "")
    stem = name[: -len(suffix)] if suffix else name

    if stem.upper() in _RESERVED_STEMS:
        # "NUL.so.zip" opens the null device on Windows and writes nowhere.
        stem = "_" + stem

    if suffix:
        stem = stem[: MAX_CHARS - len(suffix)] or "core"
        name = f"{stem}{suffix}"
    else:
        name = stem[:MAX_CHARS]

    name = name.strip(". ")
    return name or fallback
