"""Plugin data assets: a dataset the plugin declares and the host fetches.

    manifest [[data_assets]] -> host downloads -> (unpack) -> verify sha256
    -> <ROM_HUB_HOME>/var/plugin-data/<slug>/<name> -> the plugin gets a PATH

The gap this closes. RPP v1 gave a plugin no way to obtain a large binary
for its own use, and `openvgdb` is where that stopped being theoretical:
its entire source is one 9,118,645-byte release asset, and a plugin cannot
fetch it for four independent reasons. `ctx.http` caps a response at 4 MiB;
`HttpResponse` carries *text* decoded with `errors="replace"`, so there is
no byte channel at all; the fetcher follows no redirect and exposes no
`Location`, so the GitHub asset's 302 is a dead end; and a per-command
subprocess has nowhere to cache anything between invocations. So the plugin
required the operator to download the file by hand. Honest, but it is not
install-and-go, and it blocks every source that is backed by a dataset.

The design is the same one the rest of this codebase already makes:
**a plugin declares, the host performs.**

**Declared, not requested.** The URL, the digest and the size live in
`manifest.toml`. That is reviewable before install, diffable on update, and
printed by `rom-hub plugin install`. A hypothetical `ctx.download(url)`
would be none of those, and would let a plugin pull arbitrary megabytes at
a moment nobody is looking.

**The same gate as a FetchPlan URL.** `manifest.py` refuses an asset whose
host is not in `permissions.network`, and the fetch runs through
`importer.HttpDownloader`, which is the *same* code an import uses: no
redirect is followed by httpx, each hop is re-checked with `check_url`, and
a hop that leaves the allowlist ends the download. That matters here
specifically — GitHub's release asset 302s to
`release-assets.githubusercontent.com`, so this is a real redirect to a
different host and not a hypothetical one.

**Integrity is mandatory.** The manifest carries the sha256 of the file the
plugin opens. The host verifies before handing over the path and refuses on
mismatch, and a *cached* asset is re-verified on every use rather than
assumed — a file in a cache directory is a file anything on the machine may
have rewritten. There is no trust-on-first-use mode, because a 9 MB blob
fetched over the network and handed to code that trusts it is a supply
chain the operator never agreed to.

**A path, not bytes.** The plugin is told where the file is and opens it
itself, read-only. Bytes would have to cross the JSON-RPC channel, which
caps at 8 MiB per frame and would triple 42 MiB of database into host
memory on the way — and SQLite cannot mmap a bytestring anyway.

**Nothing escapes the data directory.** The asset name and any archive
member go through `types.bare_filename`, the same validator a FetchPlan
filename uses, and every path is joined with `paths.dest_in_job_dir`, the
same containment check the import pipeline uses. Reused rather than
re-implemented: a second copy of a containment rule is a second place for
it to be subtly different.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import MAX_DATA_ASSET_BYTES, DataAsset, Manifest
from .paths import UnsafeDestination, dest_in_job_dir

#: Under `<ROM_HUB_HOME>/var/`, beside the job queue and the download
#: directory. Runtime state that grows: it must not land in the repo, and
#: on a workstation it must not land on the system drive by default either.
DATA_DIR_NAME = "plugin-data"

#: A 9 MB file over a slow link, not an API call. The `ctx.http` timeout
#: (30s) would fail a download that was working fine.
ASSET_TIMEOUT = 600.0

_HASH_CHUNK = 1024 * 1024
_UNPACK_CHUNK = 1024 * 1024

# A partial download is kept between attempts so a failed fetch resumes
# rather than restarting; the verified file is only ever moved into place
# atomically, so a half-written asset can never be mistaken for a good one.
_DOWNLOAD_SUFFIX = ".download"
_INCOMING_SUFFIX = ".incoming"


class AssetError(Exception):
    """An asset could not be fetched, verified, or cached."""


@dataclass(frozen=True)
class AssetState:
    """What `rom-hub plugin assets` reports about one declared asset."""

    asset: DataAsset
    path: Path
    #: True when a verified copy is on disk and ready to use.
    ready: bool
    #: An operator-fit account of `ready`.
    detail: str


def plugin_data_dir(data_root: Path, slug: str) -> Path:
    """The directory this plugin's assets live in.

    One per plugin, so two plugins declaring a `db.sqlite` cannot overwrite
    each other's verified bytes. The slug is manifest-validated already;
    running it through the same containment check as a filename costs
    nothing and means there is no unchecked path join in this module.
    """
    data_root = Path(data_root)
    try:
        return dest_in_job_dir(data_root, slug)
    except UnsafeDestination as exc:
        raise AssetError(str(exc)) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_bytes(size: int | None) -> str:
    """A size an operator can read, for the announcement. `?` if unknown."""
    if size is None:
        return "unknown size"
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TiB"


def describe(manifest: Manifest, data_root: Path) -> list[AssetState]:
    """Report each declared asset's cache state. Fetches nothing.

    Hashes what is already there, because "a file of the right name exists"
    is not the question anyone is asking.
    """
    if not manifest.data_assets:
        return []
    directory = plugin_data_dir(data_root, manifest.slug)
    states = []
    for asset in manifest.data_assets:
        path = _asset_path(directory, asset.name)
        states.append(AssetState(asset, path, *_cache_state(asset, path)))
    return states


def ensure_assets(
    manifest: Manifest,
    data_root: Path,
    *,
    downloader=None,
    announce=None,
    allow_fetch: bool = True,
) -> dict[str, str]:
    """Resolve every asset this plugin declares to a verified local path.

    Returns `{name: path}`, which is what the host puts in the plugin's
    `init` handshake. A plugin declaring none costs nothing at all: no
    directory is created and no announcement is made.

    `announce` is called with one line per asset before anything is
    fetched, so a multi-megabyte download is never a surprise. `allow_fetch`
    is the operator's veto (`ROM_HUB_NO_ASSET_FETCH=1`): a missing asset
    then refuses with the command that would get it, instead of pulling it.
    """
    if not manifest.data_assets:
        return {}

    directory = plugin_data_dir(data_root, manifest.slug)
    resolved: dict[str, str] = {}
    for asset in manifest.data_assets:
        path = ensure_asset(
            asset,
            directory,
            slug=manifest.slug,
            downloader=downloader,
            announce=announce,
            allow_fetch=allow_fetch,
            allowlist=list(manifest.network),
        )
        resolved[asset.name] = str(path)
    return resolved


def ensure_asset(
    asset: DataAsset,
    directory: Path,
    *,
    slug: str,
    allowlist: list[str],
    downloader=None,
    announce=None,
    allow_fetch: bool = True,
) -> Path:
    """One asset: verified on disk, fetched first if it is not there yet."""
    directory = Path(directory)
    path = _asset_path(directory, asset.name)

    ready, detail = _cache_state(asset, path)
    if ready:
        _say(announce, f"{slug}: {detail}")
        return path

    if path.exists():
        # Present and wrong. Say so and start again rather than serving it:
        # a truncated or tampered file that keeps its name is exactly the
        # case a mandatory hash exists to catch.
        _say(announce, f"{slug}: {detail}; refetching")
        _unlink(path)
        _unlink(_sibling(directory, asset.name, _DOWNLOAD_SUFFIX))

    if not allow_fetch:
        raise AssetError(
            f"plugin {slug!r} needs the data asset {asset.name!r} "
            f"({human_bytes(asset.size_bytes)} from {asset.host}) and "
            f"ROM_HUB_NO_ASSET_FETCH is set, so the Hub did not fetch it. "
            f"Run 'rom-hub plugin assets {slug} --fetch' when you want the "
            f"download to happen."
        )

    _say(
        announce,
        f"{slug}: fetching data asset {asset.name!r} -- "
        f"{human_bytes(asset.size_bytes)} from {asset.url} "
        f"(sha256 {asset.sha256[:12]}...). It is verified on arrival and "
        f"cached in {directory}, so this happens once.",
    )
    directory.mkdir(parents=True, exist_ok=True)
    _fetch_and_verify(
        asset,
        directory,
        path,
        slug=slug,
        allowlist=allowlist,
        downloader=downloader,
    )
    _say(announce, f"{slug}: data asset {asset.name!r} verified and cached")
    return path


# -- internals -----------------------------------------------------------


def _asset_path(directory: Path, name: str) -> Path:
    return _sibling(directory, name, "")


def _sibling(directory: Path, name: str, suffix: str) -> Path:
    """`directory/name+suffix`, refusing anything that lands outside.

    `name` is already `bare_filename`-validated by the manifest parser.
    This is the layer that has to hold if that one ever has a gap, exactly
    as `dest_in_job_dir` sits behind `FetchFile`'s validator on the import
    path.
    """
    try:
        return dest_in_job_dir(directory, name + suffix)
    except UnsafeDestination as exc:
        raise AssetError(str(exc)) from exc


def _cache_state(asset: DataAsset, path: Path) -> tuple[bool, str]:
    """`(ready, detail)` for the copy on disk, by hashing it.

    Never by size, never by mtime, never by "the file is there". The hash
    is the whole guarantee and it is cheap next to the download it saves.
    """
    if not path.is_file():
        return False, f"data asset {asset.name!r} is not cached yet"
    try:
        digest = sha256_file(path)
    except OSError as exc:
        return False, f"cached {asset.name!r} could not be read ({exc})"
    if digest != asset.sha256:
        return (
            False,
            f"cached {asset.name!r} does not match the declared sha256 "
            f"(found {digest[:12]}..., expected {asset.sha256[:12]}...)",
        )
    return True, f"data asset {asset.name!r} is cached and verified ({path})"


def _fetch_and_verify(
    asset: DataAsset,
    directory: Path,
    path: Path,
    *,
    slug: str,
    allowlist: list[str],
    downloader,
) -> None:
    download = _sibling(directory, asset.name, _DOWNLOAD_SUFFIX)
    incoming = _sibling(directory, asset.name, _INCOMING_SUFFIX)

    owns_downloader = downloader is None
    if owns_downloader:
        # Imported here rather than at module scope: `importer` pulls in the
        # job queue, the dedup hasher and the socket.io scanner, none of
        # which fetching a dataset needs. The class itself is reused, not
        # copied -- the redirect re-validation is the point.
        from .importer import HttpDownloader

        downloader = HttpDownloader(
            allowlist=allowlist,
            timeout=ASSET_TIMEOUT,
            max_bytes=MAX_DATA_ASSET_BYTES,
        )
    try:
        try:
            downloader.download(asset.url, download, expected_size=asset.size_bytes)
        except Exception as exc:  # noqa: BLE001 - reported, never raw
            raise AssetError(
                f"plugin {slug!r}: fetching data asset {asset.name!r} from "
                f"{asset.url!r} failed: {exc}"
            ) from exc
    finally:
        if owns_downloader:
            downloader.close()

    # A partial *transfer* is worth keeping -- the next attempt resumes it,
    # and `downloader.download` has already raised above if that is what
    # happened. A download that arrived and then failed to unpack is not:
    # the bytes are bad, resuming onto them would fail identically forever,
    # and the operator would have no way to tell the Hub to try again.
    try:
        if asset.archive == "zip":
            _extract_member(asset, download, incoming, slug=slug)
        else:
            _unlink(incoming)
            os.replace(download, incoming)
    except Exception:
        _unlink(download)
        raise

    try:
        digest = sha256_file(incoming)
    except OSError as exc:
        _unlink(incoming)
        raise AssetError(
            f"plugin {slug!r}: the fetched data asset {asset.name!r} could "
            f"not be read back for verification: {exc}"
        ) from exc

    if digest != asset.sha256:
        # Both copies go. Keeping the download would let the next run
        # "resume" onto bytes already known to be wrong.
        _unlink(incoming)
        _unlink(download)
        raise AssetError(
            f"plugin {slug!r}: the data asset {asset.name!r} fetched from "
            f"{asset.url!r} hashes to {digest}, but its manifest declares "
            f"{asset.sha256}. Nothing was cached and the plugin was not "
            f"given a path. Either the source changed or something served "
            f"you a different file."
        )

    # Verified, so publish it in one step. A reader either sees the old
    # state or the new one, never a partly written database.
    os.replace(incoming, path)
    if asset.archive is not None:
        _unlink(download)


def _extract_member(
    asset: DataAsset, archive: Path, incoming: Path, *, slug: str
) -> None:
    """Pull exactly the declared member out of a zip, bounded.

    Exactly, by full-name equality: OpenVGDB's own release carries a
    `__MACOSX/._openvgdb.sqlite` resource fork alongside `openvgdb.sqlite`,
    so "the entry whose name ends in the member" would already pick the
    wrong file here. And the entry name is never joined onto a path --
    the destination is the host's own, so a zip whose entries are named
    `../../etc/passwd` has nowhere to write.
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            try:
                info = zf.getinfo(asset.member)
            except KeyError:
                names = sorted(n for n in zf.namelist() if not n.endswith("/"))
                raise AssetError(
                    f"plugin {slug!r}: the archive fetched for data asset "
                    f"{asset.name!r} has no member {asset.member!r}; it "
                    f"contains {names[:10]}"
                ) from None
            if info.file_size > MAX_DATA_ASSET_BYTES:
                raise AssetError(
                    f"plugin {slug!r}: member {asset.member!r} declares "
                    f"{info.file_size} bytes unpacked, over the "
                    f"{MAX_DATA_ASSET_BYTES}-byte limit"
                )
            _unlink(incoming)
            written = 0
            with zf.open(info) as src, incoming.open("wb") as dest:
                for chunk in iter(lambda: src.read(_UNPACK_CHUNK), b""):
                    written += len(chunk)
                    if written > MAX_DATA_ASSET_BYTES:
                        # The header is written by whoever built the zip.
                        # Believing it alone is how a decompression bomb
                        # fills a disk.
                        dest.close()
                        _unlink(incoming)
                        raise AssetError(
                            f"plugin {slug!r}: member {asset.member!r} "
                            f"unpacked past the {MAX_DATA_ASSET_BYTES}-byte "
                            f"limit; the archive's own header understated it"
                        )
                    dest.write(chunk)
    except AssetError:
        raise
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        _unlink(incoming)
        raise AssetError(
            f"plugin {slug!r}: the archive fetched for data asset "
            f"{asset.name!r} could not be unpacked: {exc}"
        ) from exc


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _say(announce, message: str) -> None:
    if announce is not None:
        announce(message)
