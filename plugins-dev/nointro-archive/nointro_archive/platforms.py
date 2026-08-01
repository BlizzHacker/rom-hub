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
    # --- Reachable only through the census. -------------------------------
    #
    # Everything below was enumerated by `census.py` and is unreachable
    # from the shipped `collections` list. It is mapped here because a
    # directory this plugin can name the machine for should not be
    # catalogued as "platform unknown" merely because nobody typed it into
    # a config key.
    #
    # **These overlap the sets above, and that is now the right answer.**
    # The note that used to sit here said `NoIntro-Atari/Atari - 2600` was
    # deliberately unmapped because "two directories mapping to one
    # platform would list every Atari game twice". That was correct when a
    # search concatenated directories and printed the result. It is wrong
    # now: `rom_hub.grouping` merges on a matching sha1 before it looks at
    # a name, and Archive.org publishes a sha1 for every file -- measured,
    # `nointro-2600` and `NoIntro-Atari` share **523 byte-identical
    # archives**, which collapse on proof rather than on hope. Refusing to
    # catalogue a set because a deduplicator might have to do its job is
    # how a catalogue stays incomplete on purpose.
    "nointro-2600": "atari2600",
    "nointro-atari/atari - 2600": "atari2600",
    "nointro-atari/atari - 5200": "atari5200",
    "nointro-atari/atari - 7800": "atari7800",
    # Three older uploads of sets the dotted items also carry. Kept for the
    # same reason: the overlap is measurable and the deduplicator measures
    # it. `NoIntroSegaMegaDriveGenesis2019July30` and its January sibling
    # share 1,726 hashes with each other and none with `nointro.md`, which
    # is a 2025 rebuild -- so the three together are a wider corpus than
    # any one of them, not three copies of one.
    "nointrosegamegadrivegenesis2019july30": "genesis",
    "nointrosegamegadrivegenesis2019jan26": "genesis",
    "nointrocommodoreamiga2018oct12mia31": "amiga",
    # The C64 item keeps its three dump families in subdirectories. All
    # three are the same machine: PP is the Preservation Project's disk
    # images, Tapes are tape dumps, and the bare directory is cartridges
    # and cracked releases.
    "nointro-commodore-64_202302/commodore - 64": "c64",
    "nointro-commodore-64_202302/commodore - 64 (pp)": "c64",
    "nointro-commodore-64_202302/commodore - 64 (tapes)": "c64",
    # Two more WonderSwan uploads, one flat and one with the two machines
    # in subdirectories.
    "nointro-bandai-wonderswanwonderswan-color/bandai - wonderswan": "wonderswan",
    "nointro-bandai-wonderswanwonderswan-color/bandai - wonderswan color":
        "wonderswan-color",
    "nointro_bandiwonderswan": "wonderswan",
    "nointro_bandiwonderswancolor": "wonderswan-color",
    # The other spelling of the Virtual Boy item already mapped above.
    "nointro_virtualboy": "virtualboy",
    # **And an item whose title is simply wrong.** `NoIntroNintendo` is
    # labelled "No Intro - Nintendo" and is a Virtual Boy set: 3-D Tetris,
    # Bound High, Galactic Pinball, Innsmouth no Yakata. Not a guess from
    # the titles -- 31 of its 34 files are byte-identical with
    # `NoIntroVirtualBoy`, which is what the census's hashes are for. A
    # name-based mapper would have filed this under "Nintendo" or refused
    # it; the evidence says Virtual Boy.
    "nointronintendo": "virtualboy",
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


def known_directories() -> list[str]:
    """Every directory this plugin can name the machine for.

    The table's own keys, which are case-folded. Callers match against
    them case-insensitively and keep the caller's spelling for the URL --
    Archive.org's paths are case-sensitive and the table is not.

    Exists so `importer` can accept a source id the census produced. The
    census enumerates all 71 `identifier:nointro*` items; `collections`
    lists 25 directories. Without this the Hub would catalogue a ROM and
    then refuse to import it because nobody had typed its directory into a
    *search* config key.
    """
    return list(DIRECTORY_PLATFORMS)
