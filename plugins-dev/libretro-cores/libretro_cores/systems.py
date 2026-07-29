"""Core id -> the system that core emulates.

**Absence is the answer, not a gap to be filled by guessing.** The
buildbot publishes 218 cores for Linux x86_64 alone and its
`.index-extended` says nothing about what any of them run -- it is a list
of filenames, a date and a crc32. The only machine-readable mapping
libretro ships is inside `assets/frontend/info.zip`, and a plugin cannot
open it: `ctx.http` returns text, deliberately, because a plugin has no
sockets and the broker is not a file transfer service.

So this is a hand-kept table, and a core that is not in it gets
`system=None` -- which `CoreArtifact` allows, and which prints as blank.
That is a true statement ("this plugin does not know"), where a derived
guess would be a false one. `2048` is not a console; `mame2003_plus` is
not "Mame2003 Plus"; and an operator who sees a system name has to be
able to trust it, or the column is worse than empty.

The vocabulary is libretro's own. The system names below are the
directory names served by `https://buildbot.libretro.com/assets/cores/`,
read on 2026-07-29, so a reader comparing this table against libretro's
own material sees the same spellings rather than a private dialect.

Nothing here is load-bearing. `system` is a label an operator reads while
choosing; it is never a RomM platform slug, never a path component, and
never consulted when deciding what to fetch. Adding a row is a one-line
change and cannot break an install.
"""

#: Core id -> emulated system, in libretro's own spelling. Partial by
#: design; see the module docstring.
CORE_SYSTEMS: dict[str, str] = {
    # Nintendo - Nintendo Entertainment System
    "fceumm": "Nintendo - Nintendo Entertainment System",
    "nestopia": "Nintendo - Nintendo Entertainment System",
    "quicknes": "Nintendo - Nintendo Entertainment System",
    "mesen": "Nintendo - Nintendo Entertainment System",
    # Nintendo - Super Nintendo Entertainment System
    "snes9x": "Nintendo - Super Nintendo Entertainment System",
    "snes9x2002": "Nintendo - Super Nintendo Entertainment System",
    "snes9x2005": "Nintendo - Super Nintendo Entertainment System",
    "snes9x2010": "Nintendo - Super Nintendo Entertainment System",
    "bsnes": "Nintendo - Super Nintendo Entertainment System",
    "bsnes_hd_beta": "Nintendo - Super Nintendo Entertainment System",
    "bsnes2014_accuracy": "Nintendo - Super Nintendo Entertainment System",
    "bsnes2014_balanced": "Nintendo - Super Nintendo Entertainment System",
    "bsnes2014_performance": "Nintendo - Super Nintendo Entertainment System",
    "mesen-s": "Nintendo - Super Nintendo Entertainment System",
    # Nintendo - GameBoy
    "gambatte": "Nintendo - GameBoy",
    "sameboy": "Nintendo - GameBoy",
    "tgbdual": "Nintendo - GameBoy",
    "gearboy": "Nintendo - GameBoy",
    # Nintendo - GameBoy Advance
    "mgba": "Nintendo - GameBoy Advance",
    "vbam": "Nintendo - GameBoy Advance",
    "vba_next": "Nintendo - GameBoy Advance",
    "gpsp": "Nintendo - GameBoy Advance",
    # Nintendo - Nintendo 64
    "mupen64plus_next": "Nintendo - Nintendo 64",
    "parallel_n64": "Nintendo - Nintendo 64",
    # Nintendo - GameCube - Wii
    "dolphin": "Nintendo - GameCube - Wii",
    # Nintendo - Nintendo 3DS
    "citra": "Nintendo - Nintendo 3DS",
    "citra2018": "Nintendo - Nintendo 3DS",
    # Nintendo - Virtual Boy
    "mednafen_vb": "Nintendo - Virtual Boy",
    # Nintendo - Pokemon Mini
    "pokemini": "Nintendo - Pokemon Mini",
    # Nintendo - Nintendo DS
    "melonds": "Nintendo - Nintendo DS",
    "desmume": "Nintendo - Nintendo DS",
    "desmume2015": "Nintendo - Nintendo DS",
    # Sega - Mega Drive - Genesis
    "genesis_plus_gx": "Sega - Mega Drive - Genesis",
    "picodrive": "Sega - Mega Drive - Genesis",
    "blastem": "Sega - Mega Drive - Genesis",
    # Sega - Master System - Mark III
    "smsplus": "Sega - Master System - Mark III",
    "gearsystem": "Sega - Master System - Mark III",
    # Sega - Saturn
    "yabause": "Sega - Saturn",
    "kronos": "Sega - Saturn",
    "mednafen_saturn": "Sega - Saturn",
    # Sega - Dreamcast
    "flycast": "Sega - Dreamcast",
    # Sony - PlayStation
    "mednafen_psx": "Sony - PlayStation",
    "mednafen_psx_hw": "Sony - PlayStation",
    "pcsx_rearmed": "Sony - PlayStation",
    "swanstation": "Sony - PlayStation",
    # Sony - PlayStation Portable
    "ppsspp": "Sony - PlayStation Portable",
    # NEC - PC Engine
    "mednafen_pce": "NEC - PC Engine - TurboGrafx 16",
    "mednafen_pce_fast": "NEC - PC Engine - TurboGrafx 16",
    "mednafen_supergrafx": "NEC - PC Engine SuperGrafx",
    # Bandai - WonderSwan
    "mednafen_wswan": "Bandai - WonderSwan Color",
    # SNK - Neo Geo Pocket
    "mednafen_ngp": "SNK - Neo Geo Pocket",
    # Atari - 2600
    "stella": "Atari - 2600",
    "stella2014": "Atari - 2600",
    # Coleco - Colecovision
    "bluemsx": "Coleco - Colecovision",
    # Mattel - Intellivision
    "freeintv": "Mattel - Intellivision",
    # GCE - Vectrex
    "vecx": "GCE - Vectrex",
    # Arcade
    "fbneo": "Arcade",
    "fbalpha2012": "Arcade",
    "mame2000": "Arcade",
    "mame2003": "Arcade",
    "mame2003_plus": "Arcade",
    "mame2010": "Arcade",
    "mame": "Arcade",
    "mame2003_midway": "Arcade",
    # DOS
    "dosbox_core": "DOS",
    "dosbox_pure": "DOS",
    "dosbox_svn": "DOS",
    # Game engines, which are systems here in the same sense libretro
    # means it: the thing the core runs.
    "scummvm": "ScummVM",
    "prboom": "DOOM",
    "tyrquake": "Quake",
    "vitaquake2": "Quake II",
    "ecwolf": "Wolfenstein 3D",
    "nxengine": "Cave Story",
    "easyrpg": "EasyRPG",
    "tic80": "TIC-80",
    "uzem": "Uzebox",
    "wasm4": "WASM-4",
    "vircon32": "Vircon32",
    "lowresnx": "LowResNX",
    "chailove": "ChaiLove",
    "lutro": "Lutro",
    "cannonball": "Cannonball",
    "dinothawr": "Dinothawr",
    "superbroswar": "Super Bros War",
}


def system_for(core_id: str) -> str | None:
    """The system a core emulates, or None when this plugin does not know.

    None is a fact about the table, never an instruction to substitute
    something. Callers leave `CoreArtifact.system` unset rather than
    filling it in.
    """
    if not isinstance(core_id, str):
        return None
    return CORE_SYSTEMS.get(core_id.strip())
