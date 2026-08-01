"""Index directory name -> RomM platform slug.

A directory-index mirror publishes exactly one thing about a ROM's platform:
the name of the directory it sits in. On Myrient that was
`No-Intro/Nintendo - Game Boy/`; on the Archive.org No-Intro items this
plugin ships against it is the item id, `nointro.gg`. Either way the whole
signal is a folder name, so that is what this table keys on.

**Exact match, no fallback,** for the same reason `archive_org.platforms`
gives: an unmapped directory raises "needs mapping" and names itself, and
the import stops. The alternative -- a prefix or fuzzy rule over
`nointro.*` -- looks free and is wrong exactly where it matters, because
the suffix is an abbreviation chosen by whoever uploaded the set
(`ms-mkiii` is a Master System, `ca` is an Amiga, `sg` is a SuperGrafx and
not a Game Gear). Nothing about those is derivable.

Values were checked against RomM's platform-slug enum
(`backend/handler/metadata/base_handler.py`). A slug RomM does not know
fails later and much less usefully.

Add a directory by adding a line. Keys are compared case-insensitively
after stripping surrounding slashes and whitespace, so
`No-Intro/Nintendo - Game Boy/` and its unslashed form are the same key.

**Two Archive.org items are deliberately unmapped and it is worth saying
which.** `NoIntroSatellaview` (206 archives) and `NoIntroSufamiTurbo`
(13) are Super Nintendo *peripherals*: BS-X broadcast cartridges and
Sufami Turbo carts. RomM carries `satellaview` and `sufami-turbo` as
their own platform slugs, and neither is in RomM's EmulatorJS core map --
so an import would be catalogue-only, which is allowed, but the tempting
shortcut is not: filing them under `snes` would be a remap onto hardware
they are not, since neither boots without the peripheral's own BIOS and
mapper. They stay out until somebody wants them enough to write the
NO_EQUIVALENT rows, and adding them is two lines here plus two there.
"""

# Directory (or Archive.org item id) -> RomM platform slug.
DIRECTORY_PLATFORMS: dict[str, str] = {
    # --- Archive.org No-Intro items: the live default set. -----------------
    #
    # `identifier:nointro*` finds 71 items on Archive.org (2026-08-01) and
    # most of them cannot be used: a single 12 GB .7z, a DAT-only upload,
    # a "merged" dump with a private tree layout. The ones below are the
    # ones that are what this plugin needs -- **a flat directory of
    # per-game archives for exactly one machine** -- and each was checked
    # against `archive.org/metadata/<id>` rather than guessed from a title.
    #
    # Four of them are an item's *subdirectory*, not an item, because the
    # uploader put several systems in one item and named the folders after
    # the machines. `NoIntro-Atari/Atari - Lynx` is a real index page and
    # a perfectly good key -- and it is the only way those five systems are
    # reachable at all, since the item root holds no ROMs of its own.
    "nointro.32x": "sega32",
    "nointro.atari-2600": "atari2600",
    "nointro.atari-5200": "atari5200",
    "nointro.atari-7800": "atari7800",
    "nointro.c64": "c64",
    # "ca" is Commodore Amiga. Nothing in the string says so.
    "nointro.ca": "amiga",
    "nointro.gbamultiboot": "gba",
    "nointro.gg": "gamegear",
    # "md" is Mega Drive, which RomM files under the Genesis slug.
    "nointro.md": "genesis",
    "nointro.ms-mkiii": "sms",
    # "sg" is the PC Engine SuperGrafx, NOT the Sega Game Gear -- the exact
    # collision a prefix rule would get wrong.
    "nointro.sg": "supergrafx",
    "nointro.tg-16": "tg16",
    "nointro.ws": "wonderswan",
    "nointro.wsc": "wonderswan-color",
    # --- The Nintendo sets, which this plugin could not see at all. -------
    # There is no `nointro.gb` or `nointro.snes` item in the dotted family
    # -- `nointro.snes_202203` and `nointro.n64_202203` are each one giant
    # .zip, which is not a directory of games -- so the two biggest gaps
    # were filled by uploads under different names. Both are flat, both
    # are per-game .zip archives, both were counted from the item's own
    # file list: 1,958 Game Boy and 1,746 Super Nintendo.
    "nointro-nintendo-gameboy": "gb",
    "nointro-snes": "snes",
    "nointrovirtualboy": "virtualboy",
    "nointro_pokemonmini": "pokemon-mini",
    # The Arduboy is an ATmega32u4 handheld and the NUON is a media
    # processor inside a DVD player. Both are real RomM platforms, both
    # are catalogue-only -- see rom_hub.playability.NO_EQUIVALENT, which
    # names each machine and what it is not.
    "nointroarduboy": "arduboy",
    "nointrovmlabs": "nuon",
    # --- Per-system subdirectories of multi-system items. -----------------
    # The item root of each of these holds no ROMs; the machines are one
    # level down, in folders the uploader named after them. Spelled here
    # exactly as Archive.org serves them, spaces, parentheses and all --
    # `index_url` percent-encodes, so the key is the human spelling.
    "nointro-atari/atari - jaguar (j64)": "jaguar",
    "nointro-atari/atari - lynx": "lynx",
    "nointro-coleco/coleco - colecovision": "colecovision",
    "nointro-commodore-plus4-vic20/commodore - vic-20": "vic-20",
    "nointro-commodore-plus4-vic20/commodore - plus-4": "c-plus-4",
    # `NoIntro-Atari` also carries `Atari - 2600`, `Atari - 5200` and
    # `Atari - 7800`, which are deliberately absent: the dotted
    # `nointro.atari-*` items are the same three machines from a 2025
    # rebuild rather than a 2019 one, and two directories mapping to one
    # platform would list every Atari game twice.
    # --- Myrient's own No-Intro layout, kept so the plugin can be repointed
    #     at any mirror that reproduces it. myrient.erista.me itself is gone
    #     (see README); these keys are the directory names it used. --------
    "no-intro/nintendo - game boy": "gb",
    "no-intro/nintendo - game boy color": "gbc",
    "no-intro/nintendo - game boy advance": "gba",
    "no-intro/nintendo - nintendo entertainment system (headered)": "nes",
    "no-intro/nintendo - super nintendo entertainment system": "snes",
    "no-intro/nintendo - nintendo 64 (bigendian)": "n64",
    "no-intro/sega - mega drive - genesis": "genesis",
    "no-intro/sega - master system - mark iii": "sms",
    "no-intro/sega - game gear": "gamegear",
    "no-intro/sega - 32x": "sega32",
    "no-intro/atari - 2600": "atari2600",
    "no-intro/atari - 5200": "atari5200",
    "no-intro/atari - 7800": "atari7800",
    "no-intro/atari - lynx": "lynx",
    "no-intro/bandai - wonderswan": "wonderswan",
    "no-intro/bandai - wonderswan color": "wonderswan-color",
    "no-intro/coleco - colecovision": "colecovision",
    "no-intro/commodore - commodore 64": "c64",
    "no-intro/commodore - amiga": "amiga",
    "no-intro/gce - vectrex": "vectrex",
    "no-intro/mattel - intellivision": "intellivision",
    "no-intro/nec - pc engine - turbografx-16": "tg16",
    "no-intro/nec - pc engine supergrafx": "supergrafx",
    "no-intro/snk - neo geo pocket": "neo-geo-pocket",
    "no-intro/snk - neo geo pocket color": "neo-geo-pocket-color",
}


def normalise(directory: str) -> str:
    if not isinstance(directory, str):
        return ""
    return directory.strip().strip("/").strip().lower()


def platform_for(directory: str) -> str | None:
    """The RomM platform slug for an index directory, or None.

    None means "not in the table", which callers must turn into a visible
    refusal naming the directory. It never means "use a default".
    """
    return DIRECTORY_PLATFORMS.get(normalise(directory))
