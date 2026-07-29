"""RomM platform slug -> the libretro-database DAT files for that machine.

**This table is the only thing standing between a rom and another
console's catalogue entry.** `Tetris` exists in a dozen of these DATs.
Every lookup names one file, so a slug that is not spelled out below
raises **"needs mapping"** and names itself rather than sweeping the
corpus and taking whatever answers first.

Both sides are read listings rather than recollection:

* the values are filenames served by `libretro/libretro-database` --
  `metadat/no-intro/` (92 files) and `metadat/redump/` (22 files), read
  2026-07-29 -- and each file's `clrmamepro` header `name` is its own
  filename, which is why the same strings appear in this repo's
  `hasheous` platform table;
* the keys are RomM platform slugs, the set `libretro-thumbnails`
  verified against RomM 4.9.2's `GET /api/platforms/supported`.

A slug may name **several** DATs, tried in the order given, because the
two sets divide by *dump project* and not by machine: cartridges are
No-Intro's and discs are Redump's, and a handful of systems have both.
`xbox360` and `psp` are the clear cases -- No-Intro carries the digital
titles and Redump the pressed discs.

**Deliberately absent**, each for a stated reason:

* `arcade`, `neogeoaes`, `neogeomvs` -- MAME's DATs live under `dat/` and
  `metadat/mame*`, are named per MAME release, and are keyed by short set
  names rather than by titles. That is a different matching problem, not
  a missing row.
* `dos`, `scummvm`, `tic-80`, `wasm-4`, `zx81`, `acpc`, `cpet`,
  `thomson-*`, `spectravideo`, `handheld-electronic-lcd` -- carried under
  `dat/` or `metadat/tosec/`, not in the two sets this plugin reads.
* `ps4`, `wiiu` (physical), `new-nintendo-3ds` beyond the digital set --
  no DAT in either directory.

`zxs` maps to `Sinclair - ZX Spectrum +3`, which is the disk library
only: the tape library is TOSEC's. A tape dump therefore finds nothing,
which is the honest outcome -- it is not matched to the wrong entry.
"""

# One DAT: the set directory under `metadat/`, and the file's stem.
Dat = tuple[str, str]

NO_INTRO = "no-intro"
REDUMP = "redump"

# RomM platform slug -> DATs to consult, in order.
SYSTEMS: dict[str, tuple[Dat, ...]] = {
    # Arduboy
    "arduboy": ((NO_INTRO, "Arduboy Inc - Arduboy"),),
    # Atari
    "atari2600": ((NO_INTRO, "Atari - 2600"),),
    "atari5200": ((NO_INTRO, "Atari - 5200"),),
    "atari7800": ((NO_INTRO, "Atari - 7800"),),
    "atari8bit": ((NO_INTRO, "Atari - 8-bit Family"),),
    "atari800": ((NO_INTRO, "Atari - 8-bit Family"),),
    "jaguar": ((NO_INTRO, "Atari - Jaguar"),),
    "atari-jaguar-cd": ((REDUMP, "Atari - Jaguar CD"),),
    "lynx": ((NO_INTRO, "Atari - Lynx"),),
    "atari-st": ((NO_INTRO, "Atari - ST"),),
    # Bandai
    "wonderswan": ((NO_INTRO, "Bandai - WonderSwan"),),
    "wonderswan-color": ((NO_INTRO, "Bandai - WonderSwan Color"),),
    # Casio
    "casio-loopy": ((NO_INTRO, "Casio - Loopy"),),
    "casio-pv-1000": ((NO_INTRO, "Casio - PV-1000"),),
    # Coleco
    "colecovision": ((NO_INTRO, "Coleco - ColecoVision"),),
    # Commodore
    "c64": ((NO_INTRO, "Commodore - 64"),),
    "amiga": ((NO_INTRO, "Commodore - Amiga"),),
    "amiga-cd32": ((REDUMP, "Commodore - CD32"),),
    "commodore-cdtv": ((REDUMP, "Commodore - CDTV"),),
    "c-plus-4": ((NO_INTRO, "Commodore - Plus-4"),),
    "vic-20": ((NO_INTRO, "Commodore - VIC-20"),),
    # Emerson / Entex / Epoch / Fairchild / Funtech / GCE / GamePark
    "arcadia-2001": ((NO_INTRO, "Emerson - Arcadia 2001"),),
    "adventure-vision": ((NO_INTRO, "Entex - Adventure Vision"),),
    "epoch-super-cassette-vision": ((NO_INTRO, "Epoch - Super Cassette Vision"),),
    "fairchild-channel-f": ((NO_INTRO, "Fairchild - Channel F"),),
    "super-acan": ((NO_INTRO, "Funtech - Super Acan"),),
    "vectrex": ((NO_INTRO, "GCE - Vectrex"),),
    "gp32": ((NO_INTRO, "GamePark - GP32"),),
    "hartung": ((NO_INTRO, "Hartung - Game Master"),),
    "leapster": ((NO_INTRO, "LeapFrog - Leapster Learning Game System"),),
    # Magnavox / Mattel
    "odyssey-2": ((NO_INTRO, "Magnavox - Odyssey2"),),
    "intellivision": ((NO_INTRO, "Mattel - Intellivision"),),
    # Microsoft
    "msx": ((NO_INTRO, "Microsoft - MSX"),),
    "msx2": ((NO_INTRO, "Microsoft - MSX2"),),
    "xbox": ((REDUMP, "Microsoft - Xbox"),),
    "xbox360": (
        (REDUMP, "Microsoft - Xbox 360"),
        (NO_INTRO, "Microsoft - Xbox 360"),
        (NO_INTRO, "Microsoft - Xbox 360 (Digital)"),
    ),
    # NEC
    "tg16": ((NO_INTRO, "NEC - PC Engine - TurboGrafx 16"),),
    "supergrafx": ((NO_INTRO, "NEC - PC Engine SuperGrafx"),),
    "turbografx-cd": ((REDUMP, "NEC - PC Engine CD - TurboGrafx-CD"),),
    "pc-fx": ((REDUMP, "NEC - PC-FX"),),
    "pc-9800-series": ((REDUMP, "NEC - PC-98"),),
    # Nintendo
    "fds": ((NO_INTRO, "Nintendo - Family Computer Disk System"),),
    "gb": ((NO_INTRO, "Nintendo - Game Boy"),),
    "gba": ((NO_INTRO, "Nintendo - Game Boy Advance"),),
    "gbc": ((NO_INTRO, "Nintendo - Game Boy Color"),),
    "3ds": (
        (NO_INTRO, "Nintendo - Nintendo 3DS"),
        (NO_INTRO, "Nintendo - Nintendo 3DS (Digital)"),
    ),
    "n64": ((NO_INTRO, "Nintendo - Nintendo 64"),),
    "64dd": ((NO_INTRO, "Nintendo - Nintendo 64DD"),),
    "nds": ((NO_INTRO, "Nintendo - Nintendo DS"),),
    "nintendo-dsi": ((NO_INTRO, "Nintendo - Nintendo DSi"),),
    "nes": ((NO_INTRO, "Nintendo - Nintendo Entertainment System"),),
    "famicom": ((NO_INTRO, "Nintendo - Nintendo Entertainment System"),),
    "snes": ((NO_INTRO, "Nintendo - Super Nintendo Entertainment System"),),
    "sfam": ((NO_INTRO, "Nintendo - Super Nintendo Entertainment System"),),
    "pokemon-mini": ((NO_INTRO, "Nintendo - Pokemon Mini"),),
    "satellaview": ((NO_INTRO, "Nintendo - Satellaview"),),
    "sufami-turbo": ((NO_INTRO, "Nintendo - Sufami Turbo"),),
    "virtualboy": ((NO_INTRO, "Nintendo - Virtual Boy"),),
    "ngc": ((REDUMP, "Nintendo - GameCube"),),
    "wii": ((REDUMP, "Nintendo - Wii"), (NO_INTRO, "Nintendo - Wii (Digital)")),
    # Philips / RCA
    "videopac-g7400": ((NO_INTRO, "Philips - Videopac+"),),
    "philips-cd-i": ((REDUMP, "Philips - CD-i"),),
    "rca-studio-ii": ((NO_INTRO, "RCA - Studio II"),),
    # SNK
    "neo-geo-pocket": ((NO_INTRO, "SNK - Neo Geo Pocket"),),
    "neo-geo-pocket-color": ((NO_INTRO, "SNK - Neo Geo Pocket Color"),),
    "neo-geo-cd": ((REDUMP, "SNK - Neo Geo CD"),),
    # Sega
    "sega32": ((NO_INTRO, "Sega - 32X"),),
    "gamegear": ((NO_INTRO, "Sega - Game Gear"),),
    "sms": ((NO_INTRO, "Sega - Master System - Mark III"),),
    "genesis": ((NO_INTRO, "Sega - Mega Drive - Genesis"),),
    "sega-pico": ((NO_INTRO, "Sega - PICO"),),
    "sg1000": ((NO_INTRO, "Sega - SG-1000"),),
    "segacd": ((REDUMP, "Sega - Mega-CD - Sega CD"),),
    "dc": ((REDUMP, "Sega - Dreamcast"),),
    "saturn": ((REDUMP, "Sega - Saturn"),),
    # Sharp
    "x1": ((NO_INTRO, "Sharp - X1"),),
    "sharp-x68000": ((NO_INTRO, "Sharp - X68000"),),
    # Sinclair. The +3 disk library only; tapes are TOSEC's.
    "zxs": ((NO_INTRO, "Sinclair - ZX Spectrum +3"),),
    # Sony
    "psx": ((REDUMP, "Sony - PlayStation"),),
    "ps2": ((REDUMP, "Sony - PlayStation 2"),),
    "ps3": ((REDUMP, "Sony - PlayStation 3"), (NO_INTRO, "Sony - PlayStation 3 (PSN)")),
    "psp": (
        (REDUMP, "Sony - PlayStation Portable"),
        (NO_INTRO, "Sony - PlayStation Portable"),
        (NO_INTRO, "Sony - PlayStation Portable (PSN)"),
    ),
    "psvita": (
        (NO_INTRO, "Sony - PlayStation Vita"),
        (NO_INTRO, "Sony - PlayStation Vita (PSN)"),
    ),
    # The 3DO Company / Tiger / VTech / Watara
    "3do": ((REDUMP, "The 3DO Company - 3DO"),),
    "game-dot-com": ((NO_INTRO, "Tiger - Game.com"),),
    "creativision": ((NO_INTRO, "VTech - CreatiVision"),),
    "vsmile": ((NO_INTRO, "VTech - V.Smile"),),
    "supervision": ((NO_INTRO, "Watara - Supervision"),),
}

#: Every set this table names. A `sets` config value outside this is a typo.
KNOWN_SETS = frozenset({NO_INTRO, REDUMP})


class NeedsMapping(Exception):
    """A RomM platform with no DAT in the sets this plugin reads."""


def dats_for(slug: str | None, sets) -> tuple[Dat, ...]:
    """The DATs to consult for a slug, filtered to the configured sets."""
    if not slug:
        raise NeedsMapping(
            "this rom has no platform in RomM, and every DAT here is one "
            "console's catalogue -- an unscoped lookup would match another "
            "machine's game of the same name"
        )
    dats = SYSTEMS.get(slug)
    if dats is None:
        raise NeedsMapping(
            f"needs mapping: RomM platform {slug!r} has no DAT in "
            f"libretro_database/systems.py. This plugin reads "
            f"metadat/no-intro/ and metadat/redump/ only; arcade, DOS and the "
            f"8-bit micros are catalogued elsewhere in that repository, under "
            f"different keys. Add the file's stem there if one exists."
        )
    chosen = tuple(dat for dat in dats if dat[0] in sets)
    if not chosen:
        raise NeedsMapping(
            f"RomM platform {slug!r} is catalogued only in "
            f"{sorted({dat[0] for dat in dats})}, and `sets` is "
            f"{sorted(sets)}. Widen `sets`, or accept that this machine is "
            f"not covered by the sets you chose."
        )
    return chosen
