"""The ROM Depot directory names -> canonical ROMarr platform slugs.

The site states a platform through the directory containing a file, not on
the file object itself.  Matching is exact after punctuation and whitespace
normalisation.  There is deliberately no substring or prefix fallback: an
unknown folder is omitted rather than filed under a plausible wrong system.
"""

from __future__ import annotations

import re


def normalise_label(value: object) -> str:
    """Fold a human directory label into the exact alias-table key."""

    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


# Values are canonical slugs from ROMarr's platform table.  The aliases cover
# the names normally used by ROM catalogues as well as vendor-prefixed names.
PLATFORM_ALIASES: dict[str, str] = {
    "3do": "3do",
    "panasonic 3do": "3do",
    "amiga": "amiga",
    "commodore amiga": "amiga",
    "arcade": "arcade",
    "mame": "arcade",
    "atari 2600": "atari2600",
    "atari 5200": "atari5200",
    "atari 7800": "atari7800",
    "atari jaguar": "jaguar",
    "jaguar": "jaguar",
    "atari lynx": "lynx",
    "lynx": "lynx",
    "atari st": "atari-st",
    "commodore 64": "c64",
    "c64": "c64",
    "colecovision": "colecovision",
    "sega dreamcast": "dc",
    "dreamcast": "dc",
    "famicom": "famicom",
    "nintendo famicom": "famicom",
    "game gear": "gamegear",
    "sega game gear": "gamegear",
    "game boy": "gb",
    "nintendo game boy": "gb",
    "gb": "gb",
    "game boy advance": "gba",
    "nintendo game boy advance": "gba",
    "gba": "gba",
    "game boy color": "gbc",
    "game boy colour": "gbc",
    "nintendo game boy color": "gbc",
    "nintendo game boy colour": "gbc",
    "gbc": "gbc",
    "nintendo gamecube": "ngc",
    "gamecube": "ngc",
    "intellivision": "intellivision",
    "sega genesis": "genesis-slash-megadrive",
    "sega mega drive": "genesis-slash-megadrive",
    "genesis": "genesis-slash-megadrive",
    "mega drive": "genesis-slash-megadrive",
    "msx": "msx",
    "nintendo 64": "n64",
    "n64": "n64",
    "nintendo ds": "nds",
    "nds": "nds",
    "nintendo 3ds": "3ds",
    "3ds": "3ds",
    "neo geo pocket": "neo-geo-pocket",
    "neo geo pocket color": "neo-geo-pocket-color",
    "neo geo pocket colour": "neo-geo-pocket-color",
    "nintendo entertainment system": "nes",
    "nintendo nes": "nes",
    "nes": "nes",
    "sony playstation": "psx",
    "playstation": "psx",
    "playstation 1": "psx",
    "ps1": "psx",
    "psx": "psx",
    "sony playstation 2": "ps2",
    "playstation 2": "ps2",
    "ps2": "ps2",
    "sony playstation 3": "ps3",
    "playstation 3": "ps3",
    "ps3": "ps3",
    "sony playstation portable": "psp",
    "playstation portable": "psp",
    "psp": "psp",
    "sega 32x": "sega32",
    "32x": "sega32",
    "sega cd": "segacd",
    "mega cd": "segacd",
    "sega master system": "sms",
    "master system": "sms",
    "sega saturn": "saturn",
    "saturn": "saturn",
    "sharp x68000": "sharp-x68000",
    "super nintendo": "snes",
    "super nintendo entertainment system": "snes",
    "snes": "snes",
    "pc engine": "turbografx16--1",
    "turbografx 16": "turbografx16--1",
    "turbografx16": "turbografx16--1",
    "nec turbografx 16": "turbografx16--1",
    "vectrex": "vectrex",
    "nintendo virtual boy": "virtualboy",
    "virtual boy": "virtualboy",
    "nintendo wii": "wii",
    "wii": "wii",
    "wonderswan": "wonderswan",
    "wonderswan color": "wonderswan-color",
    "wonderswan colour": "wonderswan-color",
    "zx spectrum": "zxs",
}


def platform_for(value: object) -> str | None:
    """Return the canonical ROMarr slug, or nothing for an unknown folder."""

    return PLATFORM_ALIASES.get(normalise_label(value))


def platform_from_path(path: str, stated: object = "") -> tuple[str, str] | None:
    """Resolve the stated platform or one of the first path directories.

    Returns ``(slug, source_label)`` so results can preserve what the site
    called the directory while exposing a canonical platform to ROMarr.
    """

    candidates = [str(stated or "").strip()]
    candidates.extend(path.replace("\\", "/").split("/")[:-1][:4])
    seen: set[str] = set()
    for candidate in candidates:
        key = normalise_label(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        mapped = PLATFORM_ALIASES.get(key)
        if mapped:
            return mapped, candidate
    return None
