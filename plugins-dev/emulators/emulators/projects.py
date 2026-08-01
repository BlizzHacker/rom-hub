"""Which emulators this plugin offers, and which asset each one means.

One row per project. Every field was read from the project's own
repository -- the licence from its `LICENSE`/`COPYING` file via
`GET /repos/{owner}/{repo}/license`, read in full rather than taken from
GitHub's SPDX summary, and the asset names from
`GET /repos/{owner}/{repo}/releases/latest`. The original four were
verified 2026-07-29 and re-verified 2026-08-01; the seven added in 0.2.0
were verified 2026-08-01.

Reading the licence file rather than the SPDX field is not ceremony.
Five of these eleven projects report `NOASSERTION`, and the five answers
are all different: ares is ISC with third-party notices appended, PPSSPP
is GPL-2.0 with a PSPSDK BSD notice at the top of the same file, MAME's
COPYING says "MAME as a whole is made available under the terms of the
GNU General Public License ... version 2", xemu carries QEMU's LICENSE
(GPL-2.0), and DuckStation is CC BY-NC-ND 4.0, which is not an
open-source licence at all. A sixth, BizHawk, was **rejected** on what
its own LICENSE says -- see DECLINED.

Three things in here are load-bearing.

**A pattern per (project, target), never a heuristic.** Upstream naming
is not a convention, it is four conventions. The Linux x86_64 build is
`DuckStation-x64.AppImage`, `mGBA-0.10.5-appimage-x64.appimage`,
`pcsx2-v2.6.3-linux-appimage-x64-Qt.AppImage` and
`melonDS-1.1-appimage-x86_64.zip` -- four spellings of one machine, three
of which embed a version. Any generic "find the Linux build" rule that
covers those also covers `pcsx2-v2.6.3-linux-flatpak-x64-Qt.flatpak` and
`melonDS-1.1-ubuntu-x86_64.zip`, and it takes whichever the release
happened to list first. So each cell is an explicit anchored regex, and a
target with no cell is a target this plugin does not offer for that
project -- said out loud rather than approximated.

**Exactly one match, or a refusal.** `select` requires the pattern to hit
precisely one asset. Zero means the project renamed something; two means
the pattern is too loose. Both are bugs in this table, and both are cases
where picking one anyway would install a plausible wrong file: DuckStation
alone publishes `duckstation-windows-x64-release.zip`,
`duckstation-windows-x64-sse2-release.zip` and
`duckstation-windows-x64-release-symbols.7z`, and only the first of those
is the emulator.

**The licence is a field, not a comment.** `cores list` prints it, because
these projects do not agree and the difference matters to whoever is
about to install one. mGBA and Cemu are MPL-2.0; PCSX2, melonDS, simple64
and Vita3K are GPL-3.0 or GPL-2.0; Flycast, PPSSPP, MAME and xemu are
GPL-2.0; ares is ISC; and DuckStation is **not open source at all** -- it
relicensed to CC BY-NC-ND 4.0, which permits non-commercial
redistribution of the unmodified work and forbids derivatives. Nothing
here redistributes anything: the plugin names the project's own release
URL and the host fetches it once, for the operator who asked. But an
operator is entitled to read what they are installing without leaving the
terminal.

**One API call per project, and there are eleven.** Unauthenticated
GitHub API requests are capped at 60 per hour per address, so a full
`cores list` now costs eleven of that budget where it used to cost four.
That is roughly five listings an hour, shared with anything else on the
same address. The `only` config key is the answer and it is worth knowing
about before the 403 arrives; `releases.fetch_release` says so when it
does.
"""

import re
from dataclasses import dataclass, field


class UnknownProject(Exception):
    """No such project in this plugin's table, and the message names it."""


class NoAssetForTarget(Exception):
    """This project publishes nothing for the configured target."""


class AmbiguousAsset(Exception):
    """The pattern for this (project, target) matched other than one asset."""


@dataclass(frozen=True)
class Project:
    """One emulator, its repository, and how to find its build per target."""

    #: The id an operator types (`rom-hub cores install emulators mgba`).
    #: Constrained to `CoreArtifact.core_id`'s character set.
    project_id: str
    #: How the project spells its own name.
    display: str
    #: `owner/repo` on GitHub.
    repo: str
    #: The system it emulates. Goes in the SYSTEM column of `cores list`.
    system: str
    #: SPDX identifier where the project has one, plain words where it
    #: does not. Printed. See the module docstring.
    license: str
    #: Longer licence sentence for `description`.
    license_note: str
    #: target key -> anchored regex matching exactly one release asset.
    assets: dict[str, str] = field(default_factory=dict)
    #: Why some targets are missing, when the reason is not "upstream does
    #: not build it". Printed only in this file and the README.
    caveat: str = ""

    @property
    def releases_url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/releases/latest"

    def pattern_for(self, target_key: str) -> str:
        pattern = self.assets.get(target_key)
        if pattern is None:
            raise NoAssetForTarget(
                f"{self.display} publishes no build this plugin offers for "
                f"{target_key!r}. It offers: "
                f"{', '.join(sorted(self.assets)) or '(nothing)'}."
                + (f" {self.caveat}" if self.caveat else "")
            )
        return pattern

    def select(self, target_key: str, names: list[str]) -> str:
        """The one asset name in `names` this (project, target) means.

        Refuses on zero and on more than one, because both are how a
        release rename turns into the wrong file quietly. See the module
        docstring.
        """
        pattern = re.compile(self.pattern_for(target_key))
        hits = sorted(name for name in names if pattern.fullmatch(name))
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise NoAssetForTarget(
                f"{self.display}'s latest release carries no asset matching "
                f"{pattern.pattern!r} for {target_key!r}. Upstream has renamed "
                f"or dropped that build; fix the pattern in "
                f"emulators/projects.py rather than relaxing it, because a "
                f"looser pattern here picks an installer or a symbols archive. "
                f"The release lists: {', '.join(sorted(names)) or '(nothing)'}"
            )
        raise AmbiguousAsset(
            f"the {target_key!r} pattern for {self.display} "
            f"({pattern.pattern!r}) matched {len(hits)} assets: "
            f"{', '.join(hits)}. Exactly one of them is the emulator and this "
            f"plugin will not guess which; tighten the pattern in "
            f"emulators/projects.py."
        )


# A version segment inside an asset name. Deliberately not `.+`: the
# projects that embed a version embed exactly one path-free segment
# (`0.10.5`, `v2.6.3`, `1.1`), and a greedy wildcard would let
# `melonDS-1.1-ubuntu-x86_64.zip` satisfy an `-appimage-x86_64` pattern
# on a future release that renamed things.
_V = r"[0-9A-Za-z._+-]+"

# A version segment that may **not** contain a hyphen, which `_V` may.
# xemu is why it exists: its release carries both
# `xemu-0.8.136-x86_64.AppImage` and `xemu-0.8.136-dbg-x86_64.AppImage`,
# and `xemu-{_V}-x86_64\.AppImage` fullmatches *both* because `_V` eats
# `0.8.136-dbg`. `select` would refuse that as ambiguous rather than
# install a debug build -- which is the safety net working -- but a
# pattern that describes the asset exactly is better than one that relies
# on being caught.
_VN = r"[0-9][0-9A-Za-z._+]*"

# MAME versions its assets `mame0289b_x64.exe`: no separator, four
# digits, and the letter that follows says which artifact it is.
_MAME_V = r"[0-9]{4,5}"

PROJECTS: tuple[Project, ...] = (
    Project(
        project_id="duckstation",
        display="DuckStation",
        repo="stenzek/duckstation",
        system="Sony PlayStation",
        license="CC-BY-NC-ND-4.0",
        license_note=(
            "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 "
            "International -- NOT an open-source licence. Non-commercial use "
            "and redistribution of the unmodified build only; no derivatives."
        ),
        assets={
            # Verified against the `latest` rolling release, 2026-07-29.
            # The SSE2 variants are a fallback for CPUs without AVX2 and
            # are deliberately not offered: two builds for one machine
            # would be two rows an operator has to choose between with no
            # information, and the plain build is upstream's default.
            "linux/x86_64": r"DuckStation-x64\.AppImage",
            "linux/aarch64": r"DuckStation-arm64\.AppImage",
            "windows/x86_64": r"duckstation-windows-x64-release\.zip",
            "windows/arm64": r"duckstation-windows-arm64-release\.zip",
            "macos/universal": r"duckstation-mac-release\.zip",
        },
        caveat=(
            "Its Windows assets also include `-installer.exe` and "
            "`-symbols.7z`, neither of which is the emulator."
        ),
    ),
    Project(
        project_id="mgba",
        display="mGBA",
        repo="mgba-emu/mgba",
        system="Game Boy Advance",
        license="MPL-2.0",
        license_note="Mozilla Public License 2.0.",
        assets={
            "linux/x86_64": rf"mGBA-{_V}-appimage-x64\.appimage",
            "linux/aarch64": rf"mGBA-{_V}-appimage-arm64\.appimage",
            "windows/x86_64": rf"mGBA-{_V}-win64\.7z",
            "windows/x86": rf"mGBA-{_V}-win32\.7z",
        },
        caveat=(
            "macOS is deliberately not offered: release 0.10.5 ships both "
            "`mGBA-0.10.5-macos.dmg` and `mGBA-0.10.5-osx.dmg` and says "
            "nowhere which macOS version or architecture either one targets, "
            "so picking either would be a guess. Its 3DS, Switch, Vita and "
            "Wii builds are emulators for game consoles rather than for a "
            "machine in this plugin's target list, and its five "
            "`ubuntu64-<codename>` tarballs are superseded by the AppImage."
        ),
    ),
    Project(
        project_id="pcsx2",
        display="PCSX2",
        repo="PCSX2/pcsx2",
        system="Sony PlayStation 2",
        license="GPL-3.0",
        license_note="GNU General Public License v3.0.",
        assets={
            "linux/x86_64": rf"pcsx2-{_V}-linux-appimage-x64-Qt\.AppImage",
            "windows/x86_64": rf"pcsx2-{_V}-windows-x64-Qt\.7z",
            "macos/universal": rf"pcsx2-{_V}-macos-Qt\.tar\.xz",
        },
        caveat=(
            "Its `.flatpak` build needs a Flatpak runtime the Hub cannot "
            "provide by dropping a file in a directory, and "
            "`-Qt-symbols.7z` is debug symbols."
        ),
    ),
    Project(
        project_id="melonds",
        display="melonDS",
        repo="melonDS-emu/melonDS",
        system="Nintendo DS",
        license="GPL-3.0",
        license_note="GNU General Public License v3.0.",
        assets={
            "linux/x86_64": rf"melonDS-{_V}-appimage-x86_64\.zip",
            "linux/aarch64": rf"melonDS-{_V}-appimage-aarch64\.zip",
            "windows/x86_64": rf"melonDS-{_V}-windows-x86_64\.zip",
            "windows/arm64": rf"melonDS-{_V}-windows-aarch64\.zip",
            "macos/universal": rf"melonDS-{_V}-macOS-universal\.zip",
        },
        caveat=(
            "Its `ubuntu-*` builds link against a specific distribution's "
            "libraries where the AppImage does not, and its FreeBSD, NetBSD "
            "and OpenBSD builds have no name in this plugin's target list."
        ),
    ),
    # -- added in 0.2.0 --------------------------------------------------
    Project(
        project_id="ares",
        display="ares",
        repo="ares-emulator/ares",
        system="Multi-system (Nintendo, Sega, NEC, SNK and more)",
        # `LICENSE` opens with the ISC text -- "Permission to use, copy,
        # modify, and/or distribute this software for any purpose with or
        # without fee is hereby granted", copyright "2004-2025 ares team,
        # Near et al" -- and then appends the notices of the third-party
        # components it bundles (sljit and others), each under its own
        # heading. GitHub reports NOASSERTION because of that appendix.
        license="ISC",
        license_note=(
            "ISC. The repository's LICENSE carries the ISC text for ares "
            "itself and then the separate notices of the third-party "
            "components it bundles; GitHub's NOASSERTION is that appendix, "
            "not an unclear licence for ares."
        ),
        assets={
            "windows/x86_64": r"ares-windows-x64\.zip",
            "windows/arm64": r"ares-windows-clang-cl-arm64\.zip",
            "macos/universal": r"ares-macos-universal\.zip",
        },
        caveat=(
            "There is no Linux build: release v148 publishes Windows, macOS "
            "and `ares-source.tar.gz` only. Its `-PDBs.zip` and `-dSYMs.zip` "
            "assets are debug symbols."
        ),
    ),
    Project(
        project_id="ppsspp",
        display="PPSSPP",
        repo="hrydgard/ppsspp",
        system="Sony PlayStation Portable",
        # `LICENSE.TXT` is a PSPSDK BSD notice covering the PSP headers and
        # constants PPSSPP reuses, followed by the full text of the GNU
        # General Public License version 2. GitHub reports NOASSERTION
        # because of the BSD notice sitting above the GPL text.
        license="GPL-2.0",
        license_note=(
            "GNU General Public License v2. LICENSE.TXT carries the full "
            "GPL-2 text; the BSD notice above it covers the PSPSDK headers "
            "and constants PPSSPP reuses, and is why GitHub reports "
            "NOASSERTION."
        ),
        assets={
            "linux/x86_64": rf"PPSSPP-v{_V}-anylinux-x86_64\.AppImage",
            "linux/aarch64": rf"PPSSPP-v{_V}-anylinux-aarch64\.AppImage",
            "windows/x86_64": rf"PPSSPP-v{_V}-Windows-x64\.zip",
            "windows/arm64": rf"PPSSPP-v{_V}-Windows-ARM64\.zip",
            "macos/universal": rf"PPSSPPSDL-macOS-v{_V}\.zip",
        },
        caveat=(
            "Its `.AppImage.zsync` files are delta-update manifests, not "
            "builds -- the patterns end at `.AppImage` and fullmatch, so "
            "they cannot pick one up. Its `.ipa` and `.deb` are iOS builds."
        ),
    ),
    Project(
        project_id="flycast",
        display="Flycast",
        repo="flyinghead/flycast",
        system="Sega Dreamcast",
        license="GPL-2.0",
        license_note="GNU General Public License v2.",
        assets={
            "linux/x86_64": r"flycast-x86_64\.AppImage",
            "windows/x86_64": rf"flycast-win64-{_V}\.zip",
            "macos/universal": rf"flycast-macOS-{_V}\.zip",
        },
        caveat=(
            "Its `.apk` is Android, its `.nro` is a Nintendo Switch homebrew "
            "build, and its `.appx` is a Windows store package that has to be "
            "sideloaded through the installer rather than run from a "
            "directory."
        ),
    ),
    Project(
        project_id="vita3k",
        display="Vita3K",
        repo="Vita3K/Vita3K",
        system="Sony PlayStation Vita",
        license="GPL-2.0",
        license_note="GNU General Public License v2 (COPYING.txt).",
        assets={
            "linux/x86_64": r"Vita3K-x86_64\.AppImage",
            "linux/aarch64": r"Vita3K-aarch64\.AppImage",
            "windows/x86_64": r"windows-latest\.zip",
            "windows/arm64": r"windows-arm64-latest\.zip",
            # Two files, not one fat binary, so these are two targets and
            # there is deliberately no `macos/universal` cell.
            "macos/x86_64": r"macos-latest\.dmg",
            "macos/arm64": r"macos-arm64-latest\.dmg",
        },
        caveat=(
            "Its release is a rolling tag literally called `continuous`, so "
            "`cores list` prints that as the version -- it is what upstream "
            "issued. Its `ubuntu-*.zip` builds link against a specific "
            "distribution's libraries where the AppImage does not, its "
            "`.apk` is Android, and its `.AppImage.zsync` files are delta "
            "manifests."
        ),
    ),
    Project(
        project_id="xemu",
        display="xemu",
        repo="xemu-project/xemu",
        system="Microsoft Xbox",
        # xemu is a QEMU fork and ships QEMU's LICENSE: "The QEMU emulator
        # as a whole is released under the GNU General Public License,
        # version 2." GitHub reports NOASSERTION because that file also
        # enumerates the per-component licences (TCG under BSD/MIT, the
        # firmware blobs separately).
        license="GPL-2.0",
        license_note=(
            "GNU General Public License v2. xemu is a QEMU fork and carries "
            "QEMU's LICENSE, which states the emulator as a whole is GPL-2 "
            "and then lists the components under compatible terms; that list "
            "is why GitHub reports NOASSERTION."
        ),
        assets={
            # `_VN` rather than `_V` on every one of these: see its comment.
            "linux/x86_64": rf"xemu-{_VN}-x86_64\.AppImage",
            "linux/aarch64": rf"xemu-{_VN}-aarch64\.AppImage",
            "windows/x86_64": rf"xemu-{_VN}-windows-x86_64\.zip",
            "windows/arm64": rf"xemu-{_VN}-windows-arm64\.zip",
            "macos/universal": rf"xemu-{_VN}-macos-universal\.zip",
        },
        caveat=(
            "Its release carries five kinds of near-duplicate and none of "
            "them is the build you want: `-dbg-` is a debug build, `-pdb` is "
            "symbols, `-unsigned` is the macOS build without a signature, "
            "`.tar.zst` is source, and `xemu-win-x86_64-release.zip` is a "
            "byte-identical second copy of the versioned Windows zip under a "
            "legacy name. The version segment in these patterns excludes "
            "hyphens so that `-dbg-` cannot slip into it."
        ),
    ),
    Project(
        project_id="cemu",
        display="Cemu",
        repo="cemu-project/Cemu",
        system="Nintendo Wii U",
        license="MPL-2.0",
        license_note="Mozilla Public License 2.0.",
        assets={
            "linux/x86_64": rf"Cemu-{_V}-x86_64\.AppImage",
            "windows/x86_64": rf"cemu-{_V}-windows-x64\.zip",
            "macos/x86_64": rf"cemu-{_V}-macos-12-x64\.dmg",
        },
        caveat=(
            "Its `ubuntu-22.04-x64.zip` links against that release's "
            "libraries where the AppImage does not. There is no arm64 build "
            "for any platform, and the macOS build is x86_64 only -- which is "
            "why it has a `macos/x86_64` cell and no `macos/universal` one."
        ),
    ),
    Project(
        project_id="mame",
        display="MAME",
        repo="mamedev/mame",
        system="Arcade",
        # COPYING: "MAME as a whole is made available under the terms of the
        # GNU General Public License ... under the terms of the GNU General
        # Public License version 2". GitHub reports NOASSERTION because the
        # same file opens with a trademark notice and says individual source
        # files may be under less restrictive terms.
        license="GPL-2.0",
        license_note=(
            "GNU General Public License v2. MAME's COPYING says the work as "
            "a whole is GPL-2 and that individual source files may carry "
            "less restrictive terms; the trademark notice above that is why "
            "GitHub reports NOASSERTION."
        ),
        assets={
            "windows/x86_64": rf"mame{_MAME_V}b_x64\.exe",
            "windows/arm64": rf"mame{_MAME_V}b_arm64\.exe",
        },
        caveat=(
            "MAME publishes official binaries for Windows only; every other "
            "platform builds from source. The `b_` in the asset name is what "
            "marks a binary archive -- these are 7-Zip self-extracting "
            "archives, not installers. `mame0289s.exe` is the source archive, "
            "and `mame0289lx.zip` holds a single file, `mame0289.xml`, which "
            "is the machine list rather than any program (confirmed by "
            "reading the zip's central directory, 2026-08-01)."
        ),
    ),
    Project(
        project_id="simple64",
        display="simple64",
        repo="simple64/simple64",
        system="Nintendo 64",
        license="GPL-3.0",
        license_note="GNU General Public License v3.0.",
        assets={
            # The trailing segment is the commit the build came from, so it
            # is matched as a hex string rather than pinned.
            "windows/x86_64": r"simple64-win64-[0-9a-f]{6,40}\.zip",
        },
        caveat=(
            "Windows only: its latest release (v2024.12.1) attaches exactly "
            "one asset. Its Linux build is distributed as a Flatpak from "
            "Flathub, which the Hub cannot provide by dropping a file in a "
            "directory."
        ),
    ),
)

BY_ID: dict[str, Project] = {p.project_id: p for p in PROJECTS}


# -- projects this plugin deliberately does not offer ---------------------
#
# Recorded rather than omitted, so that "why is Dolphin not here?" has an
# answer in the code and not only in a README somebody may not read.

DECLINED: dict[str, str] = {
    "dolphin": (
        "Dolphin publishes no GitHub releases at all -- "
        "https://api.github.com/repos/dolphin-emu/dolphin/releases/latest "
        "answers 404 and /releases answers an empty array (re-checked "
        "2026-08-01), because its builds ship from dolphin-emu.org/download/ "
        "instead. That page cannot be used from here either: dolphin-emu.org "
        "is behind a bunny.net JavaScript challenge that answers 403 to "
        "everything, including its own /robots.txt. A plugin with no sockets "
        "and no browser cannot pass a proof-of-work challenge, and working "
        "around an anti-bot wall is not something this plugin will do. "
        "Dolphin is therefore out of scope until it publishes a "
        "machine-readable release feed."
    ),
    "retroarch": (
        "RetroArch does publish GitHub releases, and they are source only. "
        "v1.22.2 attaches exactly one asset, "
        "`retroarch-sourceonly-1.22.2.tar.xz` (checked 2026-08-01). The "
        "binaries live on buildbot.libretro.com under "
        "/stable/<version>/<os>/<arch>/RetroArch.7z, which does exist and "
        "does answer 200 -- but that directory serves no `.index-extended` "
        "and no machine-readable listing of any kind, only an h5ai "
        "JavaScript file browser. The sibling `libretro-cores` plugin reads "
        "that host precisely because its nightly core directories DO publish "
        "`.index-extended`, and its own module notes that the rendered index "
        "'is not a contract with anybody'. Guessing a path from a GitHub tag "
        "and hoping is not the same as reading a catalogue. RetroArch belongs "
        "here the day the buildbot's stable tree publishes an index."
    ),
    "bizhawk": (
        "Rejected on licensing, which is a different thing from the other "
        "two, and rejected on what BizHawk's own LICENSE says rather than on "
        "an outside reading of it. That file states that the repository "
        "combines MIT-licensed original work with embedded submodules whose "
        "licences are in some cases not provided at all, and then says: its "
        "condition as-is 'should be considered an illegal combination of "
        "several incompatible GPL licenses', and that anyone with an "
        "interest in the details should 'assume it is a minefield'. The "
        "release assets -- BizHawk-2.11.1-win-x64.zip and "
        "BizHawk-2.11.1-linux-x64.tar.gz -- are that combination compiled. "
        "The rule for this plugin is that an item whose redistributability "
        "is unclear is left out and the reason recorded; this is the "
        "clearest case of it, because the ambiguity is upstream's own "
        "stated position (read 2026-08-01)."
    ),
}


def project_for(project_id: str) -> Project:
    """The project with this id, or a refusal naming what exists."""
    key = (project_id or "").strip()
    if key in BY_ID:
        return BY_ID[key]
    if key in DECLINED:
        raise UnknownProject(
            f"{key!r} is not offered by this plugin. {DECLINED[key]}"
        )
    raise UnknownProject(
        f"no project {key!r} is offered by this plugin; it offers: "
        f"{', '.join(sorted(BY_ID))}"
    )
