"""emulators `cores`: standalone emulator builds from their own releases.

    config.target -> releases/latest per project -> CoreArtifact[]
    CoreArtifact  -> releases/latest             -> FetchPlan -> the HOST fetches

The plugin never downloads an emulator. It names a URL, and the **host**
fetches it after checking that URL -- and every redirect hop behind it --
against this plugin's own `network` allowlist, and after re-validating the
filename. That is the same gate a ROM import goes through, because a
binary landing on the operator's disk is exactly as privileged as a ROM.


Why `cores` and not a new capability
------------------------------------

RPP's `cores` contract, in `rom_hub_sdk.capabilities.CoreProvider`, is
"list installable emulator binaries; return a `FetchPlan`; the host
installs into a configured local directory". That is a description of this
plugin with nothing left over:

* `CoreArtifact` already carries every field a standalone emulator needs
  and no field it lacks -- `core_id`, `name`, `version`, `system`,
  `description`. `system` is documented there as *a label for the
  operator, not a library platform slug*, which is precisely what
  "Sony PlayStation" is here.
* The host side is already right. `rom_hub.cores.install_core` writes into
  `<cores_dir>/<plugin slug>/`, so these binaries land beside libretro's
  cores but never on top of them, and `ROM_HUB_CORES_DIR` moves the lot.
* A new `emulators` capability would need a line in
  `manifest.KNOWN_CAPABILITIES`, a method on the protocol, a dispatcher
  branch, a CLI subcommand and a host installer -- all of it duplicating
  `cores` exactly, to express a distinction (`.so` versus `.AppImage`)
  that nothing downstream acts on.

**Not `assets`.** A sibling plugin is adding an `assets` capability for
shaders, overlays and cheats, and this could plausibly have gone there --
both are "a file the host puts somewhere local". It should not. Those are
*data an emulator reads*; this is *the emulator*. The consequence of
installing the wrong one is different in kind: a bad shader renders
oddly, a bad binary is code the operator then runs. Keeping executables
under the capability whose entire documentation is about executables --
and whose CLI output an operator already reads as "things I will run" --
is the classification that matches what is at stake, not merely the one
that matches the file operation.


Three decisions that could have gone the other way
--------------------------------------------------

**`plan()` re-reads the release instead of trusting the CoreArtifact.**
The artifact handed back to `plan()` has been out of this process: the
host serialised it, the operator's command chose it, and it returns as a
dict this plugin did not construct. Believing its fields would mean
building a download URL out of a value that made a round trip through
somewhere else. Re-reading costs one API call and means the URL always
comes from what GitHub says right now.

**A project that fails is skipped in `list()` and fatal in `plan()`.**
Four projects mean four independent API calls, and one project having a
bad day should not empty the catalogue -- so `list()` records the failure
in the row it can still show and carries on. `plan()` is a specific
request for a specific binary, so there the same failure is refused
outright.

**Version is the upstream tag, verbatim.** DuckStation's rolling release
is literally tagged `latest`; mGBA's is `0.10.5`. Printing `latest` is
honest about a project that does not do numbered releases, where
substituting a date would invent a version upstream never issued.
"""

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

from .filenames import safe_filename
from .projects import (  # noqa: F401 - AmbiguousAsset re-exported for tests
    BY_ID,
    DECLINED,
    PROJECTS,
    AmbiguousAsset,
    NoAssetForTarget,
    UnknownProject,
    project_for,
)
from .releases import ReleaseError, fetch_release
from .targets import DEFAULT_TARGET, NeedsMapping, target_for  # noqa: F401


class CoreListError(Exception):
    """The catalogue could not be produced at all, and the message says why."""


class Cores(CoreProvider):
    def list(self) -> list[CoreArtifact]:
        target = target_for(self._target_name())
        wanted = self._only()

        projects = list(PROJECTS)
        if wanted:
            unknown = sorted(wanted - set(BY_ID))
            if unknown:
                raise CoreListError(
                    f"this plugin's `only` config names project(s) it does not "
                    f"have: {', '.join(unknown)}. It offers: "
                    f"{', '.join(sorted(BY_ID))}."
                )
            projects = [p for p in projects if p.project_id in wanted]

        cores: list[CoreArtifact] = []
        for project in projects:
            try:
                release = fetch_release(self.ctx.http, project)
                asset_name = project.select(target.key, release.names())
            except NoAssetForTarget:
                # Not an error and not worth a row: this project simply
                # does not build for the machine the operator configured.
                continue
            except (ReleaseError, AmbiguousAsset) as exc:
                # Shown rather than dropped, so a project that breaks is
                # visible in the listing an operator is already reading
                # instead of silently absent from it.
                cores.append(
                    CoreArtifact(
                        core_id=project.project_id,
                        name=f"{project.display} - unavailable",
                        version=None,
                        system=project.system,
                        description=f"unavailable: {exc}"[:1000],
                    )
                )
                continue

            asset = release.named(asset_name)
            size = (
                f"{asset.size_bytes / 1_048_576:.1f} MB"
                if asset.size_bytes
                else "size unknown"
            )
            cores.append(
                CoreArtifact(
                    core_id=project.project_id,
                    # The licence rides in the NAME column because that is
                    # the column `rom-hub cores list` prints last and does
                    # not truncate, and because these four projects do not
                    # agree -- one of them is not open source at all.
                    #
                    # ASCII only, and that is not fussiness: a Windows
                    # console defaults to cp1252, and an em dash here comes
                    # out of `cores list` as a replacement character in the
                    # middle of every row. `rom_hub.catalog.symbol_for`
                    # carries ASCII fallbacks for the same reason.
                    name=f"{project.display} [{project.license}] - {asset_name}"[
                        :200
                    ],
                    version=release.tag,
                    system=project.system,
                    description=(
                        f"{project.display} {release.tag} for {target.label}. "
                        f"Licence: {project.license_note} "
                        f"Asset: {asset_name} ({size}). "
                        f"Release: {release.html_url or project.repo}"
                    )[:1000],
                )
            )
        return cores

    def plan(self, core: CoreArtifact) -> FetchPlan:
        target = target_for(self._target_name())
        project = project_for(core.core_id)

        release = fetch_release(self.ctx.http, project)
        asset_name = project.select(target.key, release.names())
        asset = release.named(asset_name)

        return FetchPlan(
            files=[
                FetchFile(
                    # GitHub's own asset URL, unmodified. It answers 302 to
                    # release-assets.githubusercontent.com, which is why
                    # that host is in the manifest allowlist -- the host
                    # re-checks every hop.
                    url=asset.url,
                    filename=safe_filename(asset.name),
                    size_bytes=asset.size_bytes,
                )
            ],
            # A label for the operator, not a library platform slug --
            # nothing about an emulator binary is filed in a ROM library.
            platform=project.system,
        )

    # -- configuration ---------------------------------------------------

    def _target_name(self) -> str:
        return str(self.ctx.config.get("target") or DEFAULT_TARGET)

    def _only(self) -> set[str]:
        raw = self.ctx.config.get("only") or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(item).strip() for item in raw if str(item).strip()}
