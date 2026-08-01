"""Which platforms each in-tree plugin targets, and whether they can be played.

    python scripts/audit_platforms.py            # the table
    python scripts/audit_platforms.py --json     # {slug: [platform, ...]}

A ROM filed under a platform with no EmulatorJS core imports fine and then
does nothing when clicked. This is the sweep that says where that can
happen, and `catalog/plugins.json` records the answer per plugin so the
published directory says it too.

**It reads the tables, it does not parse for them.** An earlier version of
this guessed which module held a platform table from its filename and
scanned every dict literal for slug-shaped strings. That missed
`if-archive/formats.py`, `scummvm-freeware/games.py` and every
`systems.py`, and it counted itch-io's `_SIZE_UNITS = {..., "gb": 1000**3}`
as Game Boy support. Every plugin's table is a plain module-level mapping,
so `TABLES` below names each one and this imports it. The cost is that a
plugin renaming its table breaks this script -- loudly, in CI, which is
the correct direction for that failure.

**A dead target does not mean the same thing for every plugin.** Only an
`importer` files a ROM. When `hasheous` carries a row for `vectrex` it is
offering to identify a Vectrex ROM the operator obtained elsewhere; there
is no unplayable import in it, because there is no import in it. So the
report splits on capability and the totals are kept apart.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins-dev"
sys.path.insert(0, str(ROOT / "src"))

from rom_hub.playability import CATALOGUE_ONLY, NEEDS_NETPLAY, verdict_for  # noqa: E402


def values(table) -> list[str]:
    return [v for v in table.values() if isinstance(v, str)]


def keys(table) -> list[str]:
    return [k for k in table if isinstance(k, str)]


#: plugin slug -> (package directory, module, how to get the slugs out).
#:
#: The accessor is spelled out per plugin because the direction differs:
#: about half map a source label ONTO a RomM slug and about half key their
#: table BY one, and a rule that guessed which would be wrong somewhere.
#:
#: A plugin absent from this table has no platform mapping at all --
#: `emulators`, `libretro-cores`, `libretro-cheats`, `libretro-overlays`
#: and `retroarch-autoconfig` deal in emulator, core and controller names,
#: none of which is a platform. They are listed in `NO_PLATFORM_TABLE` so
#: that "missing" and "deliberately none" stay distinguishable.
TABLES: dict[str, tuple[str, str, object]] = {
    "aminet": ("aminet", "aminet.platforms", lambda m: values(m.ARCHITECTURES)),
    "archive-org": ("archive-org", "archive_org.platforms", lambda m: values(m.EMULATOR_PLATFORMS)),
    "demozoo": ("demozoo", "demozoo.platforms", lambda m: [p.slug for p in m.PLATFORMS.values()]),
    "hasheous": ("hasheous", "hasheous.platforms", lambda m: keys(m.PLATFORMS)),
    "homebrew": ("homebrew", "homebrew.platforms", lambda m: values(m.HUB_PLATFORMS)),
    "if-archive": ("if-archive", "if_archive.formats", lambda m: [f.platform for f in m._FORMATS if f.platform]),
    "itch-io": ("itch-io", "itch_io.platforms", lambda m: values(m.ITCH_PLATFORMS)),
    "libretro-content": ("libretro-content", "libretro_content.platforms", lambda m: values(m.SYSTEMS)),
    "libretro-database": ("libretro-database", "libretro_database.systems", lambda m: keys(m.SYSTEMS)),
    "libretro-thumbnails": ("libretro-thumbnails", "libretro_thumbnails.systems", lambda m: keys(m.SYSTEMS)),
    "ludusavi": ("ludusavi", "ludusavi.platforms", lambda m: keys(m.PC_PLATFORMS)),
    "nointro-archive": ("nointro-archive", "nointro_archive.platforms", lambda m: values(m.DIRECTORY_PLATFORMS)),
    "open-bios": ("open-bios", "open_bios.platforms", lambda m: values(m.SYSTEM_PLATFORMS)),
    "openvgdb": ("openvgdb", "openvgdb.platforms", lambda m: keys(m.SYSTEMS)),
    "retroachievements": ("retroachievements", "retroachievements.consoles", lambda m: keys(m.CONSOLES)),
    "scummvm-freeware": ("scummvm-freeware", "scummvm_freeware.games", lambda m: [g.platform for g in m.GAMES.values()]),
    "universal-db": ("universal-db", "universal_db.platforms", lambda m: values(m.SYSTEM_PLATFORMS)),
}

#: Plugins with no platform mapping, and what they map instead. Named so
#: that a plugin which *loses* its table is a failure rather than a shrug.
NO_PLATFORM_TABLE: dict[str, str] = {
    "emulators": "GitHub release assets, keyed by emulator project and build target",
    "libretro-cheats": "cheat files, keyed by libretro's own system directory names",
    "libretro-cores": "libretro cores, keyed by core name -- never a platform slug",
    "libretro-overlays": "bezel overlays, keyed by libretro's system directory names",
    "retroarch-autoconfig": "controller profiles, keyed by device name",
}


def _load(package_dir: str, module: str):
    sys.path.insert(0, str(PLUGINS / package_dir))
    try:
        return importlib.import_module(module)
    finally:
        sys.path.pop(0)


def targets(slug: str) -> list[str]:
    """Every RomM platform slug this plugin's table can produce, sorted."""
    if slug in NO_PLATFORM_TABLE:
        return []
    package_dir, module, accessor = TABLES[slug]
    return sorted({s.strip().lower() for s in accessor(_load(package_dir, module)) if s})


def capabilities(slug: str) -> list[str]:
    """What the plugin's own manifest declares, read rather than assumed."""
    from rom_hub.manifest import parse_manifest

    manifest = parse_manifest((PLUGINS / slug / "manifest.toml").read_text(encoding="utf-8"))
    return sorted(manifest.capabilities)


def audit() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for slug in sorted(set(TABLES) | set(NO_PLATFORM_TABLE)):
        found = targets(slug)
        caps = capabilities(slug)
        out[slug] = {
            "capabilities": caps,
            "files_roms": "importer" in caps,
            "platforms": found,
            "playable": [p for p in found if verdict_for(p).plays],
            "netplay_only": [p for p in found if verdict_for(p).verdict == NEEDS_NETPLAY],
            "catalogue_only": [p for p in found if verdict_for(p).verdict == CATALOGUE_ONLY],
        }
    return out


def _table(rows: dict[str, dict], want_importers: bool, title: str) -> None:
    subset = {k: v for k, v in rows.items() if v["files_roms"] is want_importers}
    print(f"\n{title}")
    print(f"{'plugin':<22}{'play':>6}{'netplay':>9}{'CATALOGUE-ONLY':>16}  slugs with no core")
    print("-" * 118)
    tp = tn = tc = 0
    for slug, row in subset.items():
        tp += len(row["playable"])
        tn += len(row["netplay_only"])
        tc += len(row["catalogue_only"])
        print(
            f"{slug:<22}{len(row['playable']):>6}{len(row['netplay_only']):>9}"
            f"{len(row['catalogue_only']):>16}  " + ", ".join(row["catalogue_only"])
        )
    print("-" * 118)
    print(f"{'TOTAL':<22}{tp:>6}{tn:>9}{tc:>16}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args()

    rows = audit()
    if args.json:
        print(json.dumps({k: v["platforms"] for k, v in rows.items()}, indent=2))
        return 0

    _table(rows, True, "IMPORTERS -- a slug with no core here files a ROM that will not play")
    _table(rows, False, "NON-IMPORTERS -- file nothing; a slug with no core is coverage, not a dead ROM")

    dead = sorted({d for r in rows.values() if r["files_roms"] for d in r["catalogue_only"]})
    print(f"\nDistinct catalogue-only IMPORT targets ({len(dead)}):\n  " + ", ".join(dead))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
