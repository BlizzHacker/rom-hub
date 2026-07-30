"""Demozoo platform name -> RomM platform slug.

**This table is the only thing standing between an import and a demo filed
under the wrong machine**, so it is an exact-match lookup with no fallback
and no prefix rule. A platform that is not spelled out below raises
"needs mapping" and the import stops, because a visible gap is cheap to
close and a silently misfiled production is not.

Both sides are read rather than remembered:

* the keys are the 93 platform names Demozoo serves from
  `GET https://demozoo.org/api/v1/platforms/`, captured verbatim on
  2026-07-29 into `tests/fixtures/demozoo/platforms.json`. They are
  matched literally, including `Nintendo SNES/Super FamiCom` and
  `Atari 2600 Video Computer System (VCS)` -- a tidied-up spelling here
  would simply never match what the API returns;
* the values are RomM platform slugs from the set the sibling plugins
  verified against RomM 4.9.2's `GET /api/platforms/supported`.

The `id` beside each slug is Demozoo's own platform id, and it is here
because searching needs it: `productions/?platform=<id>` is a server-side
filter and `?platform=<name>` is not a thing. Three Demozoo platforms map
onto RomM's single `amiga`, which is why the reverse lookup returns a
*list* of ids rather than one.

Absences are as deliberate as entries, and there are three kinds, because
"add a row" is the right answer to only one of them:

* **needs mapping** -- a real machine RomM has a slug for, which nobody
  has added here yet. The message says to add the row.
* **not a library platform** -- `Windows`, `Linux`, `macOS`, `Browser`,
  `Java`, `Flash`, `Android`, `Paper`. A Windows 64K intro is a `.exe`
  for a desktop PC; there is no shelf in a ROM library for it, and there
  never will be. Saying "needs mapping" for those would invite somebody
  to invent one.
* **ambiguous** -- worse than missing, because it looks answerable.
  Demozoo has one `Neo Geo` where RomM keeps `neogeoaes` and
  `neogeomvs`; one `Neo Geo Pocket / Neo Geo Pocket Color (NGPC)` where
  RomM keeps two; one `Thomson` where RomM keeps `thomson-mo5` and
  `thomson-to`. Picking either side of any of those files half the
  productions wrongly, so all three refuse with that sentence instead.

Machines left in neither table -- `Acorn Archimedes`, `BBC Micro`,
`Commodore 128`, `Atari Falcon`, `Atari TT`, `Oric`, `SAM Coupé`,
`Sinclair QL`, `Sharp MZ`, `TRS-80`, `Vector-06C`, `Enterprise`,
`KC 85/Robotron KC 87`, `PMD 85`, `ZVT PP01`, `Electronika BK-0010/11M`,
`VTech Laser 200 / VZ 200`, `Amstrad Plus`, `Commodore 64-DTV`,
`Atari Portfolio`, `PICO-8`, `Nintendo Switch (NSW)`, `Raspberry Pi`,
`Gamepark 32`, `Gamepark GP2X`, `MicroW8`, `Mobile`, `BeOS`, `FreeBSD`,
`Console Handheld`, `Custom Hardware`, `Fantasy Console`, `Calculator` --
are the "needs mapping" case. Most of them have no RomM slug at all;
`Amstrad Plus` does not because the CPC Plus range and the GX4000 console
are one Demozoo platform and two RomM ones.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemozooPlatform:
    """One Demozoo platform, and where its productions belong."""

    #: Demozoo's own numeric id, used as `productions/?platform=<id>`.
    id: int
    #: The RomM platform slug.
    slug: str


#: Demozoo platform name (verbatim) -> where it goes.
PLATFORMS: dict[str, DemozooPlatform] = {
    # Commodore. Three Amiga entries, one RomM slug: AGA and OCS/ECS are
    # chipset revisions of the same computer, and PPC/RTG is an
    # accelerator card and a graphics card *inside* one.
    "Amiga OCS/ECS": DemozooPlatform(5, "amiga"),
    "Amiga AGA": DemozooPlatform(6, "amiga"),
    "Amiga PPC/RTG": DemozooPlatform(26, "amiga"),
    "Commodore 64": DemozooPlatform(3, "c64"),
    "Commodore 16/Plus 4": DemozooPlatform(41, "c-plus-4"),
    "Commodore PET": DemozooPlatform(90, "cpet"),
    "Commodore VIC-20": DemozooPlatform(21, "vic-20"),
    # Sinclair. `ZX Spectrum Enhanced` is Demozoo's marker for a
    # production that needs a 128K machine or a clone with extra
    # hardware; it is still a Spectrum and RomM has one slug for the
    # family.
    "ZX Spectrum": DemozooPlatform(2, "zxs"),
    "ZX Spectrum Enhanced": DemozooPlatform(69, "zxs"),
    "ZX81": DemozooPlatform(45, "zx81"),
    # Amstrad / Schneider.
    "Amstrad CPC": DemozooPlatform(36, "acpc"),
    # Atari.
    "Atari 8 bit": DemozooPlatform(16, "atari8bit"),
    "Atari ST/E": DemozooPlatform(9, "atari-st"),
    "Atari 2600 Video Computer System (VCS)": DemozooPlatform(54, "atari2600"),
    "Atari 7800 ProSystem": DemozooPlatform(86, "atari7800"),
    "Atari Jaguar": DemozooPlatform(50, "jaguar"),
    "Atari Lynx": DemozooPlatform(70, "lynx"),
    # Apple.
    "Apple II": DemozooPlatform(67, "appleii"),
    "Apple II GS": DemozooPlatform(57, "apple-iigs"),
    "Mac OS (Classic)": DemozooPlatform(94, "mac"),
    # PC and other home computers.
    "MS-Dos": DemozooPlatform(4, "dos"),
    "MSX": DemozooPlatform(31, "msx"),
    "Sharp X68000": DemozooPlatform(100, "sharp-x68000"),
    # Consoles.
    "ColecoVision": DemozooPlatform(91, "colecovision"),
    "Intellivision": DemozooPlatform(85, "intellivision"),
    "Vectrex": DemozooPlatform(43, "vectrex"),
    "NEC PC Engine": DemozooPlatform(75, "tg16"),
    "Sega Master System": DemozooPlatform(44, "sms"),
    "Sega Megadrive/Genesis": DemozooPlatform(22, "genesis"),
    "Sega Game Gear": DemozooPlatform(102, "gamegear"),
    "Sega Dreamcast": DemozooPlatform(23, "dc"),
    "Nintendo Entertainment System (NES)": DemozooPlatform(25, "nes"),
    "Nintendo SNES/Super FamiCom": DemozooPlatform(34, "snes"),
    "Nintendo 64 (N64)": DemozooPlatform(39, "n64"),
    "Nintendo GameCube (NGC)": DemozooPlatform(60, "ngc"),
    "Nintendo Wii": DemozooPlatform(24, "wii"),
    "Nintendo Game Boy (GB)": DemozooPlatform(38, "gb"),
    "Nintendo Game Boy Color (GBC)": DemozooPlatform(37, "gbc"),
    "Nintendo Game Boy Advance (GBA)": DemozooPlatform(30, "gba"),
    "Nintendo DS (NDS)": DemozooPlatform(32, "nds"),
    "Nintendo 3DS": DemozooPlatform(71, "3ds"),
    "Sony Playstation 1 (PSX)": DemozooPlatform(13, "psx"),
    "Sony Playstation 2 (PS2)": DemozooPlatform(28, "ps2"),
    "Sony Playstation 3 (PS3)": DemozooPlatform(68, "ps3"),
    "Sony Playstation Portable (PSP)": DemozooPlatform(29, "psp"),
    "Wonderswan": DemozooPlatform(97, "wonderswan"),
    "XBOX": DemozooPlatform(40, "xbox"),
    "XBOX360": DemozooPlatform(15, "xbox360"),
    "TIC-80": DemozooPlatform(92, "tic-80"),
}


#: Not a machine a ROM library files games for, and never will be. The
#: value is the sentence an operator gets.
NOT_A_LIBRARY_PLATFORM: dict[str, str] = {
    "Windows": "a desktop PC executable, not a console or home-computer ROM",
    "Linux": "a desktop PC executable, not a console or home-computer ROM",
    "macOS": (
        "a modern Mac executable, not a console or home-computer ROM "
        "(Demozoo's `Mac OS (Classic)` is the retro machine and does map)"
    ),
    "Android": "a phone application, not a ROM",
    "Browser": "a web page, which has nothing to download and nothing to emulate",
    "Java": "a JVM application, not a ROM",
    "Flash": "an SWF for a player that no longer exists, not a ROM",
    "Paper": "printed matter -- there is no file at all",
}


#: One Demozoo platform, two RomM slugs. Refused rather than halved.
AMBIGUOUS: dict[str, str] = {
    "Neo Geo": (
        "RomM keeps the arcade `neogeomvs` and the home `neogeoaes` as "
        "separate platforms and Demozoo has one `Neo Geo`, which does not "
        "say which a given production is for"
    ),
    "Neo Geo Pocket / Neo Geo Pocket Color (NGPC)": (
        "one Demozoo platform covering two machines RomM keeps apart, "
        "`neo-geo-pocket` and `neo-geo-pocket-color`"
    ),
    "Thomson": (
        "RomM keeps `thomson-mo5` and `thomson-to` as separate platforms "
        "and Demozoo has one `Thomson`"
    ),
}


class NeedsMapping(Exception):
    """A production's platform has no RomM slug here, and it is named."""


def slug_for(name: str) -> str | None:
    """The RomM slug for this Demozoo platform name, or None.

    None means "this plugin has nothing to say", which is what `search`
    wants -- a result whose platform is unmapped is simply not shown.
    `importer` calls `require_slug` instead, because there the same
    situation has to become a message.
    """
    entry = PLATFORMS.get(name)
    return entry.slug if entry else None


def require_slug(name: str) -> str:
    """The RomM slug, or a refusal that says which of the three cases it is."""
    entry = PLATFORMS.get(name)
    if entry is not None:
        return entry.slug
    if name in NOT_A_LIBRARY_PLATFORM:
        raise NeedsMapping(
            f"Demozoo platform {name!r} is not a library platform: "
            f"{NOT_A_LIBRARY_PLATFORM[name]}. This production cannot be "
            f"imported into a ROM library, and no row in "
            f"demozoo/platforms.py would change that."
        )
    if name in AMBIGUOUS:
        raise NeedsMapping(
            f"Demozoo platform {name!r} is ambiguous: {AMBIGUOUS[name]}. "
            f"Guessing would file half of these productions under the wrong "
            f"machine, so this plugin refuses instead."
        )
    raise NeedsMapping(
        f"Demozoo platform {name!r} needs mapping: it is not in this "
        f"plugin's platform -> RomM slug table, and guessing would file the "
        f"production under the wrong system. Add it to "
        f"demozoo/platforms.py -- and only if RomM actually has a slug for "
        f"that machine."
    )


def demozoo_ids_for_slug(slug: str) -> list[int]:
    """Demozoo platform ids that map onto one RomM slug.

    A list, not an id: `amiga` is three Demozoo platforms. Sorted so a
    search issues its requests in the same order every time.
    """
    wanted = (slug or "").strip().lower()
    return sorted(
        entry.id for entry in PLATFORMS.values() if entry.slug == wanted
    )
