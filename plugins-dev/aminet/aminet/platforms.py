"""Aminet architecture -> RomM platform, and which `game/` shelves hold games.

Two tables, because Aminet answers two different questions badly if you
squint at either one.

## Architecture

Aminet is not an Amiga archive. It is the archive for *the whole family
of systems that grew out of the Amiga*, and it says which one each upload
targets in the `Architecture:` line of its `.readme` and as an icon in
every search row. Live counts over a `dir=game` search on 2026-07-29:
`m68k-amigaos` 98, `generic` 28, `ppc-amigaos` 16, `ppc-morphos` 15,
`i386-aros` 9, `ppc-warpup` 3, `ppc-powerup` 3, `i386-amithlon` 3.

RomM has **`amiga`**, and `amiga` means the Commodore Amiga. So:

* `m68k-amigaos` is that machine, and maps.
* `ppc-warpup` and `ppc-powerup` also map, and this is the one judgement
  call in the file. Those are PowerPC binaries for the Blizzard and
  CyberStorm accelerator *cards*, which plug into a Commodore Amiga and
  run under AmigaOS 3.x on it. The physical requirement is an Amiga; the
  file will not run on anything else. Filing them anywhere but `amiga`
  would be the misfiling, not the caution.
* **Everything else refuses, by name.** `ppc-amigaos` is AmigaOS 4 on
  AmigaOne and Sam hardware; `ppc-morphos` is MorphOS on Pegasos and Mac
  hardware; the `*-aros` family is AROS on PC hardware; `i386-amithlon`
  is Amithlon on a PC. Those are four *different computers*, none of them
  a Commodore Amiga, and RomM has no platform for any of them. Folding
  them into `amiga` would fill an Amiga shelf with software an Amiga
  cannot run, and nothing downstream would ever say so.
* `generic` refuses too, and it is worth saying why separately: it means
  the upload targets no machine -- source code, a font, a data set. It is
  not an unknown architecture, it is the absence of one.

## `game/` subdirectories

Aminet's `game/` tree has 18 shelves and four of them hold no games:
`data` is data files, `edit` is level editors, `hint` is walkthrough
documents and `patch` is patches. The descriptions below are Aminet's
own, read from `https://aminet.net/tree?path=game` on 2026-07-29.

`demo` is in and is worth a sentence: Aminet describes it as "Demos of
commercial games", which sounds like the wrong side of the line and is
not. A publisher's playable demo was published *for* free distribution --
that is what made it admissible to Aminet in the first place -- so it is
a real, freely-redistributable game, just a short one.
"""

# Aminet architecture -> RomM platform slug.
ARCHITECTURES: dict[str, str] = {
    "m68k-amigaos": "amiga",
    "ppc-warpup": "amiga",
    "ppc-powerup": "amiga",
}

#: RomM slug -> the architectures that satisfy it. Derived, so it stays
#: correct when a row is added above.
BY_PLATFORM: dict[str, tuple[str, ...]] = {}
for _arch, _slug in ARCHITECTURES.items():
    BY_PLATFORM[_slug] = BY_PLATFORM.get(_slug, ()) + (_arch,)

#: Architectures Aminet publishes that deliberately do not map, and the
#: machine each one actually is. Named individually so a refusal can say
#: "MorphOS is not a Commodore Amiga" instead of "unknown".
NOT_AN_AMIGA: dict[str, str] = {
    "ppc-amigaos": "AmigaOS 4 on AmigaOne/Sam PowerPC hardware",
    "ppc-morphos": "MorphOS on Pegasos or PowerPC Macintosh hardware",
    "i386-aros": "AROS on 32-bit PC hardware",
    "x86_64-aros": "AROS on 64-bit PC hardware",
    "ppc-aros": "AROS on PowerPC hardware",
    "arm-aros": "AROS on ARM hardware",
    "i386-amithlon": "Amithlon on PC hardware",
    "generic": (
        "no machine at all -- 'generic' is source code, data or documents, "
        "which is the absence of an architecture rather than an unknown one"
    ),
    "other": "Aminet's own catch-all for an architecture it does not name",
}

#: Aminet `game/` subdirectory -> (Aminet's own description, holds games).
GAME_DIRS: dict[str, tuple[str, bool]] = {
    "game/2play": ("2 and more player games", True),
    "game/actio": ("Action games", True),
    "game/board": ("Board games", True),
    "game/data": ("Data files for games", False),
    "game/demo": ("Demos of commercial games", True),
    "game/edit": ("Game editors", False),
    "game/gag": ("Gag programs", True),
    "game/hint": ("Game hint documents", False),
    "game/jump": ("Jump-n-run games", True),
    "game/misc": ("Miscellaneous games", True),
    "game/patch": ("Patches for games", False),
    "game/race": ("Racing games", True),
    "game/role": ("Role, adventure games", True),
    "game/shoot": ("Shoot-em-up games", True),
    "game/strat": ("Strategy games", True),
    "game/text": ("Text based games", True),
    "game/think": ("Mind games", True),
    "game/wb": ("Workbench games", True),
}


def platform_for(architecture: str) -> str | None:
    """The RomM slug for an Aminet architecture, or None.

    None means "not in the table". Callers must turn it into a visible
    refusal naming the architecture; it never means "use a default".
    """
    if not isinstance(architecture, str):
        return None
    return ARCHITECTURES.get(_normalise(architecture))


def why_unmapped(architecture: str) -> str:
    """The sentence explaining one unmapped architecture."""
    arch = _normalise(architecture)
    if not arch:
        return (
            "this Aminet package declares no Architecture:, so there is nothing "
            "to map to a RomM platform. Pass --platform to say where it should "
            "be filed."
        )
    machine = NOT_AN_AMIGA.get(arch)
    if machine:
        return (
            f"Aminet architecture {arch!r} is {machine}, not a Commodore Amiga. "
            f"RomM's 'amiga' platform is the Commodore machine and has no slug "
            f"for this one, so filing it there would put software on an Amiga "
            f"shelf that an Amiga cannot run. Pass --platform if you keep a "
            f"shelf for it."
        )
    return (
        f"Aminet architecture {arch!r} needs mapping: it is not in this "
        f"plugin's architecture -> RomM platform table, and guessing would "
        f"file the package under the wrong system. Add it to "
        f"aminet/platforms.py."
    )


def holds_games(directory: str) -> bool | None:
    """True/False for a known `game/` shelf, None for anything else.

    None means "not a `game/` directory at all" -- `util/`, `mods/`,
    `pix/` and the other twelve top-level trees. A ROM library has no use
    for any of them, and the refusal says which shelf was asked for.
    """
    if not isinstance(directory, str):
        return None
    entry = GAME_DIRS.get(directory.strip().strip("/").lower())
    return None if entry is None else entry[1]


def describe(directory: str) -> str:
    """Aminet's own description of a shelf, or ""."""
    entry = GAME_DIRS.get((directory or "").strip().strip("/").lower())
    return entry[0] if entry else ""


def _normalise(architecture: str) -> str:
    """`ppc-amigaos >= 4.0.0` -> `ppc-amigaos`.

    Real `.readme` files qualify the architecture with a minimum OS
    version, and the qualifier is about the OS rather than the machine.
    Splitting on whitespace is enough and stays enough: Aminet's own
    architecture *icons* carry the bare token, so the two sources of this
    value agree after this call and not before it.
    """
    if not isinstance(architecture, str):
        return ""
    return architecture.strip().split()[0].lower() if architecture.strip() else ""
