"""The machine an emulator binary has to run on.

A target is not decoration. Every project in `projects.py` publishes one
release carrying builds for a dozen different machines at once -- mGBA
0.10.5 ships seventeen assets, of which two are Windows installers, one is
a 3DS homebrew build and two are debug symbols -- and the target is what
decides which single one of them an operator is offered.

**Never detected from the host OS.** `libretro-cores` makes the same
refusal for the same reason and it is worth repeating rather than
cross-referencing: the Hub is frequently not running on the machine that
will run these binaries. A Linux container serving a household's ROM
library while the emulator runs on a Windows desktop is the ordinary
deployment, not the exotic one. A plugin that read `platform.system()`
would install a `.AppImage` for somebody who needed a `.zip`, and the
symptom -- "nothing happens when I double-click it" -- surfaces long after
the decision that caused it.

So the target is config, and a target that is not in this table is refused
**by name**.

The names are spelled `os/arch` and are deliberately the same vocabulary
`libretro-cores` uses, so that an operator who has already configured one
of them does not have to learn a second spelling for the same machine.
`macos/universal` is the one addition, and it is not a synonym for the
other two macOS names: DuckStation, PCSX2, melonDS, ares and xemu each
publish one fat artifact for both Mac architectures, while Vita3K and Cemu
publish a separate file per architecture. So all three names exist and
each means a different artifact. A project that ships a universal build
has a `macos/universal` cell and no other; a project that ships two has
`macos/x86_64` and `macos/arm64` and no `macos/universal`. Nothing is
listed twice under two names.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    """One machine an emulator can be installed for."""

    #: The name an operator types into config.
    key: str
    #: What to call this in a message to an operator.
    label: str


#: Operator-typed target name -> label. Every entry here is a target that
#: at least one project in `projects.py` actually publishes an asset for;
#: a target no project builds for would be a name that can only ever
#: produce an empty catalogue.
TARGETS: dict[str, Target] = {
    "linux/x86_64": Target("linux/x86_64", "Linux x86_64"),
    "linux/aarch64": Target("linux/aarch64", "Linux aarch64"),
    "windows/x86_64": Target("windows/x86_64", "Windows x86_64"),
    "windows/x86": Target("windows/x86", "Windows x86"),
    "windows/arm64": Target("windows/arm64", "Windows arm64"),
    "macos/universal": Target("macos/universal", "macOS (universal)"),
    "macos/x86_64": Target("macos/x86_64", "macOS x86_64"),
    "macos/arm64": Target("macos/arm64", "macOS arm64"),
}

DEFAULT_TARGET = "linux/x86_64"


class NeedsMapping(Exception):
    """The configured target is not in the table, and is named in the message."""


def target_for(name: str) -> Target:
    """The target called `name`, or a refusal naming it.

    Never falls back to a default. Installing the wrong architecture's
    build is a failure whose symptom is an emulator that will not start,
    which is far more expensive to diagnose than this sentence is to read.
    """
    key = (name or "").strip().lower().replace("\\", "/")
    if key in TARGETS:
        return TARGETS[key]
    raise NeedsMapping(
        f"target {name!r} needs mapping: it is not one of this plugin's known "
        f"targets. Set `target` in this plugin's config to one of: "
        f"{', '.join(sorted(TARGETS))}. If a project has started publishing "
        f"for a machine that is not listed, add it to emulators/targets.py "
        f"and give it an asset pattern in emulators/projects.py rather than "
        f"guessing -- the wrong build installs an emulator that will not run."
    )
