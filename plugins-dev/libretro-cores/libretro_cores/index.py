"""Reading libretro's `.index-extended`, which is what RetroArch reads.

Every build-target directory on the buildbot carries a file called
`.index-extended` alongside the cores. One line per core:

    2026-07-29 edf888ae mednafen_supergrafx_libretro.so.zip
    <date>     <crc32>  <filename>

That is the whole format. It is used here in preference to parsing the
directory's HTML for two reasons worth stating: it is what RetroArch's own
core updater consumes, so it is maintained rather than incidental; and it
is ~10 KB of text where the rendered index is a JavaScript file browser
whose markup is not a contract with anybody.

**A filename is validated, never repaired.** A line whose filename is not
a plain bare name, or does not carry the suffix this target's cores carry,
is *skipped*. Two different things are being defended against and both
matter:

  * `FetchFile.filename` is a string the host opens for writing, so
    anything that could be read as a path must never get that far. The
    shape accepted below has no separator, no colon and no leading dot, so
    it is a bare name by construction rather than by cleaning.
  * A `.index-extended` entry that does not look like a core is not a core
    -- the directory also holds `.index`, and a future addition should
    show up as one absent core rather than as a mystery download.

Sizes are absent from this format, so `FetchFile.size_bytes` is left
unset. The host learns the real length from the response, and inventing a
number from the crc32 column would be worse than saying nothing.
"""

import re
from dataclasses import dataclass

# One line, exactly. The crc32 column is matched but not used: this plugin
# does not verify it, because it cannot -- the plugin never sees the bytes,
# the host fetches them. Claiming a checksum check that does not happen
# would be the worst of the three options.
_LINE = re.compile(
    r"\A(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<crc>[0-9a-fA-F]{8})\s+(?P<name>\S+)\Z"
)

# What a filename may be. An allowlist of characters and a required
# leading alphanumeric, so no separator, no drive letter, no "..", no
# leading dot, and nothing `rom_hub.types.bare_filename` would refuse.
_FILENAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._+-]*\Z")

# A core file is `<something>_libretro<suffix>`; the stem is what is left
# once the suffix comes off. Stripping a trailing `_libretro` gives the id
# RetroArch and libretro's own documentation use.
_LIBRETRO_STEM = "_libretro"


class IndexError_(Exception):
    """The buildbot index could not be read or made sense of."""


@dataclass(frozen=True)
class CoreEntry:
    """One core in one build target's index."""

    core_id: str
    filename: str
    #: The date the buildbot last rebuilt it, as printed. A build stamp,
    #: not a version -- nightly cores do not have versions, and calling
    #: this one would imply a stability it does not have.
    built: str


def core_id_for(filename: str, suffix: str) -> str | None:
    """The core id for a filename, or None if it is not a core file.

    `2048_libretro.so.zip` with suffix `.so.zip` is `2048`. A stem that
    does not end in `_libretro` keeps its whole name -- the buildbot
    genuinely ships some (`reminiscence_libretro_ios.dylib.zip`), and an
    id that matches the file is better than dropping the core or guessing
    where to cut.
    """
    if not filename.endswith(suffix):
        return None
    stem = filename[: -len(suffix)]
    if stem.endswith(_LIBRETRO_STEM):
        stem = stem[: -len(_LIBRETRO_STEM)]
    return stem or None


def parse_index(text: str, suffix: str) -> list[CoreEntry]:
    """Every usable core in a `.index-extended` body, sorted by core id.

    Sorted here rather than by a caller because the buildbot emits them in
    build order, which changes nightly: an operator reading `cores list`
    twice should not see the catalogue shuffle. Sorting also makes the
    fixture-backed tests assert on a stable sequence.
    """
    entries: dict[str, CoreEntry] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _LINE.match(line)
        if not match:
            continue
        filename = match.group("name")
        if not _FILENAME.match(filename):
            continue
        core_id = core_id_for(filename, suffix)
        if core_id is None or not _FILENAME.match(core_id):
            continue
        # A repeated id would make `cores install` ambiguous. The index
        # has not shown one, and if it ever does the first line wins so
        # the answer stays deterministic instead of depending on order.
        entries.setdefault(
            core_id,
            CoreEntry(core_id=core_id, filename=filename, built=match.group("date")),
        )

    if not entries:
        # An empty result is never "the buildbot has no cores". It means
        # the body was not an index -- a redirect to an error page, a
        # proxy's interstitial, or a format change -- and saying so is the
        # difference between a fixable report and a silent empty list.
        raise IndexError_(
            "libretro's buildbot returned a body with no core entries in it. "
            "That is not an empty build target: `.index-extended` always "
            "lists the cores it has, so a body without a single parseable "
            "line means something other than the index was served."
        )
    return sorted(entries.values(), key=lambda e: e.core_id)
