"""Plugin data assets: declared in the manifest, fetched by the host.

Three layers, and every one of them has to hold on its own:

  * the **manifest parser**, which is where a URL on an undeclared host,
    a missing digest or a name that is not a bare filename stops;
  * `rom_hub.assets`, which fetches, unpacks, verifies and caches -- and
    re-verifies, because a cached file is a file anything could have
    rewritten;
  * the **downloader**, which is the import pipeline's own, so a redirect
    off the allowlist ends an asset fetch exactly as it ends an import.

Nothing here opens a socket: downloads go through `httpx.MockTransport` or
a fake downloader, and the archive is a zip built in `tmp_path`.
"""

import hashlib
import io
import textwrap
import zipfile
from pathlib import Path

import httpx
import pytest

from rom_hub.assets import (
    AssetError,
    describe,
    ensure_assets,
    human_bytes,
    plugin_data_dir,
)
from rom_hub.importer import DownloadError, HttpDownloader
from rom_hub.manifest import MAX_DATA_ASSET_BYTES, ManifestError, parse_manifest

PAYLOAD = b"a tiny stand-in for 42 MiB of SQLite" * 16
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()

BASE = """
[plugin]
slug = "assetful"
name = "Assetful"
version = "0.1.0"
rpp_version = "1"

[capabilities]
metadata = "assetful.metadata:Metadata"

[permissions]
network = ["dataset.example", "*.cdn.example"]
romm_api = []
"""


def manifest_with(body: str, base: str = BASE):
    return parse_manifest(base + textwrap.dedent(body))


def declaration(**overrides) -> str:
    fields = {
        "name": '"db.sqlite"',
        "url": '"https://dataset.example/db.sqlite"',
        "sha256": f'"{PAYLOAD_SHA}"',
        "size_bytes": str(len(PAYLOAD)),
    }
    fields.update(overrides)
    lines = "\n".join(f"{k} = {v}" for k, v in fields.items() if v is not None)
    return f"\n[[data_assets]]\n{lines}\n"


# -- the declaration ------------------------------------------------------


def test_a_declared_asset_is_parsed_whole():
    manifest = manifest_with(declaration(description='"OpenVGDB v29.0"'))
    (asset,) = manifest.data_assets
    assert asset.name == "db.sqlite"
    assert asset.sha256 == PAYLOAD_SHA
    assert asset.size_bytes == len(PAYLOAD)
    assert asset.host == "dataset.example"
    assert asset.archive is None
    assert asset.description == "OpenVGDB v29.0"


def test_a_plugin_that_declares_nothing_gets_an_empty_tuple():
    """Ten shipped plugins are in this shape. It must cost them nothing."""
    assert parse_manifest(BASE).data_assets == ()


def test_an_asset_on_an_undeclared_host_is_refused():
    """The gate this whole feature lives or dies by.

    An asset URL is exactly as privileged as a FetchPlan URL -- the *host*
    fetches it, with the host's own network access. If `permissions.network`
    did not have to cover it, a plugin could declare an empty allowlist, be
    installed on the strength of it, and still make the Hub pull a file
    from anywhere.
    """
    with pytest.raises(ManifestError, match="permissions.network"):
        manifest_with(declaration(url='"https://evil.example/db.sqlite"'))


def test_a_wildcard_in_the_allowlist_covers_an_asset_host():
    manifest = manifest_with(declaration(url='"https://files.cdn.example/db"'))
    assert manifest.data_assets[0].host == "files.cdn.example"


def test_a_plain_http_asset_url_is_refused():
    """`netpolicy` permits https only, and an asset is not an exception."""
    with pytest.raises(ManifestError, match="permissions.network"):
        manifest_with(declaration(url='"http://dataset.example/db.sqlite"'))


def test_an_asset_without_a_digest_is_refused():
    with pytest.raises(ManifestError, match="sha256"):
        manifest_with(declaration(sha256=None))


@pytest.mark.parametrize(
    "bad",
    ['"not hex at all"', f'"{"a" * 63}"', f'"{"a" * 65}"', "123", "true"],
)
def test_a_digest_that_is_not_64_hex_characters_is_refused(bad):
    with pytest.raises(ManifestError, match="sha256"):
        manifest_with(declaration(sha256=bad))


def test_an_upper_case_digest_is_normalised():
    manifest = manifest_with(declaration(sha256=f'"{PAYLOAD_SHA.upper()}"'))
    assert manifest.data_assets[0].sha256 == PAYLOAD_SHA


@pytest.mark.parametrize(
    "name", ['"../escape.sqlite"', '"sub/db.sqlite"', '"C:evil.sqlite"', '"."', '""']
)
def test_an_asset_name_that_is_not_a_bare_filename_is_refused(name):
    """Same validator a FetchPlan filename goes through, by construction.

    `C:evil.sqlite` is the one that matters: it carries no separator, so a
    denylist of path syntax lets it through, and on Windows it resolves
    against C:'s current directory rather than the data directory.
    """
    with pytest.raises(ManifestError):
        manifest_with(declaration(name=name))


def test_an_unknown_key_in_a_declaration_is_refused():
    """Everything unknown is rejected, as everywhere else in this parser."""
    with pytest.raises(ManifestError, match="unknown key"):
        manifest_with(declaration(unpack_to='"anywhere"'))


def test_a_member_needs_an_archive_and_an_archive_needs_a_member():
    with pytest.raises(ManifestError, match="no archive"):
        manifest_with(declaration(member='"db.sqlite"'))
    with pytest.raises(ManifestError, match="no member"):
        manifest_with(declaration(archive='"zip"'))


def test_an_unsupported_archive_format_is_refused():
    with pytest.raises(ManifestError, match="not supported"):
        manifest_with(declaration(archive='"7z"', member='"db.sqlite"'))


def test_an_archive_member_is_a_bare_filename_too():
    """A zip's entry names are attacker-controlled; `../../etc/passwd` is a
    perfectly legal one to write into an archive."""
    with pytest.raises(ManifestError):
        manifest_with(declaration(archive='"zip"', member='"../../etc/passwd"'))


def test_two_assets_may_not_share_a_name():
    with pytest.raises(ManifestError, match="more than once"):
        manifest_with(declaration() + declaration(name='"DB.sqlite"'))


def test_the_number_of_assets_is_bounded():
    body = "".join(declaration(name=f'"db{i}.sqlite"') for i in range(9))
    with pytest.raises(ManifestError, match="at most"):
        manifest_with(body)


def test_a_declared_size_over_the_cap_is_refused():
    with pytest.raises(ManifestError, match="outside"):
        manifest_with(declaration(size_bytes=str(MAX_DATA_ASSET_BYTES + 1)))


def test_size_bytes_must_not_be_a_bool():
    """`bool` is an `int` in Python, so `size_bytes = true` would be 1."""
    with pytest.raises(ManifestError, match="integer"):
        manifest_with(declaration(size_bytes="true"))


def test_a_single_table_rather_than_an_array_says_so():
    with pytest.raises(ManifestError, match=r"\[\[data_assets\]\]"):
        parse_manifest(BASE + '\n[data_assets]\nname = "db.sqlite"\n')


# -- fetching, verifying, caching ----------------------------------------


class FakeDownloader:
    """Writes canned bytes where a real downloader would stream them."""

    def __init__(self, body: bytes = PAYLOAD):
        self.body = body
        self.urls: list[str] = []

    def download(self, url, dest, expected_size=None):
        self.urls.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.body)
        return dest

    def close(self):
        pass


def _ensure(manifest, root, downloader, **kwargs):
    notes: list[str] = []
    resolved = ensure_assets(
        manifest,
        root,
        downloader=downloader,
        announce=notes.append,
        **kwargs,
    )
    return resolved, notes


def test_an_asset_is_fetched_verified_and_cached(tmp_path):
    manifest = manifest_with(declaration())
    downloader = FakeDownloader()
    resolved, notes = _ensure(manifest, tmp_path, downloader)

    path = Path(resolved["db.sqlite"])
    assert path.read_bytes() == PAYLOAD
    assert path.parent == plugin_data_dir(tmp_path, "assetful")
    assert downloader.urls == ["https://dataset.example/db.sqlite"]
    assert any("verified and cached" in n for n in notes)


def test_the_second_run_uses_the_cache_and_fetches_nothing(tmp_path):
    manifest = manifest_with(declaration())
    first = FakeDownloader()
    _ensure(manifest, tmp_path, first)

    second = FakeDownloader()
    resolved, notes = _ensure(manifest, tmp_path, second)
    assert second.urls == [], "a cached asset must not be refetched"
    assert Path(resolved["db.sqlite"]).read_bytes() == PAYLOAD
    assert any("cached and verified" in n for n in notes)


def test_a_cached_asset_is_re_verified_rather_than_assumed(tmp_path):
    """The cache is a directory on a machine, not a vault.

    Anything that can write there can swap the file for another one of the
    same name, and "it was verified when we fetched it" is not a statement
    about the bytes that are there now.
    """
    manifest = manifest_with(declaration())
    resolved, _ = _ensure(manifest, tmp_path, FakeDownloader())
    Path(resolved["db.sqlite"]).write_bytes(b"swapped underneath you")

    refetch = FakeDownloader()
    resolved, notes = _ensure(manifest, tmp_path, refetch)
    assert refetch.urls == ["https://dataset.example/db.sqlite"]
    assert Path(resolved["db.sqlite"]).read_bytes() == PAYLOAD
    assert any("does not match the declared sha256" in n for n in notes)


def test_a_hash_mismatch_refuses_and_caches_nothing(tmp_path):
    """The whole point of a mandatory digest.

    A 9 MB blob fetched over the network and handed to code that trusts it
    is a supply chain nobody agreed to, so the refusal must leave *nothing*
    behind -- not the wrong file under the asset's name, and not the
    partial download, which a resume would otherwise build on.
    """
    manifest = manifest_with(declaration())
    downloader = FakeDownloader(b"not what the manifest promised")
    with pytest.raises(AssetError, match="hashes to"):
        _ensure(manifest, tmp_path, downloader)

    directory = plugin_data_dir(tmp_path, "assetful")
    assert sorted(p.name for p in directory.iterdir()) == []


def test_a_download_failure_is_reported_with_the_url(tmp_path):
    class Broken(FakeDownloader):
        def download(self, url, dest, expected_size=None):
            raise DownloadError("connection reset")

    manifest = manifest_with(declaration())
    with pytest.raises(AssetError, match="connection reset"):
        _ensure(manifest, tmp_path, Broken())


def test_the_fetch_is_announced_with_its_size_and_origin_first(tmp_path):
    """A silent multi-megabyte download is the surprise this avoids."""
    manifest = manifest_with(declaration())
    _, notes = _ensure(manifest, tmp_path, FakeDownloader())
    opening = notes[0]
    assert "fetching data asset" in opening
    assert human_bytes(len(PAYLOAD)) in opening
    assert "https://dataset.example/db.sqlite" in opening
    assert PAYLOAD_SHA[:12] in opening


def test_the_operator_can_veto_the_download(tmp_path):
    manifest = manifest_with(declaration())
    downloader = FakeDownloader()
    with pytest.raises(AssetError, match="plugin assets assetful --fetch"):
        _ensure(manifest, tmp_path, downloader, allow_fetch=False)
    assert downloader.urls == []


def test_a_plugin_with_no_assets_creates_no_directory(tmp_path):
    assert ensure_assets(parse_manifest(BASE), tmp_path) == {}
    assert not (tmp_path / "assetful").exists()


def test_describe_reports_cache_state_without_fetching(tmp_path):
    manifest = manifest_with(declaration())
    (before,) = describe(manifest, tmp_path)
    assert not before.ready
    assert "not cached yet" in before.detail

    _ensure(manifest, tmp_path, FakeDownloader())
    (after,) = describe(manifest, tmp_path)
    assert after.ready
    assert after.path.read_bytes() == PAYLOAD


# -- archives -------------------------------------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buffer.getvalue()


ARCHIVE_DECLARATION = dict(
    url='"https://dataset.example/db.zip"', archive='"zip"', member='"db.sqlite"'
)


def test_the_declared_member_is_extracted_and_verified(tmp_path):
    archive = _zip_bytes(
        # The resource fork is not incidental: OpenVGDB's own release
        # carries `__MACOSX/._openvgdb.sqlite` beside the database, so a
        # suffix match would already pick the wrong member here.
        {"db.sqlite": PAYLOAD, "__MACOSX/._db.sqlite": b"resource fork"}
    )
    manifest = manifest_with(declaration(**ARCHIVE_DECLARATION))
    resolved, _ = _ensure(manifest, tmp_path, FakeDownloader(archive))

    path = Path(resolved["db.sqlite"])
    assert path.read_bytes() == PAYLOAD
    # The archive is a means, not a thing to keep: 9 MB of zip beside 42 MB
    # of database is the operator's disk paying twice for one dataset.
    assert sorted(p.name for p in path.parent.iterdir()) == ["db.sqlite"]


def test_an_archive_without_the_declared_member_says_what_is_in_it(tmp_path):
    manifest = manifest_with(declaration(**ARCHIVE_DECLARATION))
    archive = _zip_bytes({"something-else.sqlite": PAYLOAD})
    with pytest.raises(AssetError, match="something-else.sqlite"):
        _ensure(manifest, tmp_path, FakeDownloader(archive))


def test_bytes_that_are_not_a_zip_are_refused(tmp_path):
    manifest = manifest_with(declaration(**ARCHIVE_DECLARATION))
    with pytest.raises(AssetError, match="could not be unpacked"):
        _ensure(manifest, tmp_path, FakeDownloader(b"this is not a zip file"))


def test_a_member_whose_header_claims_more_than_the_cap_is_refused(tmp_path):
    """Cheap refusal: the central directory says it before a byte is read."""
    manifest = manifest_with(declaration(**ARCHIVE_DECLARATION))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("db.sqlite")
        zf.writestr(info, b"x" * 1024)
    raw = bytearray(buffer.getvalue())
    # Overstate the uncompressed size in both the local header and the
    # central directory, which is what a decompression bomb's header does.
    lied = _rewrite_sizes(bytes(raw), MAX_DATA_ASSET_BYTES + 1)
    with pytest.raises(AssetError, match="over the"):
        _ensure(manifest, tmp_path, FakeDownloader(lied))


def _rewrite_sizes(archive: bytes, claimed: int) -> bytes:
    """Replace every uncompressed-size field with `claimed`.

    zipfile reads the size from the central directory, so a "bomb" needs
    only a header that lies -- which is exactly why the extractor counts
    bytes as it goes rather than trusting this number.
    """
    import struct

    data = bytearray(archive)
    end = data.rfind(b"PK\x05\x06")
    offset = struct.unpack_from("<I", data, end + 16)[0]
    struct.pack_into("<I", data, offset + 24, claimed)  # central directory
    struct.pack_into("<I", data, 22, claimed)  # local header
    return bytes(data)


def test_a_member_that_unpacks_past_the_cap_is_stopped_mid_stream(tmp_path):
    """The header is written by whoever built the zip. Believing it alone
    is how a decompression bomb fills a disk."""
    from rom_hub import assets as assets_module

    manifest = manifest_with(declaration(**ARCHIVE_DECLARATION))
    archive = _zip_bytes({"db.sqlite": b"\0" * (4 * 1024 * 1024)})
    # A cap small enough to be crossed by the fixture, applied to the same
    # constant the extractor reads.
    original = assets_module.MAX_DATA_ASSET_BYTES
    assets_module.MAX_DATA_ASSET_BYTES = 1024
    try:
        with pytest.raises(AssetError, match="over the|understated"):
            _ensure(manifest, tmp_path, FakeDownloader(archive))
    finally:
        assets_module.MAX_DATA_ASSET_BYTES = original
    assert list(plugin_data_dir(tmp_path, "assetful").iterdir()) == []


# -- the downloader's own bound and its redirect handling ----------------


def _downloader(handler, **kwargs) -> HttpDownloader:
    return HttpDownloader(
        allowlist=["dataset.example"],
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_a_declared_length_over_the_bound_costs_no_body(tmp_path):
    pulled = []

    def handler(request):
        pulled.append(request.url.path)
        return httpx.Response(
            200, headers={"Content-Length": str(10_000)}, content=b"x" * 10_000
        )

    downloader = _downloader(handler, max_bytes=1024)
    try:
        with pytest.raises(DownloadError, match="over the 1024-byte limit"):
            downloader.download("https://dataset.example/big", tmp_path / "big")
    finally:
        downloader.close()
    assert not (tmp_path / "big").exists()


def test_a_server_that_lies_about_its_length_is_stopped_while_streaming(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"x" * 10_000)

    downloader = _downloader(handler, max_bytes=1024)
    try:
        with pytest.raises(DownloadError, match="over the 1024-byte limit"):
            downloader.download("https://dataset.example/big", tmp_path / "big")
    finally:
        downloader.close()


def test_an_import_download_is_not_bounded_by_the_asset_limit(tmp_path):
    """ROMs are multi-GB by nature and the operator asked for the file.

    The asset bound is a different budget for a different transaction, and
    adding it must not have quietly capped every import.
    """

    def handler(request):
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024))

    downloader = _downloader(handler)
    try:
        dest = downloader.download("https://dataset.example/rom", tmp_path / "rom")
    finally:
        downloader.close()
    assert dest.stat().st_size == 2 * 1024 * 1024


def test_an_asset_redirect_off_the_allowlist_ends_the_download(tmp_path):
    """The reason the fetch reuses `HttpDownloader` rather than httpx.

    GitHub's release asset genuinely 302s to another host, so redirects
    must be followed -- but only to hosts the plugin declared. This is the
    same defence an import gets, from the same code, so the two cannot
    drift apart.
    """
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "dataset.example":
            return httpx.Response(
                302, headers={"Location": "https://evil.example/db.sqlite"}
            )
        return httpx.Response(200, content=PAYLOAD)

    downloader = _downloader(handler)
    try:
        with pytest.raises(DownloadError, match="outside the plugin's allowlist"):
            downloader.download("https://dataset.example/db", tmp_path / "db")
    finally:
        downloader.close()
    assert seen == ["dataset.example"], "the undeclared host was contacted anyway"


def test_a_redirect_within_the_allowlist_is_followed(tmp_path):
    def handler(request):
        if request.url.path == "/db":
            return httpx.Response(
                302, headers={"Location": "https://files.cdn.example/real"}
            )
        return httpx.Response(200, content=PAYLOAD)

    downloader = HttpDownloader(
        allowlist=["dataset.example", "*.cdn.example"],
        transport=httpx.MockTransport(handler),
        max_bytes=MAX_DATA_ASSET_BYTES,
    )
    try:
        dest = downloader.download("https://dataset.example/db", tmp_path / "db")
    finally:
        downloader.close()
    assert dest.read_bytes() == PAYLOAD


def test_the_asset_fetch_uses_that_downloader_end_to_end(tmp_path):
    """No fake anywhere: the manifest, the redirect and the digest together."""

    def handler(request):
        if request.url.host == "dataset.example":
            return httpx.Response(
                302, headers={"Location": "https://files.cdn.example/blob"}
            )
        return httpx.Response(200, content=PAYLOAD)

    manifest = manifest_with(declaration())
    downloader = HttpDownloader(
        allowlist=list(manifest.network),
        transport=httpx.MockTransport(handler),
        max_bytes=MAX_DATA_ASSET_BYTES,
    )
    try:
        resolved = ensure_assets(manifest, tmp_path, downloader=downloader)
    finally:
        downloader.close()
    assert Path(resolved["db.sqlite"]).read_bytes() == PAYLOAD


# -- what the plugin actually receives -----------------------------------


PLUGIN_SRC = textwrap.dedent(
    '''
    from rom_hub_sdk import DataAssetUnavailable, MetadataPatch, MetadataProvider


    class Metadata(MetadataProvider):
        def enrich(self, rom):
            try:
                path = self.ctx.data_asset("db.sqlite")
            except DataAssetUnavailable as exc:
                return MetadataPatch(name=f"missing: {exc}"[:400])
            with open(path, "rb") as fh:
                return MetadataPatch(name=fh.read(24).decode("utf-8"))
    '''
)


def _run_plugin(tmp_path, data_assets):
    from rom_hub.broker.host import PluginProcess
    from rom_hub.types import RomRef

    plugin_dir = tmp_path / "plugin" / "assetful"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "metadata.py").write_text(PLUGIN_SRC, encoding="utf-8")

    class NoFetcher:
        def get(self, url, params):  # pragma: no cover - never called
            raise AssertionError("this plugin makes no requests")

    with PluginProcess(
        plugin_dir=plugin_dir.parent,
        manifest=manifest_with(declaration()),
        config={},
        fetcher=NoFetcher(),
        # This test is about what crosses the handshake, not about sandbox
        # policy, so it opts out of the fail-closed default exactly as
        # `test_broker_host` does rather than needing a real sandbox.
        allow_unsandboxed=True,
        data_assets=data_assets,
    ) as proc:
        return proc.enrich(RomRef(rom_id=1, name="x"))


def test_the_plugin_opens_the_file_itself_from_the_path_it_is_given(tmp_path):
    """A path, not bytes. 42 MiB through an 8 MiB JSON frame is not a plan,
    and SQLite cannot mmap a bytestring."""
    manifest = manifest_with(declaration())
    resolved = ensure_assets(manifest, tmp_path, downloader=FakeDownloader())
    patch = _run_plugin(tmp_path, resolved)
    assert patch.name == PAYLOAD[:24].decode("utf-8")


def test_a_plugin_given_no_assets_gets_a_legible_refusal(tmp_path):
    """An older host, or a capability that was never given one. Either way
    the plugin must say which asset it wanted, not raise a bare KeyError."""
    patch = _run_plugin(tmp_path, {})
    assert patch.name.startswith("missing: the data asset 'db.sqlite'")


# -- the announcement's arithmetic ---------------------------------------


@pytest.mark.parametrize(
    "size,expected",
    [
        (None, "unknown size"),
        (512, "512 B"),
        (9_118_645, "8.7 MiB"),
        (42_288_128, "40.3 MiB"),
    ],
)
def test_human_bytes_reads_like_the_project_page_does(size, expected):
    assert human_bytes(size) == expected
