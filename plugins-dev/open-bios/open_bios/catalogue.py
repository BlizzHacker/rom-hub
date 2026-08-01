"""What this plugin offers, and the evidence for each entry.

Every line here is a claim about somebody else's project, so every line
was checked against that project's own repository on 2026-07-29 rather
than against a wiki, a forum post or a memory. The three that survived
are below; the ones that did not are at the bottom, with why, because a
list of what was rejected is the part a reader can actually audit.

## The rule

**Clean-room and openly licensed, or it is not here.** Nothing in this
catalogue is a dump of a retail chip. The Hub cannot check that -- a
dumped BIOS and a reimplemented one look identical on the wire -- so it
is a rule about what this file may contain, and the licence of every item
is carried in `FirmwareArtifact.license` where the operator reads it
before installing.

## Why the catalogue is static

`list()` makes no network request. Two reasons, and the second is the
real one.

Firmware is bytes an emulator *executes*. Pinning means an operator gets
the release this plugin's README says it verified, rather than whatever
was tagged this morning by someone whose CI changed. Resolving "latest"
at list time would quietly move that target, and the move would be
invisible.

And a catalogue that costs nothing is a catalogue people run. `rom-hub
firmware list open-bios` is instant and works offline.

The cost is that a new upstream release needs a new plugin release. That
is the right trade for three entries that change once a year, and
`sameboy_release` is the escape hatch for an operator who wants a newer
tag before this plugin ships one.
"""

from dataclasses import dataclass

#: Pinned to a commit, not to `master`. A branch name in a firmware URL
#: means the bytes can change under a plugin that claims to know what it
#: is serving. Verified 2026-07-29: this path answers 200 with
#: `Content-Length: 16384` and no redirect.
CULT_OF_GBA_COMMIT = "a30e9a96df083628b650724b7d4d7112b4070b98"

#: A GBA BIOS is exactly 16 KiB. Stated so the host can report the size
#: before the request goes out, and so a wrong file is obvious.
GBA_BIOS_BYTES = 16384

#: The smallest SameBoy release asset that carries the built boot ROMs.
#: The boot ROMs are identical bytes in every build -- they are 256- and
#: 2304-byte binaries assembled from `BootROMs/*.asm` -- so the choice
#: between artifacts is only about how much else comes along. Measured:
#: `sameboy_winsdl_*.zip` is 1.6 MB, `sameboy_cocoa_*.zip` is 4.8 MB.
SAMEBOY_ASSET = "sameboy_winsdl_{release}.zip"

#: The release this plugin was verified against, and the fallback when the
#: config carries nothing. It is also `manifest.toml`'s declared default;
#: the two must agree, and a test asserts they do rather than trusting
#: anyone to remember.
DEFAULT_SAMEBOY_RELEASE = "v1.0.3"

#: openMSX's release that carries the built C-BIOS ROMs. Pinned, for the
#: reason every other URL here is pinned: firmware is bytes an emulator
#: executes.
OPENMSX_RELEASE = "RELEASE_21_0"

#: The smallest openMSX asset carrying all 19 C-BIOS ROMs. Measured
#: 2026-08-01: the Linux zip is 9.6 MB against the Windows zip's 12.8 MB,
#: and both carry the identical `share/machines/cbios_*.rom` set. The host
#: keeps only the declared members and deletes the archive.
OPENMSX_ASSET = "openmsx-21.0-linux-x86_64-bin.zip"

OPENMSX_RELEASE_URL = (
    "https://github.com/openMSX/openMSX/releases/download/"
    f"{OPENMSX_RELEASE}/{OPENMSX_ASSET}"
)

#: Where openMSX keeps them inside that zip. A *lookup key*, never a
#: destination: `rom_hub.firmware` installs the basename. See
#: `FirmwareArtifact.members`.
CBIOS_DIR = "share/machines/"


@dataclass(frozen=True)
class Source:
    """One installable firmware item, before it becomes a FirmwareArtifact."""

    firmware_id: str
    name: str
    #: This plugin's system name. `platforms.platform_for` turns it into a
    #: library platform slug, or refuses.
    system: str
    #: What the operator is permitted to do with these bytes. SPDX where
    #: there is one. Printed by `rom-hub firmware list`.
    license: str
    #: The project this comes from, so the claim above is checkable.
    project: str
    description: str
    #: A path relative to the plugin's `network` hosts, filled in by
    #: `firmware.py` -- an item either names a direct file or an asset.
    filename: str = ""
    url: str = ""
    size_bytes: int | None = None
    archive: str | None = None
    members: tuple[str, ...] = ()
    #: Set for an item whose URL depends on the `sameboy_release` config.
    asset: str = ""
    #: What `firmware list` shows in the VERSION column. Left empty for a
    #: SameBoy item, whose version is the configured release tag and is
    #: therefore not knowable here.
    version: str = ""


SOURCES: tuple[Source, ...] = (
    Source(
        firmware_id="cult-of-gba",
        name="Cult-of-GBA BIOS",
        system="Game Boy Advance",
        # `Cult-of-GBA/BIOS/LICENSE` is the Expat text, "Copyright 2020 -
        # 2021 DenSinH and fleroviux", and GitHub reports the repository
        # as MIT. Redistribution is explicit.
        license="MIT",
        project="https://github.com/Cult-of-GBA/BIOS",
        description=(
            "A from-scratch Game Boy Advance BIOS by DenSinH and fleroviux. "
            "The repository is the assembly source for every BIOS call plus "
            "the boot screen, and the built 16 KiB image is committed "
            "beside it. Named gba_bios.bin, which is what mGBA, VBA-M and "
            "the libretro cores look for."
        ),
        filename="gba_bios.bin",
        url=(
            "https://raw.githubusercontent.com/Cult-of-GBA/BIOS/"
            f"{CULT_OF_GBA_COMMIT}/bios.bin"
        ),
        size_bytes=GBA_BIOS_BYTES,
    ),
    Source(
        firmware_id="sameboy-dmg",
        name="SameBoy Game Boy boot ROMs",
        system="Game Boy",
        # SameBoy's own LICENSE: "All files and directories in this
        # repository, except for the iOS and HexFiend directories, are
        # licensed under the Expat License". `BootROMs/` is neither, so
        # the boot ROMs are Expat -- which is the specific question worth
        # asking here, because GitHub reports the repository as
        # NOASSERTION on account of that iOS carve-out.
        license="MIT (Expat)",
        project="https://github.com/LIJI32/SameBoy",
        description=(
            "Lior Halphon's clean-room replacements for the original Game "
            "Boy boot ROMs, assembled from BootROMs/dmg_boot.asm and "
            "mgb_boot.asm. dmg_boot.bin is the DMG (1989) boot ROM; "
            "mgb_boot.bin is the Game Boy Pocket's."
        ),
        archive="zip",
        members=("dmg_boot.bin", "mgb_boot.bin"),
        asset=SAMEBOY_ASSET,
    ),
    Source(
        firmware_id="sameboy-cgb",
        name="SameBoy Game Boy Color boot ROMs",
        system="Game Boy Color",
        license="MIT (Expat)",
        project="https://github.com/LIJI32/SameBoy",
        description=(
            "The same project's Game Boy Color boot ROMs. cgb_boot.bin is "
            "the CGB-A..E revision, cgb0_boot.bin the earlier CGB-0, and "
            "agb_boot.bin the one a Game Boy Advance runs when it is being "
            "a Game Boy Color -- all three are Game Boy Color boot ROMs, "
            "which is why they are one item."
        ),
        archive="zip",
        members=("cgb_boot.bin", "cgb0_boot.bin", "agb_boot.bin"),
        asset=SAMEBOY_ASSET,
    ),
    # -- C-BIOS, added in 0.2.0 -------------------------------------------
    #
    # Previously declined as "source only", and that was correct: `cbios/
    # cbios` had no releases at all. It now has ten (latest v0.29,
    # 2026-08-01) and they are still source only -- the release page
    # attaches nothing but GitHub's auto-generated `.tar.gz` and `.zip`,
    # and the repository holds a Makefile and `src/` with no built `.rom`
    # anywhere. So the original disqualification stands for C-BIOS's own
    # distribution.
    #
    # What changed the answer is that **openMSX publishes built C-BIOS
    # ROMs** inside its own GitHub release, under `share/machines/`. That
    # is the same shape as SameBoy -- a BIOS that exists only inside an
    # emulator's archive -- so it is installed the same way: the host
    # fetches the zip, keeps exactly the declared members, and deletes the
    # rest.
    #
    # The licence is C-BIOS's own and is not openMSX's. `doc/cbios.txt`,
    # shipped in that same archive and identical to the copy in the
    # cbios repository, carries a three-clause BSD notice -- "Redistribution
    # and use in source and binary forms, with or without modification,
    # are permitted provided that the following conditions are met" --
    # over copyrights held by BouKiCHi, Reikan, Maarten ter Huurne, Albert
    # Beevendorp and five others. Binary redistribution is explicit, which
    # is the question that matters here.
    #
    # Three items rather than one because the MSX generations take
    # different BIOS ROMs and are three different library platforms. The
    # regional variants (`_br`, `_eu`, `_jp`) are deliberately not offered:
    # openMSX selects them per machine configuration, and offering ten
    # near-identical files under one id would be a choice with no
    # information attached to it.
    Source(
        firmware_id="cbios-msx1",
        name="C-BIOS (MSX1)",
        system="MSX",
        license="BSD-3-Clause",
        project="https://github.com/cbios/cbios",
        description=(
            "A clean-room MSX BIOS written from scratch by the C-BIOS team, "
            "so that MSX emulation needs no dumped machine ROM. This is the "
            "MSX1 main BIOS plus its boot logo, taken from openMSX's own "
            "release because C-BIOS publishes no built ROMs of its own. It "
            "runs cartridge images; it is not a complete MSX-BASIC "
            "environment."
        ),
        archive="zip",
        members=(
            f"{CBIOS_DIR}cbios_main_msx1.rom",
            f"{CBIOS_DIR}cbios_logo_msx1.rom",
        ),
        url=OPENMSX_RELEASE_URL,
        filename=OPENMSX_ASSET,
        version=OPENMSX_RELEASE,
    ),
    Source(
        firmware_id="cbios-msx2",
        name="C-BIOS (MSX2)",
        system="MSX2",
        license="BSD-3-Clause",
        project="https://github.com/cbios/cbios",
        description=(
            "The same project's MSX2 set: the main BIOS, the sub-ROM an "
            "MSX2 needs alongside it, and the boot logo. From openMSX's "
            "release, under C-BIOS's own BSD licence."
        ),
        archive="zip",
        members=(
            f"{CBIOS_DIR}cbios_main_msx2.rom",
            f"{CBIOS_DIR}cbios_sub.rom",
            f"{CBIOS_DIR}cbios_logo_msx2.rom",
        ),
        url=OPENMSX_RELEASE_URL,
        filename=OPENMSX_ASSET,
        version=OPENMSX_RELEASE,
    ),
    Source(
        firmware_id="cbios-msx2plus",
        name="C-BIOS (MSX2+)",
        system="MSX2+",
        license="BSD-3-Clause",
        project="https://github.com/cbios/cbios",
        description=(
            "The MSX2+ set: main BIOS, sub-ROM and boot logo. From "
            "openMSX's release, under C-BIOS's own BSD licence."
        ),
        archive="zip",
        members=(
            f"{CBIOS_DIR}cbios_main_msx2+.rom",
            f"{CBIOS_DIR}cbios_sub.rom",
            f"{CBIOS_DIR}cbios_logo_msx2+.rom",
        ),
        url=OPENMSX_RELEASE_URL,
        filename=OPENMSX_ASSET,
        version=OPENMSX_RELEASE,
    ),
)


# -- what was left out, and why ------------------------------------------
#
# Each of these was a real candidate. Three were dropped for reasons that
# are properties of the upstream project, and one for a reason that is a
# property of this plugin. Recorded here so nobody has to establish them
# a second time, and so a future release can pick one up if the reason
# stops being true.
#
# **PCSX-ReDux OpenBIOS (PlayStation, GPL-2.0)** -- the one people most
# want, and it is not installable. `grumpycoders/pcsx-redux` has **no
# releases and no tags at all** (`GET /repos/.../releases` and `/tags`
# both answer an empty array), so there is no `openbios.bin` to fetch.
# `src/mips/openbios/README.md` documents the build as `make` under a
# Docker MIPS toolchain, and the only prebuilt copies are the ones
# `.github/workflows/linux-build.yml` installs *inside* the emulator's own
# AppImage (`make install install-openbios DESTDIR=AppDir/usr`), published
# to distrib.app rather than to GitHub. An entry pointing at a hundred
# megabytes of emulator to extract a BIOS is not an entry; an entry
# pointing at a release asset that does not exist is a 404. If the project
# ever publishes the binary, this is a one-row change.
#
# **nds-bootstrap (GPL-3.0)** -- real, maintained, and *not firmware*. Its
# release ships `nds-bootstrap.nds` and `nds-bootstrap-hb-release.nds`: a
# homebrew loader that runs on DS hardware to boot .nds files from an SD
# card. It is not a replacement for the DS BIOS (`biosnds7.bin`,
# `biosnds9.bin`) or for the DS firmware image, which is what melonDS and
# DeSmuME ask for. Installing it into a firmware directory and calling it
# DS firmware would be the exact confusion this plugin exists to prevent.
#
# **C-BIOS (MSX)** -- openly licensed and genuinely clean-room, but
# `cbios/cbios` publishes no release assets and no built `.rom` files;
# the ROMs are assembled from source. Same disqualification as OpenBIOS.
#
# **SameBoy's Super Game Boy boot ROMs** (`sgb_boot.bin`, `sgb2_boot.bin`)
# -- these are in the archive this plugin already downloads, and they are
# under the same Expat licence, so nothing about them is in doubt. They
# are absent because the Super Game Boy is a *SNES peripheral* and there
# is no unambiguous library platform for it, and the rule in
# `platforms.py` is that this plugin never guesses a platform. An item
# nobody can file is worse than an item nobody offered.


def find(firmware_id: str) -> Source:
    for source in SOURCES:
        if source.firmware_id == firmware_id:
            return source
    known = ", ".join(sorted(s.firmware_id for s in SOURCES))
    raise KeyError(f"no source {firmware_id!r}; this plugin offers {known}")
