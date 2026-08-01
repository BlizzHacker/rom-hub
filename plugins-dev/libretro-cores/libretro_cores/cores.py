"""libretro-cores `cores`: the buildbot's catalogue, and one download.

    config.target -> .index-extended -> CoreArtifact[]   (+ core info)
    CoreArtifact  -> .index-extended -> FetchPlan -> the HOST downloads
                     the core AND its .info

The plugin never fetches a core. It names a URL and the **host** fetches
it, after checking that URL against this plugin's own `network` allowlist
and re-validating the filename -- the same gate a ROM import goes
through, because a core is a binary landing on the operator's disk and
that is exactly as privileged as a ROM.

## What a core *is*, not only what it is called

The buildbot's `.index-extended` is a filename, a date and a crc32. On
its own that makes `cores list` a list of 218 identifiers, which answers
"what is available" and not "which one do I want". libretro publishes the
missing half separately, as one `.info` file per core, and this plugin
now uses it in two different ways for two different reasons.

**In the catalogue, from a generated snapshot.** `coreinfo.py` is 305
rows produced by `scripts/render_core_info.py` from
`libretro/libretro-core-info`. It carries the system, the manufacturer,
the core's own licence, the extensions it loads and the BIOS it requires
-- so `cores list` prints what the core runs and what it will need, and
the `system` config key can narrow 218 cores to the handful for one
console. This is a snapshot and not a request, because there is no way to
read 305 files in fewer than 305 requests and a catalogue nobody waits
for is not a catalogue.

**At install, live.** `plan()` adds `<core>_libretro.info` to the
FetchPlan, from `raw.githubusercontent.com`, so the file RetroArch
actually reads is the current one rather than the snapshot. That is not a
nicety: RetroArch reads `.info` from its `libretro_info_dir` to learn a
core's display name, its extensions and its firmware requirements, and a
core installed without one shows up in the frontend as a filename that
loads nothing in particular.

## Three decisions that could have gone the other way

**`plan()` re-reads the index instead of trusting the CoreArtifact.** The
`CoreArtifact` handed back to `plan()` has been out of this process: the
host serialised it, the operator's command chose it, and it arrives as a
dict this plugin did not construct. Believing its fields would mean
building a URL out of a value that made a round trip through somewhere
else. Re-reading costs one request against a 10 KB text file and means
the URL is always built from what the buildbot says right now.

**`version` carries the build date, and calls it that.** These are
nightly builds; they have no version number. `CoreArtifact.version` is a
free-text field and putting `"2026-07-29"` in it is honest, where
inventing `"1.0"` would imply a release that does not exist.

**A core the info data does not know still gets no system.** See
`systems.py`. `CoreArtifact.system` is optional precisely so a plugin can
decline to answer, and a plausible-looking guess in a column an operator
reads while choosing is worse than a blank. That is now 25 cores rather
than 112, but the rule has not changed.
"""

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

from .coreinfo import info_for, matches_system
from .filenames import safe_filename
from .index import IndexError_, parse_index
from .info import INFO_HOST, info_filename, info_url  # noqa: F401
from .systems import system_for
from .targets import DEFAULT_TARGET, NeedsMapping, target_for  # noqa: F401

# The host refuses a catalogue over `rom_hub.types.MAX_CORES_PER_PLUGIN`
# (256). Linux x86_64 shipped 218 cores on 2026-07-29, which is close
# enough that the ceiling is a real event rather than a theoretical one --
# so it is checked here, where the message can name the config keys that
# fix it, instead of surfacing as the host's generic "over the limit".
MAX_CORES = 256


class CoreListError(Exception):
    """The catalogue could not be produced, and the message says why."""


class UnknownCore(Exception):
    """No such core in this target's index."""


def describe(core_id: str, target_label: str, built: str) -> str:
    """The DESCRIPTION column: what this core is, needs and is licensed as.

    Assembled from `coreinfo` where libretro says something and silently
    shorter where it does not, so a core with no upstream info reads as a
    build stamp rather than as a row of empty labels.
    """
    row = info_for(core_id)
    parts = []
    if row.get("display"):
        parts.append(row["display"])
    if row.get("extensions"):
        parts.append(f"loads {row['extensions']}")
    firmware = row.get("firmware") or []
    if firmware:
        # The single most useful thing this plugin can say. A core whose
        # BIOS is missing does not fail at install, it fails much later
        # with a black screen -- and libretro already knows which files it
        # wants. Only the ones libretro does NOT mark optional get here;
        # see `render_core_info.required_firmware`.
        parts.append(f"needs BIOS: {', '.join(firmware)}")
    if row.get("license"):
        # The CORE's licence, which is not this plugin's and not
        # libretro's: Snes9x says "Non-commercial".
        parts.append(f"core licence: {row['license']}")
    parts.append(f"{target_label} build, {built}")
    return " -- ".join(parts)[:1000]


class Cores(CoreProvider):
    def list(self) -> list[CoreArtifact]:
        target = target_for(self._target_name())
        entries = self._entries(target)

        wanted = self._only()
        if wanted:
            entries = [e for e in entries if e.core_id in wanted]

        system = self._system()
        if system:
            entries = [e for e in entries if matches_system(e.core_id, system)]
            if not entries:
                raise CoreListError(
                    f"no core in libretro's {target.label} index matches "
                    f"system {system!r}. That is matched against the system "
                    f"name, the manufacturer and every libretro database name "
                    f"in this plugin's core-info table, case-insensitively -- "
                    f'try a shorter word ("snes", "playstation", "atari"), or '
                    f"clear the `system` config key to see everything."
                )

        if len(entries) > MAX_CORES:
            raise CoreListError(
                f"libretro's {target.label} build target offers {len(entries)} "
                f"cores, over the {MAX_CORES} a plugin may return. Narrow it "
                f"with this plugin's `system` config key, which takes a "
                f'console or manufacturer name such as "Game Boy", or with '
                f"`only`, which takes a list of core ids."
            )

        return [
            CoreArtifact(
                core_id=entry.core_id,
                name=entry.core_id,
                # A build stamp, not a release. See the module docstring.
                version=entry.built,
                system=system_for(entry.core_id),
                description=describe(entry.core_id, target.label, entry.built),
            )
            for entry in entries
        ]

    def plan(self, core: CoreArtifact) -> FetchPlan:
        target = target_for(self._target_name())
        entries = self._entries(target)

        entry = next((e for e in entries if e.core_id == core.core_id), None)
        if entry is None:
            raise UnknownCore(
                f"libretro's {target.label} build target has no core "
                f"{core.core_id!r} in its index. Run `rom-hub cores list "
                f"libretro-cores` to see what it does have -- the buildbot "
                f"drops cores that stop building, so a core that was there "
                f"last week may not be today."
            )

        files = [
            FetchFile(
                url=target.file_url(entry.filename),
                filename=safe_filename(entry.filename),
                # The index carries no size, and the crc32 column is not
                # one. The host learns the length from the response.
                size_bytes=None,
            )
        ]

        # The `.info` beside the core, when libretro has one for it. Only
        # for cores in the generated table, because that table is the
        # evidence the file exists -- planning a URL for a core libretro
        # has no info file for would 404 the install of a core that was
        # otherwise fine. 305 cores have one; the buildbot ships 218 for
        # Linux x86_64, and every one of those is in the table.
        if info_for(entry.core_id):
            files.append(
                FetchFile(
                    url=info_url(entry.core_id),
                    filename=info_filename(entry.core_id),
                    size_bytes=None,
                )
            )

        return FetchPlan(
            files=files,
            # A label for the operator, not a RomM platform slug -- nothing
            # about a core is filed in a library. The emulated system when
            # this plugin knows it; otherwise the build target, which is the
            # one thing always true about these bytes. Never a guess at what
            # the core runs.
            platform=system_for(entry.core_id) or target.label,
        )

    # -- configuration ---------------------------------------------------

    def _target_name(self) -> str:
        return str(self.ctx.config.get("target") or DEFAULT_TARGET)

    def _only(self) -> set[str]:
        raw = self.ctx.config.get("only") or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(item).strip() for item in raw if str(item).strip()}

    def _system(self) -> str:
        return str(self.ctx.config.get("system") or "").strip()

    # -- the network -----------------------------------------------------

    def _entries(self, target):
        """This target's index, parsed.

        Not cached. `cores list` and `cores install` are separate CLI
        invocations and therefore separate plugin processes, so a cache
        would never be hit across the pair it would exist to help; within
        one call the index is read once.
        """
        response = self.ctx.http.get(target.index_url)
        if response.status_code != 200:
            raise IndexError_(
                f"libretro's buildbot answered HTTP {response.status_code} for "
                f"the {target.label} core index ({target.index_url})"
            )
        return parse_index(response.text, target.suffix)
