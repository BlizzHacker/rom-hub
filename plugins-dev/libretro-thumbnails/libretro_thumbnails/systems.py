"""RomM platform slug -> libretro thumbnail system directory.

**This table is the only thing standing between a rom and another
system's box art**, so it is an exact-match lookup with no fallback. A
slug that is not spelled out below raises "needs mapping" and names
itself. Guessing would attach a plausible-looking cover from the wrong
console, and nothing about the library afterwards would say so.

Both sides of the table are real listings, not lists from memory:

* the values are the directory names served by
  `https://thumbnails.libretro.com/` (127 of them, read on 2026-07-29);
* the keys are RomM 4.9.2's own platform slugs, read from
  `GET /api/platforms/supported` (458 of them).

Where the two disagree about granularity the *narrower* side wins, and
where a RomM slug has no libretro directory at all it is simply absent:

* `famicom` and `sfam` map onto the NES and SNES directories, because
  No-Intro files Famicom and Super Famicom cartridges in those DATs and
  the thumbnails are named from those DATs. `fds` does not -- the Disk
  System is its own DAT and its own directory.
* `atari800` and `atari8bit` both map to `Atari - 8-bit`, which is one
  directory covering the whole 8-bit line.
* `neogeoaes` and `neogeomvs` both map to `SNK - Neo Geo`; libretro does
  not split the home and arcade sets.
* `c128`, `new-nintendo-3ds`, `msx-turbo` and `msx2plus` are deliberately
  **absent**. Each is a real machine whose software libretro does not
  carry separately, and folding it into its nearest neighbour would be
  the misfiling this table exists to prevent.
* `arcade` maps to `MAME`. libretro also serves `FBNeo - Arcade Games`,
  which is a differently-named set of the same cabinets; an operator
  whose library follows FBNeo's naming should set `system_override`
  rather than have this table pick for them.

libretro's remaining directories -- `DOOM`, `Quake`, `Cave Story`,
`Tomb Raider`, `MrBoom` and friends -- are single-game engine ports, not
platforms, and have no RomM slug to map from.
"""

# RomM platform slug -> libretro thumbnail system directory name.
SYSTEMS: dict[str, str] = {
    # Amstrad
    "acpc": "Amstrad - CPC",
    "amstrad-gx4000": "Amstrad - GX4000",
    # Arduboy
    "arduboy": "Arduboy Inc - Arduboy",
    # Atari
    "atari8bit": "Atari - 8-bit",
    "atari800": "Atari - 8-bit",
    "atari2600": "Atari - 2600",
    "atari5200": "Atari - 5200",
    "atari7800": "Atari - 7800",
    "jaguar": "Atari - Jaguar",
    "lynx": "Atari - Lynx",
    "atari-st": "Atari - ST",
    # Bandai
    "wonderswan": "Bandai - WonderSwan",
    "wonderswan-color": "Bandai - WonderSwan Color",
    # Casio
    "casio-loopy": "Casio - Loopy",
    "casio-pv-1000": "Casio - PV-1000",
    # Coleco
    "colecovision": "Coleco - ColecoVision",
    # Commodore
    "c64": "Commodore - 64",
    "amiga": "Commodore - Amiga",
    "amiga-cd32": "Commodore - CD32",
    "commodore-cdtv": "Commodore - CDTV",
    "cpet": "Commodore - PET",
    "c-plus-4": "Commodore - Plus-4",
    "vic-20": "Commodore - VIC-20",
    # PC
    "dos": "DOS",
    "scummvm": "ScummVM",
    # Emerson / Entex / Epoch / Fairchild / Funtech / GCE / GamePark
    "arcadia-2001": "Emerson - Arcadia 2001",
    "adventure-vision": "Entex - Adventure Vision",
    "epoch-super-cassette-vision": "Epoch - Super Cassette Vision",
    "fairchild-channel-f": "Fairchild - Channel F",
    "super-acan": "Funtech - Super Acan",
    "vectrex": "GCE - Vectrex",
    "gp32": "GamePark - GP32",
    # Handhelds with no manufacturer directory of their own
    "handheld-electronic-lcd": "Handheld Electronic Game",
    "hartung": "Hartung - Game Master",
    "leapster": "LeapFrog - Leapster Learning Game System",
    # Microsoft
    "msx": "Microsoft - MSX",
    "msx2": "Microsoft - MSX2",
    "xbox": "Microsoft - Xbox",
    "xbox360": "Microsoft - Xbox 360",
    # Magnavox / Mattel / Philips
    "odyssey-2": "Magnavox - Odyssey2",
    "videopac-g7400": "Philips - Videopac+",
    "philips-cd-i": "Philips - CD-i",
    "intellivision": "Mattel - Intellivision",
    # NEC
    "pc-9800-series": "NEC - PC-98",
    "pc-8000": "NEC - PC-8001 - PC-8801",
    "pc-8800-series": "NEC - PC-8001 - PC-8801",
    "pc-fx": "NEC - PC-FX",
    "tg16": "NEC - PC Engine - TurboGrafx 16",
    "turbografx-cd": "NEC - PC Engine CD - TurboGrafx-CD",
    "supergrafx": "NEC - PC Engine SuperGrafx",
    # Nintendo
    "fds": "Nintendo - Family Computer Disk System",
    "gb": "Nintendo - Game Boy",
    "gba": "Nintendo - Game Boy Advance",
    "gbc": "Nintendo - Game Boy Color",
    "ngc": "Nintendo - GameCube",
    "3ds": "Nintendo - Nintendo 3DS",
    "n64": "Nintendo - Nintendo 64",
    "64dd": "Nintendo - Nintendo 64DD",
    "nds": "Nintendo - Nintendo DS",
    "nintendo-dsi": "Nintendo - Nintendo DSi",
    "nes": "Nintendo - Nintendo Entertainment System",
    "famicom": "Nintendo - Nintendo Entertainment System",
    "pokemon-mini": "Nintendo - Pokemon Mini",
    "satellaview": "Nintendo - Satellaview",
    "sufami-turbo": "Nintendo - Sufami Turbo",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "sfam": "Nintendo - Super Nintendo Entertainment System",
    "virtualboy": "Nintendo - Virtual Boy",
    "wii": "Nintendo - Wii",
    "wiiu": "Nintendo - Wii U",
    # RCA
    "rca-studio-ii": "RCA - Studio II",
    # SNK
    "neogeoaes": "SNK - Neo Geo",
    "neogeomvs": "SNK - Neo Geo",
    "neo-geo-cd": "SNK - Neo Geo CD",
    "neo-geo-pocket": "SNK - Neo Geo Pocket",
    "neo-geo-pocket-color": "SNK - Neo Geo Pocket Color",
    # Sega
    "sega32": "Sega - 32X",
    "dc": "Sega - Dreamcast",
    "gamegear": "Sega - Game Gear",
    "sms": "Sega - Master System - Mark III",
    "segacd": "Sega - Mega-CD - Sega CD",
    "genesis": "Sega - Mega Drive - Genesis",
    "sega-pico": "Sega - PICO",
    "sg1000": "Sega - SG-1000",
    "saturn": "Sega - Saturn",
    # Sharp / Sinclair / Spectravideo / Thomson
    "x1": "Sharp - X1",
    "sharp-x68000": "Sharp - X68000",
    "zx81": "Sinclair - ZX 81",
    "zxs": "Sinclair - ZX Spectrum",
    "spectravideo": "Spectravideo - SVI-318 - SVI-328",
    "thomson-mo5": "Thomson - MOTO",
    "thomson-to": "Thomson - MOTO",
    # Sony
    "psx": "Sony - PlayStation",
    "ps2": "Sony - PlayStation 2",
    "ps3": "Sony - PlayStation 3",
    "ps4": "Sony - PlayStation 4",
    "psp": "Sony - PlayStation Portable",
    "psvita": "Sony - PlayStation Vita",
    # The 3DO Company
    "3do": "The 3DO Company - 3DO",
    # Tiger / VTech / Watara
    "game-dot-com": "Tiger - Game.com",
    "creativision": "VTech - CreatiVision",
    "vsmile": "VTech - V.Smile",
    "supervision": "Watara - Supervision",
    # Fantasy consoles
    "tic-80": "TIC-80",
    "wasm-4": "WASM-4",
    # Arcade
    "arcade": "MAME",
}


class NeedsMapping(Exception):
    """This platform has no entry in SYSTEMS, and one has to be added."""


def system_for(platform: str | None) -> str:
    """The libretro system directory for a RomM platform slug.

    Raises `NeedsMapping` rather than returning a default. A wrong system
    here does not fail -- it succeeds, with another console's cover.
    """
    slug = (platform or "").strip().lower()
    if not slug:
        raise NeedsMapping(
            "this rom has no platform in RomM, so there is no libretro system "
            "to look it up in; set the rom's platform in RomM first"
        )
    try:
        return SYSTEMS[slug]
    except KeyError:
        raise NeedsMapping(
            f"platform {slug!r} needs mapping: libretro-thumbnails has no "
            f"libretro system directory for it, and this plugin will not guess "
            f"one, because the wrong system does not fail -- it succeeds, with "
            f"another console's box art. Add {slug!r} to "
            f"libretro_thumbnails/systems.py"
        ) from None
