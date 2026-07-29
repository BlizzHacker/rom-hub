"""RomM platform slug -> OpenVGDB `SYSTEMS.systemID`.

**This table is what stops a filename match landing on another console's
game.** OpenVGDB is one table of 51,742 ROMs across 43 systems, and
`Tetris.gb` is not the only `Tetris` in it. Every query this plugin makes
is scoped to a systemID, so a slug that is not spelled out below raises
**"needs mapping"** and names itself rather than searching the whole
corpus and taking whatever comes back first.

Both sides are read from real listings rather than remembered:

* the values are `SYSTEMS.systemID` from `openvgdb.sqlite` itself
  (release `v29.0`, `SELECT systemID, systemName, systemShortName FROM
  SYSTEMS` -- 43 rows, read 2026-07-29);
* the keys are RomM platform slugs, the same set `libretro-thumbnails`
  verified against RomM 4.9.2's own `GET /api/platforms/supported`.

Where the two disagree about granularity the *narrower* side wins, and
the two folds below are the same ones the rest of this repo's tables
make, for the same reason -- No-Intro files Famicom and Super Famicom
cartridges in the NES and SNES sets, and OpenVGDB's ROM rows come from
those DATs:

* `famicom` -> 25 (Nintendo Entertainment System)
* `sfam` -> 26 (Super Nintendo Entertainment System)

OpenVGDB has no rows for anything else RomM knows about. It was built for
OpenEmu's console list and stopped being updated in 2021, so there is no
DOS, no Amiga, no ZX Spectrum, no PlayStation 2 and nothing later than the
PSP. Those slugs are absent because the data is, which is the honest
reason for an absence and the one that makes "needs mapping" the right
answer instead of an empty result set.
"""


class NeedsMapping(Exception):
    """A RomM platform OpenVGDB has no system for."""


# RomM platform slug -> OpenVGDB systemID.
SYSTEMS: dict[str, int] = {
    # The 3DO Company
    "3do": 1,
    # Arcade. OpenVGDB's system 2 is MAME, and its 10,815 entries are the
    # only ones whose cover art is not on gamefaqs.
    "arcade": 2,
    # Atari
    "atari2600": 3,
    "atari5200": 4,
    "atari7800": 5,
    "lynx": 6,
    "jaguar": 7,
    "atari-jaguar-cd": 8,
    # Bandai
    "wonderswan": 9,
    "wonderswan-color": 10,
    # Coleco
    "colecovision": 11,
    # GCE
    "vectrex": 12,
    # Mattel
    "intellivision": 13,
    # NEC
    "tg16": 14,
    "turbografx-cd": 15,
    "pc-fx": 16,
    "supergrafx": 17,
    # Nintendo
    "fds": 18,
    "gb": 19,
    "gba": 20,
    "gbc": 21,
    "ngc": 22,
    "n64": 23,
    "nds": 24,
    "nes": 25,
    "famicom": 25,
    "snes": 26,
    "sfam": 26,
    "virtualboy": 27,
    "wii": 28,
    # Sega
    "sega32": 29,
    "gamegear": 30,
    "sms": 31,
    "segacd": 32,
    "genesis": 33,
    "saturn": 34,
    "sg1000": 35,
    # SNK
    "neo-geo-pocket": 36,
    "neo-geo-pocket-color": 37,
    # Sony
    "psx": 38,
    "psp": 39,
    # Magnavox
    "odyssey-2": 40,
    # Commodore
    "c64": 41,
    # Microsoft
    "msx": 42,
    "msx2": 43,
}


def system_for(slug: str | None) -> int:
    """The OpenVGDB systemID for a RomM platform slug, or raise."""
    if not slug:
        raise NeedsMapping(
            "this rom has no platform in RomM, and OpenVGDB is one table of "
            "51,742 roms across 43 systems -- an unscoped lookup would match "
            "another console's game of the same name"
        )
    system = SYSTEMS.get(slug)
    if system is None:
        raise NeedsMapping(
            f"needs mapping: RomM platform {slug!r} has no OpenVGDB system in "
            f"openvgdb/platforms.py. OpenVGDB was built for OpenEmu's console "
            f"list and last released in 2021, so it may simply have no data "
            f"for this machine; if it does, add the systemID there."
        )
    return system
