"""Which emulators this plugin offers, and which asset each one means.

One row per project. Every field was read from the project's own
repository on 2026-07-29 -- the licence from its `LICENSE`/`COPYING` file
via `GET /repos/{owner}/{repo}/license`, the asset names from
`GET /repos/{owner}/{repo}/releases/latest`.

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
these four projects do not agree and the difference matters to whoever is
about to install one. mGBA is MPL-2.0, PCSX2 and melonDS are GPL-3.0, and
DuckStation is **not open source at all** -- it relicensed to
CC BY-NC-ND 4.0, which permits non-commercial redistribution of the
unmodified work and forbids derivatives. Nothing here redistributes
anything: the plugin names the project's own release URL and the host
fetches it once, for the operator who asked. But an operator is entitled
to read what they are installing without leaving the terminal.
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
        "answers 404 (checked 2026-07-29), because its builds ship from "
        "dolphin-emu.org/download/ instead. That page cannot be used from "
        "here either: dolphin-emu.org is behind a bunny.net JavaScript "
        "challenge that answers 403 to everything, including its own "
        "/robots.txt. A plugin with no sockets and no browser cannot pass a "
        "proof-of-work challenge, and working around an anti-bot wall is not "
        "something this plugin will do. Dolphin is therefore out of scope "
        "until it publishes a machine-readable release feed."
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
