#!/usr/bin/env python
"""Regenerate `libretro_cores/coreinfo.py` from libretro's own `.info` files.

    python scripts/render_core_info.py

libretro publishes one `.info` file per core in `libretro/libretro-core-info`
(MIT, verified by reading the repository's own COPYING). Each one states what
the core emulates, which file extensions it loads, what BIOS it needs and
under what licence it is distributed -- the four things `rom-hub cores list`
could not previously say, because the buildbot's `.index-extended` carries a
filename, a date and a crc32 and nothing else.

**Why this is generated into the plugin rather than fetched at list time.**
There are 306 `.info` files and no way to get more than one per request:
GitHub's Trees API gives their names and sizes but not their contents, its
GraphQL API needs POST and a token, and `ctx.http` is GET-only text. A
`cores list` that made 306 requests would be a catalogue nobody waits for.
The table it would build changes when libretro adds a core, which is roughly
never for an existing one -- so it is generated, checked in, dated, and
`tests/test_libretro_cores.py` pins its shape.

The `.info` file for the specific core an operator installs *is* fetched
live, by `plan()`, and installed beside the core: see `libretro_cores/cores.py`.
So the file RetroArch actually reads is always current, and only the
catalogue's summary is a snapshot.

**This replaces a hand-kept table.** `systems.py` mapped 106 core ids to a
system by hand and said so honestly -- "a core that is not in it gets
`system=None`". That was the right answer while libretro published the
mapping only inside a zip. It is not the right answer now that the same
data is 306 plain-text files in a public repository.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

OWNER = "libretro"
REPO = "libretro-core-info"
REF = "master"
TREE = f"https://api.github.com/repos/{OWNER}/{REPO}/git/trees/{REF}"
RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{REF}/"

OUT = (
    Path(__file__).resolve().parents[1]
    / "plugins-dev"
    / "libretro-cores"
    / "libretro_cores"
    / "coreinfo.py"
)

#: `key = "value"` and `key = 3`, which is the whole of the `.info` format.
_LINE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*"?(.*?)"?\s*$')

#: The example file is documentation, not a core.
_SKIP = {"00_example_libretro.info"}


def parse_info(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def core_id(filename: str) -> str:
    """`snes9x_libretro.info` -> `snes9x`, matching the buildbot's ids.

    `index.core_id_for` strips the same `_libretro` suffix off the
    buildbot's filenames, so the two agree by construction. A test asserts
    the overlap rather than assuming it.
    """
    stem = filename[: -len(".info")]
    return stem[: -len("_libretro")] if stem.endswith("_libretro") else stem


def required_firmware(info: dict[str, str]) -> list[str]:
    """The BIOS files this core needs and will not run without.

    `firmwareN_opt = "true"` marks an optional one -- Snes9x lists BS-X and
    the Sufami Turbo BIOS that way, and neither is needed to play an
    ordinary SNES cartridge. Reporting those as required would tell an
    operator to go and find a file they do not need, which is the specific
    kind of wrong answer this table exists to stop.
    """
    try:
        count = int(info.get("firmware_count", "0"))
    except ValueError:
        return []
    out = []
    for i in range(min(count, 32)):
        if info.get(f"firmware{i}_opt", "").lower() == "true":
            continue
        path = info.get(f"firmware{i}_path", "").strip()
        if path:
            out.append(path)
    return out


def main() -> int:
    client = httpx.Client(timeout=60)
    response = client.get(TREE)
    if response.status_code in (403, 429):
        # Unauthenticated GitHub API calls are 60 per hour per address, and
        # this script needs exactly one of them. Worth saying plainly: the
        # 306 `.info` bodies come from raw.githubusercontent.com, which is
        # not the API and is not part of that budget, so a rate-limited run
        # fails here or not at all.
        print(
            "GitHub rate-limited the one API call this script makes "
            f"(HTTP {response.status_code}). The budget is 60 per hour per "
            "address and resets on the hour; the 306 file fetches after it "
            "are not API calls and are not limited. Try again shortly."
        )
        return 1
    if response.status_code != 200:
        print(f"GitHub answered HTTP {response.status_code} for the tree")
        return 1
    tree = response.json()
    if tree.get("truncated"):
        print("the tree came back truncated; refusing to write a partial table")
        return 1
    names = sorted(
        e["path"]
        for e in tree["tree"]
        if e["type"] == "blob" and e["path"].endswith(".info") and e["path"] not in _SKIP
    )
    print(f"{len(names)} .info files")

    def fetch(name: str) -> tuple[str, str]:
        url = RAW + urllib.parse.quote(name, safe="/")
        return name, client.get(url).text

    with ThreadPoolExecutor(max_workers=16) as ex:
        bodies = dict(ex.map(fetch, names))

    rows = []
    for name in names:
        info = parse_info(bodies[name])
        cid = core_id(name)
        if not cid:
            continue
        rows.append(
            (
                cid,
                {
                    "display": info.get("display_name", "").strip(),
                    "system": info.get("systemname", "").strip(),
                    "manufacturer": info.get("manufacturer", "").strip(),
                    "license": info.get("license", "").strip(),
                    "extensions": info.get("supported_extensions", "").strip(),
                    "firmware": required_firmware(info),
                    "databases": [
                        d.strip()
                        for d in info.get("database", "").split("|")
                        if d.strip()
                    ],
                },
            )
        )

    today = datetime.date.today().isoformat()
    body = [HEADER.format(count=len(rows), date=today), "CORE_INFO: dict[str, dict] = {"]
    for cid, row in rows:
        body.append(f"    {cid!r}: {_compact(row)},")
    body.append("}")
    body.append(FOOTER)
    OUT.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(rows)} cores)")
    return 0


def _compact(row: dict) -> str:
    """One dict per line, with the empty fields dropped.

    A generated table is read far more often than it is regenerated, so it
    is written to be greppable: `grep '"system": "Super Nintendo'` should
    find the row. Empty values are omitted rather than written as `""`,
    which takes about a third off the file and means an absent key reads as
    "libretro does not say" instead of as an empty string somebody has to
    interpret.
    """
    parts = []
    for key, value in row.items():
        if not value:
            continue
        parts.append(f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    return "{" + ", ".join(parts) + "}"


HEADER = '''"""Core id -> what libretro says that core is. GENERATED -- do not edit.

    python scripts/render_core_info.py

{count} cores, read from `libretro/libretro-core-info` on {date}. That
repository is MIT, verified by reading its own COPYING rather than
GitHub's summary of it.

Each row carries only what an operator choosing a core needs, and every
value is libretro's own words:

  display       the core's `display_name` ("Nintendo - SNES / SFC (Snes9x)")
  system        `systemname` ("Super Nintendo Entertainment System")
  manufacturer  `manufacturer` ("Nintendo")
  license       `license` -- the CORE's licence, not this plugin's, and
                they genuinely differ: Snes9x says "Non-commercial"
  extensions    `supported_extensions` ("smc|sfc|swc|fig|bs|st")
  firmware      the BIOS files the core needs, **excluding** the ones
                libretro marks `firmwareN_opt = "true"`
  databases     the `database` field split on "|" -- libretro's own
                platform names, which is how a core says what it is for

A key is absent rather than empty when libretro says nothing, so
`row.get("system")` returning None means "not stated upstream".

This replaced a hand-kept 106-row table that answered `None` for the
other 112 cores the buildbot ships. It is a snapshot and will go stale in
one direction: a core libretro adds appears here as unknown until this is
regenerated, which is a missing label rather than a wrong one. The `.info`
file for the core an operator actually installs is fetched live at install
time and is never this snapshot.
"""

'''

FOOTER = '''

def info_for(core_id: str) -> dict:
    """What libretro says about this core, or an empty dict.

    Empty is a fact about the table, never an instruction to substitute
    something. Callers leave the field unset rather than filling it in --
    a plausible-looking guess in a column an operator reads while choosing
    is worse than a blank.
    """
    if not isinstance(core_id, str):
        return {}
    return CORE_INFO.get(core_id.strip(), {})


def system_for(core_id: str) -> str | None:
    """The system a core emulates, or None when libretro does not say."""
    return info_for(core_id).get("system") or None


def matches_system(core_id: str, needle: str) -> bool:
    """True when `needle` appears in anything this core says it is for.

    Matched against the system name, the manufacturer and every libretro
    database name, case-insensitively, because an operator types "snes"
    or "PlayStation" rather than "Super Nintendo Entertainment System" --
    and because a core's database names are where the cross-platform cases
    live: Snes9x names "Nintendo - Sufami Turbo" and "Nintendo -
    Satellaview" alongside the SNES itself.
    """
    needle = (needle or "").strip().casefold()
    if not needle:
        return True
    row = info_for(core_id)
    haystacks = [
        row.get("system", ""),
        row.get("manufacturer", ""),
        row.get("display", ""),
        *row.get("databases", []),
    ]
    return any(needle in h.casefold() for h in haystacks)
'''


if __name__ == "__main__":
    sys.exit(main())
