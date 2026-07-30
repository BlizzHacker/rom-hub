"""libretro content directory <-> RomM platform slug.

`https://buildbot.libretro.com/assets/cores/` is one directory per system,
and the directory name is the only statement anywhere about what a file in
it runs on. So this table is the only thing standing between a homebrew
cartridge and another console's shelf, and it is an exact-match lookup
with **no fallback and no prefix matching**.

Both sides are read listings rather than lists from memory:

* the keys are the 56 directory names served by `/assets/cores/`, read
  2026-07-29 -- note `Coleco - Colecovision` with a lowercase `v` and
  `Nintendo - GameBoy` with no space, both of which differ from the
  spellings the *thumbnail* server uses for the same machines. Copying
  the thumbnail table would have produced two silent 404s;
* the values are RomM platform slugs, the set `libretro-thumbnails`
  verified against RomM 4.9.2's `GET /api/platforms/supported`.

Three groups are **deliberately absent**, and each absence is a refusal
this plugin can explain rather than a row somebody forgot:

* **Not a platform.** `Images`, `Video` and `Utilities` hold screensavers,
  test videos and tools. Nothing in them is a game, so there is no shelf
  to file them on.
* **A single game, not a system.** `DOOM`, `Quake`, `Quake II`,
  `Cave Story`, `Cannonball`, `Dinothawr`, `Rick Dangerous`,
  `Tomb Raider`, `Wolfenstein 3D`, `Jump 'n Bump`, `Super Bros War`,
  `EasyRPG` and `PocketCDG` are game data for one engine core each. RomM
  has no slug for "the DOOM engine".
* **A fantasy console RomM does not carry.** `Uzebox`, `Vircon32`,
  `VaporSpec`, `MicroW8`, `LowResNX`, `PuzzleScript`, `ChaiLove`,
  `Lutro`, `CHIP-8` and `Arduous` are real systems with real homebrew and
  no RomM platform to put them on. `TIC-80` and `WASM-4` are in the table
  precisely because RomM *does* carry those two.

And one is absent because it is **ambiguous**, which is worse than
missing: `Nintendo - GameCube - Wii` is one directory holding two
consoles. RomM has `ngc` and `wii` as separate platforms and the
directory name does not say which a given file is, so it is listed in
`AMBIGUOUS` and refuses with that sentence instead of picking.
"""

# libretro content directory -> RomM platform slug.
SYSTEMS: dict[str, str] = {
    "Arcade": "arcade",
    "Atari - 2600": "atari2600",
    "Bandai - WonderSwan Color": "wonderswan-color",
    "Coleco - Colecovision": "colecovision",
    "DOS": "dos",
    "GCE - Vectrex": "vectrex",
    "Handheld Electronic Game": "handheld-electronic-lcd",
    "Mattel - Intellivision": "intellivision",
    "NEC - PC Engine - TurboGrafx 16": "tg16",
    "NEC - PC Engine SuperGrafx": "supergrafx",
    "Nintendo - GameBoy": "gb",
    "Nintendo - GameBoy Advance": "gba",
    "Nintendo - Nintendo 3DS": "3ds",
    "Nintendo - Nintendo 64": "n64",
    "Nintendo - Nintendo Entertainment System": "nes",
    "Nintendo - Pokemon Mini": "pokemon-mini",
    "Nintendo - Super Nintendo Entertainment System": "snes",
    "Nintendo - Virtual Boy": "virtualboy",
    "SNK - Neo Geo Pocket": "neo-geo-pocket",
    "ScummVM": "scummvm",
    "Sega - Dreamcast": "dc",
    "Sega - Game Gear": "gamegear",
    "Sega - Master System - Mark III": "sms",
    "Sega - Mega Drive - Genesis": "genesis",
    "Sega - Saturn": "saturn",
    "Sony - PlayStation": "psx",
    "Sony - PlayStation Portable": "psp",
    "TIC-80": "tic-80",
    "WASM-4": "wasm-4",
}

# RomM slug -> directory. A plain inversion, so it stays correct by
# construction when a row is added above.
DIRECTORIES: dict[str, str] = {slug: name for name, slug in SYSTEMS.items()}

#: Directories that name more than one machine. Refused by name rather
#: than resolved, because both answers are defensible and only one is
#: right for any given file.
AMBIGUOUS: dict[str, str] = {
    "Nintendo - GameCube - Wii": (
        "it is one directory holding both GameCube and Wii content, and RomM "
        "keeps 'ngc' and 'wii' as separate platforms. Pass --platform to say "
        "which one this file is."
    ),
}

#: Directories that hold no games at all. Named separately so the refusal
#: can say "not a platform" instead of "needs mapping", which would invite
#: somebody to add a row.
NOT_GAMES = frozenset({"Images", "Video", "Utilities"})


def platform_for(directory: str) -> str | None:
    """The RomM slug for a libretro content directory, or None.

    None means "not in the table". Callers must turn it into a visible
    refusal naming the directory; it never means "use a default".
    """
    if not isinstance(directory, str):
        return None
    return SYSTEMS.get(directory.strip())


def directory_for(romm_slug: str) -> str | None:
    """The content directory for a RomM slug, or None.

    None means this source has nothing for that platform -- an empty
    result, not an error. libretro publishes free content for the systems
    above and for no others.
    """
    if not isinstance(romm_slug, str):
        return None
    return DIRECTORIES.get(romm_slug.strip().lower())


def why_unmapped(directory: str) -> str:
    """The sentence that explains one unmapped directory.

    Three different reasons reach an operator as three different
    sentences, because "add a row to platforms.py" is right for exactly
    one of them.
    """
    name = (directory or "").strip()
    if name in AMBIGUOUS:
        return f"libretro content directory {name!r} cannot be mapped: {AMBIGUOUS[name]}"
    if name in NOT_GAMES:
        return (
            f"libretro content directory {name!r} holds no games -- it is "
            f"screensavers, test videos or tools -- so there is no RomM "
            f"platform to file it under."
        )
    return (
        f"libretro content directory {name!r} needs mapping: it is not in this "
        f"plugin's directory -> RomM platform table, and guessing would file "
        f"the ROM under the wrong system. If it is a real RomM platform, add it "
        f"to libretro_content/platforms.py; if it is a single-game engine port "
        f"(DOOM, Quake, Cave Story) or a fantasy console RomM does not carry, "
        f"it belongs absent."
    )
