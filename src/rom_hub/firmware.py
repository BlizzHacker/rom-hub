"""Installing the BIOS a plugin described.

    plugin.firmware() -> FirmwareArtifact[]
      -> plugin.firmware_plan(item) -> FetchPlan
      -> host downloads into <firmware_dir>/<plugin slug>/
      -> host uploads into the library, if the backend can hold firmware

The same split as `importer` and `cores`, and deliberately the same
machinery: firmware is a binary from the internet landing on the
operator's disk, so it earns exactly the checks a ROM does and gets them
by *reuse*, not by resemblance. `PluginProcess.firmware_plan()` runs the
identical allowlist gate `plan()` runs, `FetchFile` validates the
filenames, and `dest_in_job_dir` is the backstop that keeps every write
inside the directory chosen for it.

## Why this is a capability rather than a `[[data_assets]]` entry

`[[data_assets]]` already does something that looks close: a
manifest-declared, sha256-verified, host-fetched, cached download. It is
the wrong mechanism here, for four reasons that are all about *who
decides*.

* **A data asset is the plugin's own file.** It lands in
  `var/plugin-data/<slug>/` and the plugin is handed the path so it can
  open it. Firmware is not for the plugin at all -- it is for the
  operator's emulator and for their library, and the plugin never sees
  it.
* **A data asset is fetched for every command.** `ensure_assets` runs
  before the subprocess starts, on `search` as much as on `import`. An
  operator running `rom-hub search` should not be pulling BIOS files.
* **The set is fixed at install time.** A manifest lists what it lists.
  Firmware is chosen one item at a time by an operator who has just read
  a catalogue -- the same shape as `cores install <plugin> <core>` -- and
  `MAX_DATA_ASSETS` is 8, which a firmware shelf outgrows immediately.
* **The mandatory sha256 pins the manifest to an upstream release.** For
  a dataset published once that is a virtue. For firmware tracked across
  upstream releases it means a new plugin version for every bump, and the
  plugin cannot answer "what is available *now*".

So: `FetchPlan`, like `cores`. What data assets contributed instead is
their *zip handling*, which this module reimplements for a list of
members rather than one -- see `_extract_members`.

## Where the bytes go

Two places, and only the first is required.

**The operator's disk**, under `<firmware_dir>/<plugin slug>/`. That is
what an emulator reads, and it is configuration exactly like the cores
directory: `$ROM_HUB_HOME/var/firmware` by default,
`ROM_HUB_FIRMWARE_DIR` overrides it, and a plugin can influence neither.

**The library**, when the backend declares `FIRMWARE`. Not every library
server has a firmware store, and the ones that do not are not all alike:
one may have no such concept at all, another may serve BIOS it already
holds while accepting none. Each says so in its own package. That step is
`OPTIONAL` in the capability scheme, so a backend without it costs the
operator the upload and not the download -- see `backends/base.FIRMWARE`.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from rom_hub.backends.base import (
    FIRMWARE,
    BackendError,
    SkippedStep,
    degrade,
)
from rom_hub.paths import (
    UnsafeDestination,
    dest_in_job_dir,
    flat_destination_only,
)
from rom_hub.types import FirmwareArtifact

#: A BIOS is kilobytes; the archive one arrives in is megabytes. This
#: bounds both. Unlike a ROM or a core -- where the operator named a
#: specific multi-gigabyte file and `HttpDownloader` is given no ceiling
#: at all -- nobody types a size here, so the ceiling is the host's.
MAX_FIRMWARE_BYTES = 64 * 1024 * 1024

_UNPACK_CHUNK = 1024 * 1024


class FirmwareError(Exception):
    """Listing or installing firmware failed, with an operator-fit message."""


@dataclass
class FirmwareInstallResult:
    firmware_id: str
    directory: Path
    files: list[Path]
    platform: str
    license: str
    #: How many files were accepted by the library. Zero is normal and not
    #: a failure: the backend may not hold firmware, or may hold it already.
    uploaded: int = 0
    #: Set when the library step did not happen because the backend cannot
    #: do it. Carried out rather than logged away, because a degradation
    #: nobody is told about is indistinguishable from a bug.
    skipped: SkippedStep | None = None
    already_present: list[str] = field(default_factory=list)
    message: str = ""


def find_firmware(items: list[FirmwareArtifact], firmware_id: str) -> FirmwareArtifact:
    """The item with this id, or a message naming the ones that exist.

    Matching is exact. Fuzzy-matching a BIOS would install *a* BIOS rather
    than the one asked for, and the operator would find out when a game
    refused to boot.
    """
    for item in items:
        if item.firmware_id == firmware_id:
            return item
    available = ", ".join(sorted(i.firmware_id for i in items)) or "(none)"
    raise FirmwareError(
        f"no firmware {firmware_id!r} is offered by this plugin; it offers: "
        f"{available}"
    )


def install_firmware(
    plugin,
    firmware: FirmwareArtifact,
    *,
    firmware_dir: Path,
    backend=None,
    downloader=None,
) -> FirmwareInstallResult:
    """Download one firmware item, then file it in the library if it can be.

    `plugin` is a started `PluginProcess` (anything with
    `.firmware_plan()` and a `.manifest`). Every URL in the returned plan
    has already been checked against the plugin's allowlist by
    `firmware_plan()`; every filename is re-checked here against the
    directory it may land in.

    `backend` is optional. Without one this is a download and nothing
    else, which is a complete and useful outcome -- an emulator pointed at
    the firmware directory does not need a library at all.
    """
    firmware_dir = Path(firmware_dir)
    manifest = getattr(plugin, "manifest", None)
    slug = getattr(manifest, "slug", "") or "unknown"
    allowlist = list(getattr(manifest, "network", []))

    # One directory per plugin, so two plugins shipping a `bios.bin` do
    # not overwrite each other. The slug is manifest-validated already;
    # running it through the same containment check as a filename costs
    # nothing and means there is no unchecked path join in this module.
    try:
        target = dest_in_job_dir(firmware_dir, slug)
    except UnsafeDestination as exc:
        raise FirmwareError(str(exc)) from exc

    try:
        plan = plugin.firmware_plan(firmware)
    except Exception as exc:  # noqa: BLE001 - reported, never propagated raw
        raise FirmwareError(
            f"plugin {slug!r} could not plan a download for firmware "
            f"{firmware.firmware_id!r}: {exc}"
        ) from exc

    if firmware.archive is not None and len(plan.files) != 1:
        raise FirmwareError(
            f"plugin {slug!r} declared firmware {firmware.firmware_id!r} as a "
            f"{firmware.archive!r} archive but planned {len(plan.files)} "
            f"downloads; an archive item names exactly one file, the archive"
        )

    destinations = []
    for entry in plan.files:
        try:
            flat_destination_only(entry, what="a firmware install")
            destinations.append((entry, dest_in_job_dir(target, entry.filename)))
        except UnsafeDestination as exc:
            raise FirmwareError(str(exc)) from exc

    target.mkdir(parents=True, exist_ok=True)

    owns_downloader = downloader is None
    if owns_downloader:
        # Imported here rather than at module scope: importer pulls in the
        # job queue, the dedup hasher and the socket.io scanner, none of
        # which installing a BIOS needs.
        from rom_hub.importer import HttpDownloader

        downloader = HttpDownloader(allowlist=allowlist, max_bytes=MAX_FIRMWARE_BYTES)

    written: list[Path] = []
    try:
        for entry, dest in destinations:
            try:
                downloader.download(entry.url, dest, expected_size=entry.size_bytes)
            except Exception as exc:  # noqa: BLE001
                raise FirmwareError(
                    f"downloading {entry.filename!r} for firmware "
                    f"{firmware.firmware_id!r} from {entry.url!r} failed: {exc}"
                ) from exc
            written.append(dest)
    finally:
        if owns_downloader:
            downloader.close()

    if firmware.archive is not None:
        written = _extract_members(firmware, written[0], target, slug=slug)

    result = FirmwareInstallResult(
        firmware_id=firmware.firmware_id,
        directory=target,
        files=written,
        platform=firmware.platform,
        license=firmware.license,
    )
    _file_in_library(result, firmware, backend, slug=slug)
    result.message = _describe(result)
    return result


def _extract_members(
    firmware: FirmwareArtifact, archive: Path, target: Path, *, slug: str
) -> list[Path]:
    """Pull exactly the declared members out of a zip, bounded.

    Exactly, by full-name equality, for the reason `assets._extract_member`
    gives: a Mac-built zip carries `__MACOSX/._dmg_boot.bin` beside
    `dmg_boot.bin`, so "the entry whose name ends in the member" already
    picks the wrong file on real archives. And an entry name is never
    joined onto a path -- the destination is the host's own, built from
    the *basename* of the member the plugin declared and already
    `bare_filename`-validated by `FirmwareArtifact`, so a zip whose entries
    are called `../../etc/passwd` has nowhere to write.

    A member may therefore name a directory inside the archive without
    that directory reaching the filesystem: `share/machines/cbios_sub.rom`
    is looked up under that name and installed as `cbios_sub.rom`. Real
    archives need it -- openMSX is the only publisher of built C-BIOS
    ROMs and it keeps them in `share/machines/`.

    The archive is removed afterwards. What the operator asked for is the
    BIOS; leaving a 1.6 MB emulator zip in the firmware directory beside
    it would be leaving something an emulator scanning that directory has
    to ignore.
    """
    written: list[Path] = []
    try:
        with zipfile.ZipFile(archive) as zf:
            for member in firmware.members:
                try:
                    info = zf.getinfo(member)
                except KeyError:
                    names = sorted(n for n in zf.namelist() if not n.endswith("/"))
                    raise FirmwareError(
                        f"plugin {slug!r}: the archive fetched for firmware "
                        f"{firmware.firmware_id!r} has no member {member!r}; "
                        f"it contains {names[:10]}"
                    ) from None
                if info.file_size > MAX_FIRMWARE_BYTES:
                    raise FirmwareError(
                        f"plugin {slug!r}: member {member!r} declares "
                        f"{info.file_size} bytes unpacked, over the "
                        f"{MAX_FIRMWARE_BYTES}-byte limit"
                    )
                try:
                    # The basename, never the member string. A member may
                    # name a path *inside the archive* -- openMSX keeps the
                    # C-BIOS ROMs under `share/machines/` -- and that path
                    # is a lookup key, not a destination. The install stays
                    # flat and `dest_in_job_dir` still sees a bare name.
                    dest = dest_in_job_dir(target, member.rpartition("/")[2])
                except UnsafeDestination as exc:
                    raise FirmwareError(str(exc)) from exc
                _write_member(zf, info, dest, member=member, slug=slug)
                written.append(dest)
    except FirmwareError:
        _unlink(archive)
        raise
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        _unlink(archive)
        raise FirmwareError(
            f"plugin {slug!r}: the archive fetched for firmware "
            f"{firmware.firmware_id!r} could not be unpacked: {exc}"
        ) from exc

    _unlink(archive)
    return written


def _write_member(zf, info, dest: Path, *, member: str, slug: str) -> None:
    written = 0
    with zf.open(info) as src, dest.open("wb") as out:
        for chunk in iter(lambda: src.read(_UNPACK_CHUNK), b""):
            written += len(chunk)
            if written > MAX_FIRMWARE_BYTES:
                # The header is written by whoever built the zip.
                # Believing it alone is how a decompression bomb fills a
                # disk.
                out.close()
                _unlink(dest)
                raise FirmwareError(
                    f"plugin {slug!r}: member {member!r} unpacked past the "
                    f"{MAX_FIRMWARE_BYTES}-byte limit; the archive's own "
                    f"header understated it"
                )
            out.write(chunk)


def _file_in_library(
    result: FirmwareInstallResult,
    firmware: FirmwareArtifact,
    backend,
    *,
    slug: str,
) -> None:
    """Push the downloaded files into the library, or record why not.

    Mutates `result` rather than returning, because every path through
    here is "the download already succeeded" -- there is no failure of
    this step that should discard files already on disk.
    """
    if backend is None:
        return

    skipped = degrade(
        backend, FIRMWARE, f"filing {firmware.firmware_id!r} in the library"
    )
    if skipped is not None:
        result.skipped = skipped
        return

    try:
        platform_id = backend.platform_id(firmware.platform)
    except BackendError as exc:
        raise FirmwareError(
            f"firmware {firmware.firmware_id!r} downloaded into "
            f"{result.directory}, but it could not be filed: {exc}. Firmware "
            f"is keyed by platform, and plugin {slug!r} says this item is "
            f"for {firmware.platform!r} -- create that platform in the "
            f"library, or install this item for its files alone."
        ) from exc

    try:
        existing = {
            str(row.get("file_name", "")).casefold()
            for row in backend.list_firmware(platform_id)
            if isinstance(row, dict)
        }
    except BackendError as exc:
        raise FirmwareError(
            f"firmware {firmware.firmware_id!r} downloaded into "
            f"{result.directory}, but the library could not be listed to "
            f"check what is already there: {exc}"
        ) from exc

    to_send = []
    for path in result.files:
        if path.name.casefold() in existing:
            result.already_present.append(path.name)
        else:
            to_send.append(path)

    if not to_send:
        return

    try:
        backend.upload_firmware(to_send, platform_id)
    except BackendError as exc:
        raise FirmwareError(
            f"firmware {firmware.firmware_id!r} downloaded into "
            f"{result.directory}, but uploading it failed: {exc}"
        ) from exc
    result.uploaded = len(to_send)


def _describe(result: FirmwareInstallResult) -> str:
    names = ", ".join(path.name for path in result.files)
    line = (
        f"installed firmware {result.firmware_id!r} for {result.platform!r} "
        f"({len(result.files)} file(s): {names}; licence: {result.license}) "
        f"into {result.directory}"
    )
    if result.skipped is not None:
        return f"{line}; {result.skipped}"
    parts = []
    if result.uploaded:
        parts.append(f"{result.uploaded} uploaded to the library")
    if result.already_present:
        parts.append(
            f"{len(result.already_present)} already there "
            f"({', '.join(result.already_present)})"
        )
    if parts:
        return f"{line}; {' and '.join(parts)}"
    return line


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
