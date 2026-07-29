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
"""

# Directory (or Archive.org item id) -> RomM platform slug.
DIRECTORY_PLATFORMS: dict[str, str] = {
    # --- Archive.org No-Intro items: the live default set. -----------------
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
