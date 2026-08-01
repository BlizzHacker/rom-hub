"""Installing an emulator core a plugin described.

    plugin.cores() -> CoreArtifact[] -> plugin.core_plan(core) -> FetchPlan
    -> host downloads into <cores_dir>/<plugin slug>/

The same split as `importer`, and deliberately the same machinery: a core
is a binary from the internet landing on the operator's disk, so it earns
exactly the checks a ROM does and gets them by *reuse*, not by
resemblance. `PluginProcess.core_plan()` runs the identical allowlist gate
`plan()` runs, `FetchFile` validates the filenames, and `dest_in_job_dir`
is the backstop that keeps every write inside the directory chosen for it.

Where the bytes go is configuration, never a plugin's decision and never a
constant compiled into this file. The default is `$ROM_HUB_HOME/var/cores`
and `ROM_HUB_CORES_DIR` overrides it; a plugin has no way to influence
either, and no way to write outside whichever one is in force.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rom_hub.paths import (
    UnsafeDestination,
    dest_in_job_dir,
    flat_destination_only,
)
from rom_hub.types import CoreArtifact


class CoreError(Exception):
    """Listing or installing a core failed, with an operator-fit message."""


@dataclass
class CoreInstallResult:
    core_id: str
    directory: Path
    files: list[Path]
    message: str


def find_core(cores: list[CoreArtifact], core_id: str) -> CoreArtifact:
    """The core with this id, or a message naming the ones that exist.

    Matching is exact. Fuzzy-matching an emulator core would install *a*
    core rather than the one asked for, and the operator would find out
    when a game refused to run.
    """
    for core in cores:
        if core.core_id == core_id:
            return core
    available = ", ".join(sorted(c.core_id for c in cores)) or "(none)"
    raise CoreError(
        f"no core {core_id!r} is offered by this plugin; it offers: {available}"
    )


def install_core(
    plugin,
    core: CoreArtifact,
    *,
    cores_dir: Path,
    downloader=None,
) -> CoreInstallResult:
    """Download one core into `cores_dir/<plugin slug>/`.

    `plugin` is a started `PluginProcess` (anything with `.core_plan()` and
    a `.manifest`). Every URL in the returned plan has already been checked
    against the plugin's allowlist by `core_plan()`; every filename is
    re-checked here against the directory it may land in.
    """
    cores_dir = Path(cores_dir)
    manifest = getattr(plugin, "manifest", None)
    slug = getattr(manifest, "slug", "") or "unknown"
    allowlist = list(getattr(manifest, "network", []))

    # One directory per plugin, so two plugins shipping a core of the same
    # name do not overwrite each other. The slug is manifest-validated
    # already; running it through the same containment check as a filename
    # costs nothing and means there is no unchecked path join anywhere in
    # this module.
    try:
        target = dest_in_job_dir(cores_dir, slug)
    except UnsafeDestination as exc:
        raise CoreError(str(exc)) from exc

    try:
        plan = plugin.core_plan(core)
    except Exception as exc:  # noqa: BLE001 - reported, never propagated raw
        raise CoreError(
            f"plugin {slug!r} could not plan a download for core "
            f"{core.core_id!r}: {exc}"
        ) from exc

    destinations = []
    for entry in plan.files:
        try:
            flat_destination_only(entry, what="a core install")
            destinations.append((entry, dest_in_job_dir(target, entry.filename)))
        except UnsafeDestination as exc:
            raise CoreError(str(exc)) from exc

    owns_downloader = downloader is None
    if owns_downloader:
        # Imported here rather than at module scope: importer pulls in the
        # job queue, the dedup hasher and the socket.io scanner, none of
        # which installing a core needs.
        from rom_hub.importer import HttpDownloader

        downloader = HttpDownloader(allowlist=allowlist)

    written: list[Path] = []
    try:
        for entry, dest in destinations:
            try:
                downloader.download(entry.url, dest, expected_size=entry.size_bytes)
            except Exception as exc:  # noqa: BLE001
                raise CoreError(
                    f"downloading {entry.filename!r} for core {core.core_id!r} "
                    f"from {entry.url!r} failed: {exc}"
                ) from exc
            written.append(dest)
    finally:
        if owns_downloader:
            downloader.close()

    names = ", ".join(path.name for path in written)
    return CoreInstallResult(
        core_id=core.core_id,
        directory=target,
        files=written,
        message=(
            f"installed core {core.core_id!r} ({len(written)} file(s): {names}) "
            f"into {target}"
        ),
    )
