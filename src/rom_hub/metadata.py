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

**A provider id is the one field the library acts on, so the library gets
a say.** Every other field in a patch is stored verbatim. A provider id
makes RomM go and fetch from that provider, which needs that provider's
key -- and on a RomM without one, `ra_id` answers 500 while the other ten
answer 200 and store the number. A plugin cannot see any of that. So
`_gate_provider_ids` asks the backend, per field, before the write:
refused ids are dropped and their reasons carried out in
`EnrichResult.withheld_ids`, and ids that *will* pull in real metadata are
reported in `enriching_ids`. Dropping one id is not allowed to cost the
name and the summary alongside it, for the same reason an artwork-less
backend does not cost them.

The one exception is a backend that has no artwork support *at all*, and
it is not quiet: `ARTWORK` is classified optional in
`rom_hub.backends.base`, so the cover is dropped before it is fetched,
the fields are written, and the result says which part did not happen.
Losing a name and a release date because the library stores no images is
a worse answer than writing them. `METADATA` is classified essential and
still refuses -- an enrich that writes nothing is not a degraded enrich.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from rom_hub.backends.base import (
    ARTWORK,
    METADATA,
    LibraryBackend,
    SkippedStep,
    degrade,
    provider_id_policy,
    require,
)
from rom_hub.netpolicy import PolicyViolation, check_url
from rom_hub.paths import UnsafeDestination, dest_in_job_dir
from rom_hub.types import (
    MAX_ARTWORK_BYTES,
    PROVIDER_ID_FIELDS,
    MetadataPatch,
    RomRef,
)

_ID_FIELDS = PROVIDER_ID_FIELDS

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
    #: Optional parts of the patch the backend could not take, dropped
    #: rather than fatal. Already spelled out in `message`.
    degraded: tuple[SkippedStep, ...] = ()
    #: Provider ids the plugin proposed and the backend refused, mapped to
    #: the backend's reason. Absent from `fields` because they were not
    #: written; here rather than nowhere because "an id was silently not
    #: written" and "the source did not know one" look identical to an
    #: operator, and only one of them has a fix.
    withheld_ids: dict[str, str] = field(default_factory=dict)
    #: Provider ids that were written *and* that the backend says will
    #: make it go and fetch that provider's own metadata. The reason a
    #: name-only plugin becomes a full one; worth reporting for the same
    #: reason the withheld ones are.
    enriching_ids: dict[str, str] = field(default_factory=dict)


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
    backend: LibraryBackend,
    work_dir: Path,
    downloader=None,
) -> EnrichResult:
    """Enrich one rom through `plugin`, writing the result to the library.

    `plugin` is a started `PluginProcess` (anything with `.enrich()` and a
    `.manifest`). `backend` is a `LibraryBackend`; nothing here knows
    which one. `work_dir` is where a fetched cover lands on its way to it;
    nothing is ever written outside it.
    """
    require(backend, METADATA, "enriching a rom's metadata")
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
    fields, withheld, enriching = _gate_provider_ids(fields, backend)

    if not fields and not patch.has_artwork():
        # Every field the plugin proposed was a provider id the backend
        # refuses. Sending an empty patch would report a change that did
        # not happen; refusing silently would look like the plugin knew
        # nothing. So the reasons the backend gave are the message.
        return EnrichResult(
            rom_id=rom.rom_id,
            fields={},
            artwork_bytes=0,
            changed=False,
            message=(
                f"plugin {slug!r} proposed only provider ids the "
                f"{getattr(backend, 'name', 'active')!r} backend will not "
                f"take for rom {rom.rom_id}, so nothing was written. "
                + "; ".join(f"{name}: {why}" for name, why in sorted(withheld.items()))
            ),
            withheld_ids=withheld,
        )

    # The artwork decision, made before `_artwork` -- which is where the
    # cover would be fetched over the network. A backend that cannot take
    # a cover costs no download either way.
    #
    # ARTWORK is classified optional in `backends.base`: a patch carrying
    # a name, a release date and an igdb_id *and* a cover should not lose
    # all four because the backend stores no images. So the cover is
    # dropped, the rest is written, and the skip is in the result.
    skipped: list[SkippedStep] = []
    if patch.has_artwork():
        artwork_skip = degrade(
            backend, ARTWORK, f"the cover art plugin {slug!r} proposed"
        )
        if artwork_skip is not None:
            skipped.append(artwork_skip)

    if skipped and not fields:
        # Nothing survives the skip. Writing an empty patch would report a
        # change that did not happen; this is the one shape where dropping
        # the cover leaves no operation at all.
        return EnrichResult(
            rom_id=rom.rom_id,
            fields={},
            artwork_bytes=0,
            changed=False,
            message=(
                f"plugin {slug!r} proposed only cover art for rom "
                f"{rom.rom_id}, and {str(skipped[0])}; nothing was written"
            ),
            degraded=tuple(skipped),
            withheld_ids=withheld,
        )

    artwork = (
        None if skipped else _artwork(patch, slug, allowlist, work_dir, downloader)
    )

    try:
        backend.update_rom(rom.rom_id, fields, artwork=artwork)
    except Exception as exc:  # noqa: BLE001
        raise EnrichError(
            f"updating rom {rom.rom_id} in the library failed: {exc}"
        ) from exc

    described = ", ".join(sorted(fields)) or "no fields"
    cover = f" and {len(artwork[1])} bytes of artwork" if artwork else ""
    note = ". " + "; ".join(str(step) for step in skipped) if skipped else ""
    if withheld:
        note += ". Withheld " + "; ".join(
            f"{name} ({why})" for name, why in sorted(withheld.items())
        )
    if enriching:
        note += ". " + "; ".join(
            f"{name} {why}" for name, why in sorted(enriching.items())
        )
    return EnrichResult(
        rom_id=rom.rom_id,
        fields=fields,
        artwork_bytes=len(artwork[1]) if artwork else 0,
        changed=True,
        message=f"rom {rom.rom_id}: updated {described}{cover}{note}",
        degraded=tuple(skipped),
        withheld_ids=withheld,
        enriching_ids=enriching,
    )


def _gate_provider_ids(
    fields: dict[str, str], backend: LibraryBackend
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Split the patch's fields by what the backend says it will accept.

    Returns `(writable, withheld, enriching)`.

    A plugin knows what a provider's id *is*. It cannot know whether the
    library on the other end holds that provider's credentials, and on
    RomM that is the difference between a 200 and a 500 -- so the decision
    belongs here, where the backend is, rather than behind a per-plugin
    "write other providers' ids?" flag that every plugin author would have
    to guess the right default for and every operator would have to set
    once per plugin.

    Withholding is a *filter*, never a failure. The patch's name, summary
    and remaining ids are still written: losing a curated title and a
    release date because one id would have upset the server is exactly the
    trade `backends.base` refuses to make for artwork, and it is the same
    trade here.
    """
    proposed = {name: value for name, value in fields.items() if name in _ID_FIELDS}
    if not proposed:
        return dict(fields), {}, {}

    policy = provider_id_policy(backend)
    writable = dict(fields)
    withheld: dict[str, str] = {}
    enriching: dict[str, str] = {}
    for name in proposed:
        verdict = policy.get(name)
        if verdict is None:
            continue
        if not verdict.allowed:
            writable.pop(name, None)
            withheld[name] = verdict.reason
        elif verdict.enriches:
            enriching[name] = verdict.reason
    return writable, withheld, enriching


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
        from rom_hub.importer import HttpDownloader

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
