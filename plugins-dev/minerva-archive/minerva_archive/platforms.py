"""Host platform slug <-> MiNERVA console name(s).

MiNERVA's search API filters by `console`, whose values are its own
long-form names ("Sony PlayStation 2", "Sega Mega Drive", "Nintendo Super
Famicom"). A host -- RomM, ROMarr -- searches by *platform slug* ("ps2",
"genesis", "snes"). The plugin is the only place that knows both
vocabularies, so the mapping lives here.

Rules, in the order they matter:

* **Every console name below was checked against the live filters
  endpoint** (`GET /v1/api/rom/filters`, 839 consoles, 2026-11-09) rather
  than typed from memory. A name MiNERVA does not list filters nothing,
  and silently: the API accepts any string.
* **One slug may name several consoles.** Redump, No-Intro and TOSEC
  disagree on what to call a machine ("Sega Mega Drive" vs "Sega
  Genesis"), and MiNERVA keeps all spellings as distinct filter values.
  A search for one slug therefore issues one request per console name
  and merges -- which is what makes a short series name findable past
  the first page of the wrong-spelling rows.
* **The reverse map is built only from unambiguous names.** "SNK Neo-Geo"
  is claimed by two RomM slugs (AES and MVS); guessing one would file a
  result under a platform nobody chose, so an ambiguous console maps to
  None and the host keeps matching on title, as it did before.

Add a slug by adding a line. Keys are compared case-sensitively because
they are the host's own slugs, not free text.
"""

from __future__ import annotations

#: RomM platform slug -> MiNERVA console filter values (all verified live).
PLATFORM_CONSOLES: dict[str, tuple[str, ...]] = {
    # --- Nintendo -----------------------------------------------------------
    "nes": ("Nintendo Entertainment System", "Nintendo Famicom"),
    "famicom": ("Nintendo Famicom",),
    "fds": ("Nintendo Famicom Disk System", "Nintendo Family Computer Disk System"),
    "snes": ("Super Nintendo Entertainment System", "Nintendo Super Famicom"),
    "sfam": ("Nintendo Super Famicom",),
    "n64": ("Nintendo 64",),
    "gb": ("Nintendo Game Boy", "Game Boy"),
    "gbc": ("Nintendo Game Boy Color", "Game Boy Color"),
    "gba": ("Nintendo Game Boy Advance",),
    "nds": ("Nintendo DS",),
    "nintendo-dsi": ("Nintendo DSi",),
    "3ds": ("Nintendo 3DS",),
    "new-nintendo-3ds": ("New Nintendo 3DS",),
    "virtualboy": ("Nintendo Virtual Boy", "Virtual Boy"),
    "pokemon-mini": ("Nintendo Pokemon Mini",),
    "satellaview": ("Nintendo Satellaview",),
    "sufami-turbo": ("Nintendo Sufami Turbo",),
    "game-and-watch": ("Nintendo Game & Watch",),
    "gamecube": ("Nintendo GameCube",),
    "wii": ("Nintendo Wii",),
    "wiiu": ("Nintendo Wii U",),
    "switch": ("Nintendo Switch",),
    # --- Sony -----------------------------------------------------------------
    "psx": ("Sony PlayStation",),
    "ps2": ("Sony PlayStation 2",),
    "ps3": ("Sony PlayStation 3",),
    "psp": ("Sony PlayStation Portable", "Sony PSP"),
    "psvita": ("Sony PlayStation Vita",),
    # --- Sega -----------------------------------------------------------------
    "genesis": (
        "Sega Mega Drive",
        "Sega Genesis",
        "Sega Mega Drive - Genesis",
        "Sega Mega Drive & Genesis",
    ),
    "sms": ("Sega Master System", "Master System", "Sega Mark III & Master System"),
    "sega-mark-iii": ("Sega Master System - Mark III",),
    "segacd": ("Sega CD", "Sega Mega CD", "Mega CD & Sega CD"),
    "sega32": ("Sega 32X",),
    "gamegear": ("Sega Game Gear", "Game Gear"),
    "dreamcast": ("Sega Dreamcast",),
    "saturn": ("Sega Saturn",),
    "sg1000": ("Sega SG-1000",),
    # --- Atari ----------------------------------------------------------------
    "atari2600": ("Atari 2600", "Atari 2600 & VCS"),
    "atari5200": ("Atari 5200",),
    "atari7800": ("Atari 7800",),
    "atari8bit": ("Atari 8-bit Family", "Atari 8bit"),
    "lynx": ("Atari Lynx",),
    "jaguar": ("Atari Jaguar",),
    "jaguarcd": ("Atari Jaguar CD",),
    "atari-st": ("Atari ST",),
    "atari-falcon": ("Atari Falcon030",),
    # --- NEC / SNK / Bandai -----------------------------------------------------
    "tg16": ("NEC TurboGrafx-16", "TurboGrafx-16", "NEC PC Engine", "PC Engine"),
    "turbografx-cd": (
        "NEC TurboGrafx-CD",
        "NEC PC Engine CD",
        "PC Engine CD Redump Supplement",
    ),
    "supergrafx": ("NEC SuperGrafx", "SuperGrafx", "NEC PC Engine SuperGrafx"),
    "pc-fx": ("NEC PC-FX",),
    "neogeoaes": ("SNK Neo-Geo AES",),
    "neogeomvs": ("SNK Neo-Geo",),
    "neo-geo-cd": ("SNK Neo Geo CD", "SNK Neo-Geo CD"),
    "neo-geo-pocket": ("SNK Neo Geo Pocket", "SNK Neo-Geo Pocket", "NeoGeo Pocket"),
    "neo-geo-pocket-color": (
        "SNK Neo Geo Pocket Color",
        "SNK Neo-Geo Pocket Color",
    ),
    "wonderswan": ("Bandai WonderSwan", "WonderSwan"),
    "wonderswan-color": ("Bandai WonderSwan Color",),
    # --- Other consoles ---------------------------------------------------------
    "3do": ("3DO Interactive Multiplayer",),
    "philips-cd-i": ("Philips CD-i",),
    "arcade": ("Arcade",),
    "xbox": ("Microsoft Xbox",),
    "xbox360": ("Microsoft Xbox 360",),
    # --- Computers --------------------------------------------------------------
    "amiga": ("Commodore Amiga",),
    "amiga-cd32": ("Commodore Amiga CD32",),
    "amiga-cdtv": ("Commodore Amiga CDTV",),
    "c64": ("Commodore 64", "Commodore C64"),
    "c128": ("Commodore C128",),
    "vic-20": ("Commodore VIC-20", "Commodore VIC20"),
    "c-plus-4": ("Commodore Plus-4", "Commodore C16, C116 & Plus-4"),
    "cpet": ("Commodore PET",),
    "msx": ("MSX", "Microsoft MSX"),
    "msx2": ("MSX2", "Microsoft MSX2"),
    "zxs": ("Sinclair ZX Spectrum", "ZX Spectrum"),
    "acpc": ("Amstrad CPC",),
    "bbcmicro": ("Acorn BBC",),
    "dos": ("IBM PC and Compatibles", "IBM PC compatible", "IBM PC Compatibles"),
}


def consoles_for_platform(slug: str | None) -> tuple[str, ...]:
    """The MiNERVA console filter values for one host platform slug.

    Empty when the slug is None or unmapped -- the caller decides what an
    unmapped slug means (search unfiltered, not search nothing: the rows
    exist, and a host that cannot use them still can by title).
    """
    if not slug:
        return ()
    return PLATFORM_CONSOLES.get(slug, ())


def _build_reverse() -> dict[str, str]:
    """Console name -> slug, for names exactly one slug claims.

    Built, not maintained: a hand-kept reverse table drifts from the
    forward one the first time somebody adds a synonym. An ambiguous
    console ("SNK Neo-Geo" is both neogeoaes's filter spelling in other
    catalogues and neogeomvs's) is dropped rather than guessed -- see the
    module docstring.
    """
    seen: dict[str, str | None] = {}
    for slug, consoles in PLATFORM_CONSOLES.items():
        for console in consoles:
            if console in seen and seen[console] != slug:
                seen[console] = None  # ambiguous: two slugs claim it
            else:
                seen.setdefault(console, slug)
    return {c: s for c, s in seen.items() if s is not None}


_CONSOLE_TO_PLATFORM: dict[str, str] = _build_reverse()


def platform_for_console(console: str | None) -> str | None:
    """The host platform slug a MiNERVA console name belongs to, or None.

    None is an honest answer, not a failure: MiNERVA lists 839 consoles
    (bitsavers, magazines, BIOS dumps, calculators) and this plugin maps
    the game machines a ROM library actually files. A row whose console
    is unmapped keeps `platform=None` and the host matches it on title,
    exactly as every row did before this mapping existed.
    """
    if not console:
        return None
    return _CONSOLE_TO_PLATFORM.get(console)
