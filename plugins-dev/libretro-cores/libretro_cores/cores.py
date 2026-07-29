"""libretro-cores `cores`: the buildbot's catalogue, and one download.

    config.target -> .index-extended -> CoreArtifact[]
    CoreArtifact  -> .index-extended -> FetchPlan -> the HOST downloads

The plugin never fetches a core. It names a URL and the **host** fetches
it, after checking that URL against this plugin's own `network` allowlist
and re-validating the filename -- the same gate a ROM import goes
through, because a core is a binary landing on the operator's disk and
that is exactly as privileged as a ROM.

Three decisions here are the careful half of choices that could have gone
the other way.

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

**A core the systems table does not know gets no system.** See
`systems.py`. `CoreArtifact.system` is optional precisely so a plugin can
decline to answer, and a plausible-looking guess in a column an operator
reads while choosing is worse than a blank.
"""

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

from .filenames import safe_filename
from .index import IndexError_, parse_index
from .systems import system_for
from .targets import DEFAULT_TARGET, NeedsMapping, target_for  # noqa: F401

# The host refuses a catalogue over `rom_hub.types.MAX_CORES_PER_PLUGIN`
# (256). Linux x86_64 shipped 218 cores on 2026-07-29, which is close
# enough that the ceiling is a real event rather than a theoretical one --
# so it is checked here, where the message can name the config key that
# fixes it, instead of surfacing as the host's generic "over the limit".
MAX_CORES = 256


class CoreListError(Exception):
    """The catalogue could not be produced, and the message says why."""


class UnknownCore(Exception):
    """No such core in this target's index."""


class Cores(CoreProvider):
    def list(self) -> list[CoreArtifact]:
        target = target_for(self._target_name())
        entries = self._entries(target)
        wanted = self._only()
        if wanted:
            entries = [e for e in entries if e.core_id in wanted]

        if len(entries) > MAX_CORES:
            raise CoreListError(
                f"libretro's {target.label} build target offers {len(entries)} "
                f"cores, over the {MAX_CORES} a plugin may return. Narrow it "
                f"with this plugin's `only` config key, which takes a list of "
                f"core ids."
            )

        cores = []
        for entry in entries:
            system = system_for(entry.core_id)
            cores.append(
                CoreArtifact(
                    core_id=entry.core_id,
                    name=entry.core_id,
                    # A build stamp, not a release. See the module docstring.
                    version=entry.built,
                    system=system,
                    description=(
                        f"libretro core for {target.label}, built {entry.built}"
                    ),
                )
            )
        return cores

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

        return FetchPlan(
            files=[
                FetchFile(
                    url=target.file_url(entry.filename),
                    filename=safe_filename(entry.filename),
                    # The index carries no size, and the crc32 column is not
                    # one. The host learns the length from the response.
                    size_bytes=None,
                )
            ],
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
