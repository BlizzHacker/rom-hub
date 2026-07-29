"""Build target -> the buildbot directory that holds it, and its file suffix.

libretro's buildbot publishes one directory per *build target*, and the
same core is a different file in each: `2048_libretro.so.zip` under Linux,
`2048_libretro.dll.zip` under Windows, `2048_libretro.dylib.zip` under
macOS. So a target is not decoration -- it decides both the URL and which
entries in that URL's index are cores at all.

**No fallback, and specifically no "detect the host OS".** The Hub may
well be running on a machine that is not the one the cores are for: a
Linux container serving a Windows RetroArch over a share is the ordinary
case, not the exotic one. A plugin that quietly chose `linux/x86_64`
because that is what `platform.system()` said would hand a Linux `.so` to
somebody whose frontend loads `.dll`, and the failure would surface much
later as "this core does nothing". So the target comes from config, and a
target that is not in the table below is refused **by name**.

Every row was verified live against buildbot.libretro.com on 2026-07-29
by fetching `<path>.index-extended` and confirming a 200 plus the suffix
its entries actually use. Targets that answer 404 for that file are
deliberately absent rather than listed hopefully:

  * `apple/osx` has no index of its own -- it is a directory of
    sub-architectures (`arm64`, `ppc`, `universal`, `x86`, `x86_64`), and
    the two current ones are listed below.
  * `android` publishes RetroArch `.apk` builds, not loose cores.
  * `emscripten` publishes no `.index-extended` at all.

The keys are the names an operator types. They are spelled `os/arch`
rather than as buildbot paths so that a reader choosing one does not have
to know that macOS lives under `apple/osx` and 32-bit ARM Linux under
`armv7-neon-hf`; the path is this table's business.
"""

from dataclasses import dataclass

BASE = "https://buildbot.libretro.com/nightly/"


@dataclass(frozen=True)
class Target:
    """One build target: where its cores live, and what they are called."""

    #: Path under `BASE`, always ending in "/".
    path: str
    #: The suffix every core file in that directory carries. Used to
    #: recognise a core, and to strip the name back to a core id.
    suffix: str
    #: What to call this in a message to an operator.
    label: str

    @property
    def index_url(self) -> str:
        return f"{BASE}{self.path}.index-extended"

    def file_url(self, filename: str) -> str:
        return f"{BASE}{self.path}{filename}"


#: Operator-typed target name -> where and what. Verified live 2026-07-29.
TARGETS: dict[str, Target] = {
    "linux/x86_64": Target("linux/x86_64/latest/", ".so.zip", "Linux x86_64"),
    "linux/x86": Target("linux/x86/latest/", ".so.zip", "Linux x86"),
    "linux/aarch64": Target("linux/aarch64/latest/", ".so.zip", "Linux aarch64"),
    "linux/armhf": Target("linux/armhf/latest/", ".so.zip", "Linux armhf"),
    "linux/armv7-neon-hf": Target(
        "linux/armv7-neon-hf/latest/", ".so.zip", "Linux armv7 neon hard-float"
    ),
    "windows/x86_64": Target("windows/x86_64/latest/", ".dll.zip", "Windows x86_64"),
    "windows/x86": Target("windows/x86/latest/", ".dll.zip", "Windows x86"),
    "macos/x86_64": Target("apple/osx/x86_64/latest/", ".dylib.zip", "macOS x86_64"),
    "macos/arm64": Target("apple/osx/arm64/latest/", ".dylib.zip", "macOS arm64"),
    "ios/arm64": Target("apple/ios-arm64/latest/", ".dylib.zip", "iOS arm64"),
    "tvos/arm64": Target("apple/tvos-arm64/latest/", ".dylib.zip", "tvOS arm64"),
}

DEFAULT_TARGET = "linux/x86_64"


class NeedsMapping(Exception):
    """The configured target is not in the table, and is named in the message."""


def target_for(name: str) -> Target:
    """The build target called `name`, or a refusal naming it.

    Never returns a default for an unrecognised name. Downloading the
    wrong architecture's core is a failure that shows up as an emulator
    silently refusing to load, which is far more expensive to diagnose
    than this sentence is to read.
    """
    key = (name or "").strip().lower().replace("\\", "/")
    if key in TARGETS:
        return TARGETS[key]
    raise NeedsMapping(
        f"build target {name!r} needs mapping: it is not one of this plugin's "
        f"known libretro buildbot targets. Set `target` in this plugin's "
        f"config to one of: {', '.join(sorted(TARGETS))}. If libretro has "
        f"added a target since, add it to libretro_cores/targets.py rather "
        f"than guessing -- the wrong architecture installs a core that will "
        f"not load."
    )
