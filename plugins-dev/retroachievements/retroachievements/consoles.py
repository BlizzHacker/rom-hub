"""RomM platform slug -> RetroAchievements console id, and how RA hashes it.

**CONSOLES** is not a list from memory. It is RomM 4.9.2's own answer:
every entry is the `slug` and `ra_id` of a platform returned by
`GET /api/platforms/supported`, for the 66 of its 458 platforms that carry
an `ra_id` at all. RomM already knows this mapping; the plugin cannot ask
it (a plugin gets a `RomRef`, not RomM's API), so it carries a copy.

The ids are RetroAchievements' own console ids -- `RC_CONSOLE_MEGA_DRIVE`
is 1, `RC_CONSOLE_SUPER_NINTENDO` is 3, and so on through
`include/rc_consoles.h` in rcheevos. RomM's values agree with that header
everywhere they overlap, which is the check that matters: a wrong console
id here would fetch the wrong system's game list and simply never match.

**WHOLE_FILE_MD5 is the honest half of this file.** RetroAchievements does
not identify games by "the md5 of the ROM". It identifies them by whatever
`rc_hash_from_buffer()` in rcheevos computes for that console, and only
some consoles hash the whole file:

* the ids below hash the file exactly as it sits on disk, so RomM's own
  `md5_hash` **is** the RA hash and a miss means the game is genuinely not
  on RA;
* everything else does something else. The NES and the Famicom Disk System
  skip a 16-byte header, the Atari 7800 skips 128 bytes, the Lynx skips 64,
  the SNES drops a copier header when it finds one, the N64 byte-swaps,
  and every disc console hashes the executable inside the image rather than
  the image. For those, RomM's md5 will not match no matter how well-known
  the game is.

That distinction is why a miss on this plugin gets two different messages.
Telling an operator "not found" when the truth is "you gave me the wrong
kind of hash" would send them looking for the wrong problem.
"""

# RomM platform slug -> RetroAchievements console id.
# Source: RomM 4.9.2 GET /api/platforms/supported, read 2026-07-29.
CONSOLES: dict[str, int] = {
    "genesis": 1,  # Sega Mega Drive/Genesis
    "n64": 2,  # Nintendo 64
    "sfam": 3,  # Super Famicom
    "snes": 3,  # Super Nintendo Entertainment System
    "gb": 4,  # Game Boy
    "gba": 5,  # Game Boy Advance
    "gbc": 6,  # Game Boy Color
    "famicom": 7,  # Family Computer
    "nes": 7,  # Nintendo Entertainment System
    "tg16": 8,  # TurboGrafx-16/PC Engine
    "segacd": 9,  # Sega CD
    "sega32": 10,  # Sega 32X
    "sms": 11,  # Sega Master System/Mark III
    "psx": 12,  # PlayStation
    "lynx": 13,  # Atari Lynx
    "neo-geo-pocket": 14,  # Neo Geo Pocket
    "neo-geo-pocket-color": 14,  # Neo Geo Pocket Color
    "gamegear": 15,  # Sega Game Gear
    "ngc": 16,  # Nintendo GameCube
    "jaguar": 17,  # Atari Jaguar
    "nds": 18,  # Nintendo DS
    "wii": 19,  # Wii
    "ps2": 21,  # PlayStation 2
    "odyssey-2": 23,  # Odyssey 2 / Videopac G7000
    "pokemon-mini": 24,  # Pokemon mini
    "atari2600": 25,  # Atari 2600
    "arcade": 27,  # Arcade
    "virtualboy": 28,  # Virtual Boy
    "msx": 29,  # MSX
    "sg1000": 33,  # SG-1000
    "amiga": 35,  # Amiga
    "atari-st": 36,  # Atari ST/STE
    "acpc": 37,  # Amstrad CPC
    "appleii": 38,  # Apple II
    "saturn": 39,  # Sega Saturn
    "dc": 40,  # Dreamcast
    "psp": 41,  # PlayStation Portable
    "philips-cd-i": 42,  # Philips CD-i
    "3do": 43,  # 3DO Interactive Multiplayer
    "colecovision": 44,  # ColecoVision
    "intellivision": 45,  # Intellivision
    "vectrex": 46,  # Vectrex
    "pc-8800-series": 47,  # PC-8800 Series
    "pc-fx": 49,  # PC-FX
    "atari5200": 50,  # Atari 5200
    "atari7800": 51,  # Atari 7800
    "sharp-x68000": 52,  # Sharp X68000
    "wonderswan": 53,  # WonderSwan
    "wonderswan-color": 53,  # WonderSwan Color
    "neo-geo-cd": 56,  # Neo Geo CD
    "fairchild-channel-f": 57,  # Fairchild Channel F
    "3ds": 62,  # Nintendo 3DS
    "supervision": 63,  # Watara/QuickShot Supervision
    "x1": 64,  # Sharp X1
    "sega-pico": 68,  # Sega Pico
    "mega-duck-slash-cougar-boy": 69,  # Mega Duck/Cougar Boy
    "arduboy": 71,  # Arduboy
    "wasm-4": 72,  # WASM-4
    "arcadia-2001": 73,  # Arcadia 2001
    "interton-vc-4000": 74,  # Interton VC 4000
    "elektor": 75,  # Elektor TV Games Computer
    "turbografx-cd": 76,  # Turbografx-16/PC Engine CD
    "atari-jaguar-cd": 77,  # Atari Jaguar CD
    "nintendo-dsi": 78,  # Nintendo DSi
    "uzebox": 80,  # Uzebox
    "win": 102,  # Windows
}

# Consoles whose RA hash is the plain md5 of the whole file, taken from the
# `rc_hash_buffer(hash, iterator->buffer, iterator->buffer_size, ...)` arm
# of `rc_hash_from_buffer()` in rcheevos `src/rhash/hash.c`. Every other
# console either skips a header, transforms the data, or hashes something
# inside a disc image.
WHOLE_FILE_MD5: frozenset[int] = frozenset(
    {
        1,  # Mega Drive
        4,  # Game Boy
        5,  # Game Boy Advance
        6,  # Game Boy Color
        10,  # Sega 32X
        11,  # Master System
        14,  # Neo Geo Pocket
        15,  # Game Gear
        17,  # Atari Jaguar
        23,  # Magnavox Odyssey2
        24,  # Pokemon Mini
        25,  # Atari 2600
        28,  # Virtual Boy
        29,  # MSX
        30,  # Commodore 64
        32,  # Oric
        33,  # SG-1000
        37,  # Amstrad CPC
        38,  # Apple II
        44,  # ColecoVision
        45,  # Intellivision
        46,  # Vectrex
        47,  # PC-8800
        53,  # WonderSwan
        57,  # Fairchild Channel F
        59,  # ZX Spectrum
        63,  # Watara Supervision
        65,  # TIC-80
        69,  # Mega Duck
        72,  # WASM-4
        73,  # Arcadia 2001
        74,  # Interton VC 4000
        75,  # Elektor TV Games Computer
        79,  # TI-83
        80,  # Uzebox
    }
)


class NeedsMapping(Exception):
    """This platform has no RetroAchievements console, and the message says
    whether that is a gap in the table or a gap in RetroAchievements."""


def console_for(platform: str | None) -> int:
    """The RA console id for a RomM platform slug.

    Raises `NeedsMapping` rather than guessing. Guessing a console id does
    not fail loudly -- it fetches a different system's game list, matches
    nothing, and looks exactly like "RetroAchievements does not have this
    game".
    """
    slug = (platform or "").strip().lower()
    if not slug:
        raise NeedsMapping(
            "this rom has no platform in RomM, and RetroAchievements' game "
            "lists are per-console; set the rom's platform in RomM first"
        )
    try:
        return CONSOLES[slug]
    except KeyError:
        raise NeedsMapping(
            f"platform {slug!r} needs mapping: this plugin has no "
            f"RetroAchievements console id for it. RomM 4.9.2 lists an `ra_id` "
            f"for 66 of its platforms and {slug!r} is not one of them, so "
            f"RetroAchievements most likely does not support it at all -- but "
            f"if it does, add {slug!r} to retroachievements/consoles.py. "
            f"Nothing is guessed here: a wrong console id fetches the wrong "
            f"game list and looks exactly like 'no such game'"
        ) from None


def hashes_whole_file(console_id: int) -> bool:
    """True when RA's hash for this console is the file's own md5."""
    return console_id in WHOLE_FILE_MD5
