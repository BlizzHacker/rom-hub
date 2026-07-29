"""The enrich pipeline: a plugin describes metadata, the host applies it.

    plugin.enrich(rom_ref) -> MetadataPatch -> [fetch artwork] -> PUT /api/roms/{id}

Same split as `importer`, on a smaller scale. The plugin never holds the
RomM token, never opens a socket, and never names a file the host does not
re-validate. What is different is the failure that matters most: this
capability's worst case is not an escape, it is a *faithful* write.

**A patch is partial by nature, and RomM applies what it is given.** So
"the plugin did not set this" has to mean the field is absent from the
request, never present-and-empty -- verified against a real RomM: a
name-only update left an existing `igdb_id` untouched. `MetadataPatch.
form_fields()` is where that is enforced, and it is why nothing here ever
builds a field mapping from `model_dump()`.

**Either the whole patch lands or none of it does.** Artwork is fetched
before the write, so a cover that cannot be downloaded fails the enrich
rather than applying the name and quietly dropping the image.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from romm_hub.netpolicy import PolicyViolation, check_url
from romm_hub.paths import UnsafeDestination, dest_in_job_dir
from romm_hub.types import MAX_ARTWORK_BYTES, MetadataPatch, RomRef

# Artwork is a cover image, not a ROM. It is fetched into memory to be
# posted straight back out, so the ceiling is the same one MetadataPatch
# applies to inline bytes -- a URL must not be the cheap way around it.
ARTWORK_TIMEOUT = 60.0

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class EnrichError(Exception):
    """An enrich failed, with a message already fit for an operator."""


@dataclass
class EnrichResult:
    rom_id: int
    fields: dict[str, str]
    artwork_bytes: int
    changed: bool
    message: str


def rom_ref_from(rom: dict, rom_id: int, extra: dict | None = None) -> RomRef:
    """Build the `RomRef` a plugin is given, from a RomM rom record.

    Deliberately narrow. RomM's rom schema carries fifty-odd fields
    including every provider's raw metadata blob; a plugin asked "what do
    you know about this game" needs the name, the file and the platform,
    and handing it the rest would be handing an untrusted process the
    library's contents for no reason.

    Anything missing or of the wrong type degrades to empty rather than
    raising: a rom with a null `name` is a rom the plugin should still get
    a chance to identify from its filename.

    `extra` is what the *operator* adds -- `--source-id` most obviously,
    since RomM does not record which plugin an import came from and a
    plugin must not be left to guess which of its items a rom is.
    """

    def text(key: str) -> str:
        value = rom.get(key)
        return value if isinstance(value, str) else ""

    size = rom.get("fs_size_bytes")
    return RomRef(
        rom_id=rom_id,
        name=text("name"),
        filename=text("fs_name"),
        platform=text("platform_slug") or None,
        size_bytes=size if isinstance(size, int) and size >= 0 else None,
        extra={k: v for k, v in (extra or {}).items() if v},
    )


def run_enrich(
    plugin,
    rom: RomRef,
    *,
    romm,
    work_dir: Path,
    downloader=None,
) -> EnrichResult:
    """Enrich one rom through `plugin`, writing the result to RomM.

    `plugin` is a started `PluginProcess` (anything with `.enrich()` and a
    `.manifest`). `work_dir` is where a fetched cover lands on its way to
    RomM; nothing is ever written outside it.
    """
    work_dir = Path(work_dir)
    manifest = getattr(plugin, "manifest", None)
    slug = getattr(manifest, "slug", "") or "unknown"
    allowlist = list(getattr(manifest, "network", []))

    try:
        patch = plugin.enrich(rom)
    except Exception as exc:  # noqa: BLE001 - reported, never propagated raw
        raise EnrichError(
            f"plugin {slug!r} could not enrich rom {rom.rom_id}: {exc}"
        ) from exc

    if patch.is_empty():
        return EnrichResult(
            rom_id=rom.rom_id,
            fields={},
            artwork_bytes=0,
            changed=False,
            message=(
                f"plugin {slug!r} proposed no changes for rom {rom.rom_id}; "
                f"RomM was not modified"
            ),
        )

    fields = patch.form_fields()
    artwork = _artwork(patch, slug, allowlist, work_dir, downloader)

    try:
        romm.update_rom(rom.rom_id, fields, artwork=artwork)
    except Exception as exc:  # noqa: BLE001
        raise EnrichError(
            f"updating rom {rom.rom_id} in RomM failed: {exc}"
        ) from exc

    described = ", ".join(sorted(fields)) or "no fields"
    cover = f" and {len(artwork[1])} bytes of artwork" if artwork else ""
    return EnrichResult(
        rom_id=rom.rom_id,
        fields=fields,
        artwork_bytes=len(artwork[1]) if artwork else 0,
        changed=True,
        message=f"rom {rom.rom_id}: updated {described}{cover}",
    )


def _artwork(
    patch: MetadataPatch,
    slug: str,
    allowlist: list[str],
    work_dir: Path,
    downloader,
) -> tuple[str, bytes, str] | None:
    """Resolve the patch's artwork to `(filename, bytes, content_type)`.

    Inline bytes are already bounded by MetadataPatch. A URL is fetched by
    the host, and only after the same two checks the import pipeline makes
    on a plugin-planned file: the URL against the manifest allowlist, and
    the filename against the directory it is allowed to land in.
    """
    inline = patch.artwork_data()
    if inline is not None:
        return (patch.artwork_filename, inline, _content_type(patch.artwork_filename))

    if patch.artwork_url is None:
        return None

    # Deliberately duplicated from PluginProcess.enrich. That is the layer
    # a real plugin cannot get past; this is the layer that holds if that
    # one has a bug, exactly as dest_in_job_dir sits behind FetchFile's
    # validator. A URL that is checked once, far from where it is used, is
    # a URL that stops being checked the day a caller is added.
    try:
        check_url(patch.artwork_url, allowlist)
    except PolicyViolation as exc:
        raise EnrichError(
            f"plugin {slug!r} asked the host to fetch artwork from a host "
            f"outside its allowlist: {exc}"
        ) from exc

    try:
        dest = dest_in_job_dir(work_dir, patch.artwork_filename)
    except UnsafeDestination as exc:
        raise EnrichError(str(exc)) from exc

    owns_downloader = downloader is None
    if owns_downloader:
        # Imported here rather than at module scope: importer pulls in the
        # job queue, the dedup hasher and the socket.io scanner, none of
        # which an enrich needs.
        from romm_hub.importer import HttpDownloader

        downloader = HttpDownloader(allowlist=allowlist, timeout=ARTWORK_TIMEOUT)
    try:
        downloader.download(patch.artwork_url, dest)
    except Exception as exc:  # noqa: BLE001
        raise EnrichError(
            f"fetching artwork for the patch from {patch.artwork_url!r} failed, "
            f"so nothing was written to RomM: {exc}"
        ) from exc
    finally:
        if owns_downloader:
            downloader.close()

    try:
        size = dest.stat().st_size
    except OSError as exc:
        raise EnrichError(
            f"the artwork fetched from {patch.artwork_url!r} could not be read: "
            f"{exc}"
        ) from exc
    if size > MAX_ARTWORK_BYTES:
        # Checked on disk, before the bytes are read into memory: an
        # allowed host is still allowed to answer with a 4 GB "cover".
        raise EnrichError(
            f"the artwork at {patch.artwork_url!r} is {size} bytes, over the "
            f"{MAX_ARTWORK_BYTES}-byte limit"
        )
    if size == 0:
        raise EnrichError(
            f"the artwork at {patch.artwork_url!r} was empty, and an empty "
            f"cover part is refused by RomM anyway"
        )

    return (
        patch.artwork_filename,
        dest.read_bytes(),
        _content_type(patch.artwork_filename),
    )


def _content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or _DEFAULT_CONTENT_TYPE
