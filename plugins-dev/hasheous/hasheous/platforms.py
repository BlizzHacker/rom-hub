"""RomM platform slug -> the platform names hasheous answers with.

**This table is a cross-check, not a filing decision.** `MetadataPatch`
has no platform field, so nothing this plugin returns can move a rom to
another system. What the table is for is catching the one failure a
hash-keyed lookup really has: a **CRC-32 collision**. Thirty-two bits
across a corpus of millions of dumps collide as a matter of routine, and
a collision does not look like an error -- it looks like a confident
answer about a different game, on a different console, written into the
library with the operator none the wiser. Comparing the console hasheous
names against the console RomM already recorded is what turns that into a
refusal.

An unmapped slug raises **"needs mapping"** and names itself rather than
being waved through, which is the same rule the rest of this repo's
plugins follow. Set `verify_platform = false` to skip the check entirely;
that is the supported escape hatch and it is a deliberate choice by an
operator, not a guess by this plugin.

**Where the values come from.** Hasheous does not invent platform names.
`HashLookup2.cs` creates the platform DataObject with
`Name = discoveredSignature.Game.System`, and `Game.System` is set by the
signature parser from the DAT file's own header -- for No-Intro,
`NoIntrosParser.cs` reads `SystemName = noIntrosObject.Name` straight out
of the `<name>` element and assigns `gameObject.System = SystemName`. So
the vocabulary is *DAT header names*, and every value below is one that
appears as a real DAT header: the No-Intro and Redump sets are carried
verbatim in `libretro/libretro-database` under `metadat/no-intro/` (92
files) and `metadat/redump/` (22 files), read 2026-07-29, and each file's
header `name` is its filename. The keys are RomM platform slugs.

**Comparison is normalised, not literal, and that is load-bearing rather
than defensive.** Hasheous also ingests TOSEC, MAME, WHDLoad, FBNeo and
Pleasuredome DATs, whose headers spell the same machine with different
punctuation -- TOSEC writes `Nintendo Game Boy` where No-Intro writes
`Nintendo - Game Boy` -- and No-Intro itself has re-punctuated some
system names since libretro's copies were taken. Running the shipped
plugin against the live service on 2026-07-29 returned
`'Sega Mega Drive & Genesis'` for the signature and
`'Sega Mega Drive / Genesis'` for the platform object, where the table
below says `Sega - Mega Drive - Genesis`. All three are the same eight
words. `key()` strips everything that is not a letter or a digit and
lowercases the rest, so they agree -- and a literal comparison would have
refused a perfectly good match.

**What is deliberately absent.** `arcade`, `neogeoaes`, `neogeomvs`,
`dos`, `scummvm`, `tic-80`, `wasm-4`, `acpc`, `zx81`, `cpet`,
`handheld-electronic-lcd`, `thomson-*`, `spectravideo`, `pc-8000`,
`pc-8800-series`, `ps4`, `wiiu` and `new-nintendo-3ds` are **not** here.
Each is either a machine whose hasheous platform name comes from a DAT
family this module has not read (MAME and TOSEC name arcade hardware and
8-bit micros in ways No-Intro never has to), or a RomM slug with no
signature source at all. A wrong entry here does not fail loudly -- it
silently approves a mismatch or silently refuses a good match -- so an
absent entry, which says so, is the better of the two.
"""

import re

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


class NeedsMapping(Exception):
    """A RomM platform this module has no hasheous platform name for."""


def key(name: str) -> str:
    """A comparison key for one machine, independent of DAT punctuation.

    `Nintendo - Game Boy`, `Nintendo Game Boy` and `nintendo_game_boy`
    all collapse to `nintendogameboy`. Nothing is dropped except
    punctuation and case, so `Nintendo - Game Boy` and `Nintendo - Game
    Boy Color` stay different -- which is the whole point, since those
    are two consoles and two RomM slugs.
    """
    return _NON_ALNUM.sub("", (name or "").strip().lower())


# RomM platform slug -> DAT header names that mean this machine.
PLATFORMS: dict[str, tuple[str, ...]] = {
    # Arduboy
    "arduboy": ("Arduboy Inc - Arduboy",),
    # Atari
    "atari2600": ("Atari - 2600",),
    "atari5200": ("Atari - 5200",),
    "atari7800": ("Atari - 7800",),
    "atari8bit": ("Atari - 8-bit Family",),
    "atari800": ("Atari - 8-bit Family",),
    "jaguar": ("Atari - Jaguar",),
    "atari-jaguar-cd": ("Atari - Jaguar CD",),
    "lynx": ("Atari - Lynx",),
    "atari-st": ("Atari - ST",),
    # Bandai
    "wonderswan": ("Bandai - WonderSwan",),
    "wonderswan-color": ("Bandai - WonderSwan Color",),
    # Casio
    "casio-loopy": ("Casio - Loopy",),
    "casio-pv-1000": ("Casio - PV-1000",),
    # Coleco
    "colecovision": ("Coleco - ColecoVision",),
    # Commodore
    "c64": ("Commodore - 64",),
    "amiga": ("Commodore - Amiga",),
    "amiga-cd32": ("Commodore - CD32",),
    "commodore-cdtv": ("Commodore - CDTV",),
    "c-plus-4": ("Commodore - Plus-4",),
    "vic-20": ("Commodore - VIC-20",),
    # Emerson / Entex / Epoch / Fairchild / Funtech / GCE / GamePark
    "arcadia-2001": ("Emerson - Arcadia 2001",),
    "adventure-vision": ("Entex - Adventure Vision",),
    "epoch-super-cassette-vision": ("Epoch - Super Cassette Vision",),
    "fairchild-channel-f": ("Fairchild - Channel F",),
    "super-acan": ("Funtech - Super Acan",),
    "vectrex": ("GCE - Vectrex",),
    "gp32": ("GamePark - GP32",),
    "hartung": ("Hartung - Game Master",),
    "leapster": ("LeapFrog - Leapster Learning Game System",),
    # Magnavox / Mattel
    "odyssey-2": ("Magnavox - Odyssey2",),
    "intellivision": ("Mattel - Intellivision",),
    # Microsoft
    "msx": ("Microsoft - MSX",),
    "msx2": ("Microsoft - MSX2",),
    "xbox": ("Microsoft - Xbox",),
    "xbox360": ("Microsoft - Xbox 360",),
    # NEC
    "tg16": ("NEC - PC Engine - TurboGrafx 16",),
    "supergrafx": ("NEC - PC Engine SuperGrafx",),
    "turbografx-cd": ("NEC - PC Engine CD - TurboGrafx-CD",),
    "pc-fx": ("NEC - PC-FX",),
    "pc-9800-series": ("NEC - PC-98",),
    # Nintendo. `famicom` and `sfam` share the NES and SNES DATs, because
    # No-Intro files Famicom and Super Famicom cartridges in those sets --
    # the same fold `libretro-thumbnails` makes, for the same reason.
    "fds": ("Nintendo - Family Computer Disk System",),
    "gb": ("Nintendo - Game Boy",),
    "gba": ("Nintendo - Game Boy Advance",),
    "gbc": ("Nintendo - Game Boy Color",),
    "3ds": ("Nintendo - Nintendo 3DS",),
    "n64": ("Nintendo - Nintendo 64",),
    "64dd": ("Nintendo - Nintendo 64DD",),
    "nds": ("Nintendo - Nintendo DS",),
    "nintendo-dsi": ("Nintendo - Nintendo DSi",),
    "nes": ("Nintendo - Nintendo Entertainment System",),
    "famicom": ("Nintendo - Nintendo Entertainment System",),
    "snes": ("Nintendo - Super Nintendo Entertainment System",),
    "sfam": ("Nintendo - Super Nintendo Entertainment System",),
    "pokemon-mini": ("Nintendo - Pokemon Mini",),
    "satellaview": ("Nintendo - Satellaview",),
    "sufami-turbo": ("Nintendo - Sufami Turbo",),
    "virtualboy": ("Nintendo - Virtual Boy",),
    "ngc": ("Nintendo - GameCube",),
    "wii": ("Nintendo - Wii",),
    # Philips / RCA
    "videopac-g7400": ("Philips - Videopac+",),
    "philips-cd-i": ("Philips - CD-i",),
    "rca-studio-ii": ("RCA - Studio II",),
    # SNK
    "neo-geo-pocket": ("SNK - Neo Geo Pocket",),
    "neo-geo-pocket-color": ("SNK - Neo Geo Pocket Color",),
    "neo-geo-cd": ("SNK - Neo Geo CD",),
    # Sega
    "sega32": ("Sega - 32X",),
    "gamegear": ("Sega - Game Gear",),
    "sms": ("Sega - Master System - Mark III",),
    "genesis": ("Sega - Mega Drive - Genesis",),
    "sega-pico": ("Sega - PICO",),
    "sg1000": ("Sega - SG-1000",),
    "segacd": ("Sega - Mega-CD - Sega CD",),
    "dc": ("Sega - Dreamcast",),
    "saturn": ("Sega - Saturn",),
    # Sharp
    "x1": ("Sharp - X1",),
    "sharp-x68000": ("Sharp - X68000",),
    # Sinclair. No-Intro carries only the +3 disk set; TOSEC carries the
    # tape library as plain "Sinclair ZX Spectrum", and both are this slug.
    "zxs": ("Sinclair - ZX Spectrum", "Sinclair - ZX Spectrum +3"),
    # Sony
    "psx": ("Sony - PlayStation",),
    "ps2": ("Sony - PlayStation 2",),
    "ps3": ("Sony - PlayStation 3",),
    "psp": ("Sony - PlayStation Portable",),
    "psvita": ("Sony - PlayStation Vita",),
    # The 3DO Company / Tiger / VTech / Watara
    "3do": ("The 3DO Company - 3DO",),
    "game-dot-com": ("Tiger - Game.com",),
    "creativision": ("VTech - CreatiVision",),
    "vsmile": ("VTech - V.Smile",),
    "supervision": ("Watara - Supervision",),
}


def expected_keys(slug: str | None) -> frozenset[str]:
    """Normalised platform names acceptable for this RomM slug."""
    if not slug:
        raise NeedsMapping(
            "this rom has no platform in RomM, so the console hasheous names "
            "cannot be checked against anything. Set verify_platform = false "
            "to look it up anyway."
        )
    names = PLATFORMS.get(slug)
    if names is None:
        raise NeedsMapping(
            f"needs mapping: RomM platform {slug!r} has no hasheous platform "
            f"name in hasheous/platforms.py, so a hash match cannot be checked "
            f"against the console this rom is filed under. Add it there, or set "
            f"verify_platform = false to accept hasheous's answer unchecked."
        )
    return frozenset(key(name) for name in names)
