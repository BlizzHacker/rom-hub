"""run_enrich: the host applying a plugin's MetadataPatch.

The plugin's involvement ends when it returns the patch. Fetching the
artwork, holding the RomM token, and deciding which form fields exist are
all host-side, and each of those is a place where an untrusted description
becomes a privileged action.

No test here may reach a live RomM or a live network.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from romm_hub.metadata import EnrichError, rom_ref_from, run_enrich
from romm_hub.types import MetadataPatch, RomRef

REF = RomRef(rom_id=42, name="doom", filename="doom.zip", platform="dos")


class FakePlugin:
    def __init__(self, patch, network=("allowed.example",)):
        self._patch = patch
        self.manifest = SimpleNamespace(slug="meta", network=list(network))
        self.calls: list[RomRef] = []

    def enrich(self, rom):
        self.calls.append(rom)
        return self._patch


class FakeRomm:
    def __init__(self):
        self.calls: list[tuple] = []

    def update_rom(self, rom_id, fields, artwork=None):
        self.calls.append((rom_id, dict(fields), artwork))
        return {"id": rom_id, **fields}


class FakeDownloader:
    def __init__(self, payload=b"\x89PNG-cover", chunks=None):
        self.payload = payload
        self.calls: list[str] = []

    def download(self, url, dest, expected_size=None):
        self.calls.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        return dest

    def close(self):
        pass


def _run(tmp_path, plugin, romm=None, downloader=None):
    return run_enrich(
        plugin,
        REF,
        romm=romm or FakeRomm(),
        work_dir=tmp_path / "artwork",
        downloader=downloader or FakeDownloader(),
    )


def test_only_the_fields_the_plugin_set_are_sent(tmp_path):
    """The failure that would quietly wreck a curated library."""
    romm = FakeRomm()
    plugin = FakePlugin(MetadataPatch(name="Doom"))
    result = _run(tmp_path, plugin, romm)

    assert len(romm.calls) == 1
    rom_id, fields, artwork = romm.calls[0]
    assert rom_id == 42
    assert fields == {"name": "Doom"}
    assert artwork is None
    assert result.rom_id == 42
    assert "name" in result.message


def test_a_patch_with_nothing_in_it_never_touches_romm(tmp_path):
    """A plugin that found nothing must not produce a write at all: an
    empty PUT is still a PUT, and RomM writes what it is given."""
    romm = FakeRomm()
    result = _run(tmp_path, FakePlugin(MetadataPatch()), romm)
    assert romm.calls == []
    assert not result.changed
    assert "no changes" in result.message


def test_artwork_by_url_is_fetched_by_the_host_and_posted(tmp_path):
    romm = FakeRomm()
    downloader = FakeDownloader(payload=b"\x89PNG-cover")
    plugin = FakePlugin(
        MetadataPatch(
            name="Doom",
            artwork_url="https://allowed.example/cover.png",
            artwork_filename="cover.png",
        )
    )
    _run(tmp_path, plugin, romm, downloader)

    assert downloader.calls == ["https://allowed.example/cover.png"]
    _, fields, artwork = romm.calls[0]
    assert fields == {"name": "Doom"}
    filename, data, content_type = artwork
    assert filename == "cover.png"
    assert data == b"\x89PNG-cover"
    assert content_type == "image/png"


def test_artwork_by_bytes_needs_no_fetch_at_all(tmp_path):
    romm = FakeRomm()
    downloader = FakeDownloader()
    plugin = FakePlugin(MetadataPatch(artwork_base64=b"\x89PNG-inline"))
    _run(tmp_path, plugin, romm, downloader)

    assert downloader.calls == []
    _, _, artwork = romm.calls[0]
    assert artwork[1] == b"\x89PNG-inline"


def test_an_artwork_url_outside_the_allowlist_is_refused_here_too(tmp_path):
    """Defence in depth, deliberately kept.

    `PluginProcess.enrich` already gates this, and a real plugin cannot get
    past it. This layer holds when that one has a bug -- the same reasoning
    that keeps `dest_in_job_dir` alive behind `FetchFile`'s validator.
    """
    romm = FakeRomm()
    downloader = FakeDownloader()
    plugin = FakePlugin(
        MetadataPatch(name="x", artwork_url="https://evil.example/cover.png")
    )
    with pytest.raises(EnrichError, match="evil.example"):
        _run(tmp_path, plugin, romm, downloader)

    assert downloader.calls == [], "nothing may be fetched from an undeclared host"
    assert romm.calls == [], "and nothing may be written to RomM either"


def test_an_escaping_artwork_filename_is_refused_before_any_write(tmp_path):
    """MetadataPatch's validator makes this unreachable; model_construct
    skips that validator, which is exactly what a validator bug looks
    like from here."""
    patch = MetadataPatch.model_construct(
        name="x",
        provider_ids={},
        raw_metadata={},
        artwork_url="https://allowed.example/cover.png",
        artwork_base64=None,
        artwork_filename="../escape.png",
    )
    romm = FakeRomm()
    downloader = FakeDownloader()
    with pytest.raises(EnrichError, match="outside"):
        _run(tmp_path, FakePlugin(patch), romm, downloader)
    assert downloader.calls == []
    assert romm.calls == []


def test_oversized_downloaded_artwork_is_refused(tmp_path):
    from romm_hub.types import MAX_ARTWORK_BYTES

    romm = FakeRomm()
    downloader = FakeDownloader(payload=b"\0" * (MAX_ARTWORK_BYTES + 1))
    plugin = FakePlugin(
        MetadataPatch(artwork_url="https://allowed.example/cover.png")
    )
    with pytest.raises(EnrichError, match="over the"):
        _run(tmp_path, plugin, romm, downloader)
    assert romm.calls == []


def test_a_failed_artwork_fetch_does_not_half_apply_the_patch(tmp_path):
    """Either the whole patch lands or none of it does. A name applied with
    the cover silently missing is the worst of both."""

    class BrokenDownloader(FakeDownloader):
        def download(self, url, dest, expected_size=None):
            raise OSError("connection reset")

    romm = FakeRomm()
    plugin = FakePlugin(
        MetadataPatch(name="Doom", artwork_url="https://allowed.example/c.png")
    )
    with pytest.raises(EnrichError, match="artwork"):
        _run(tmp_path, plugin, romm, BrokenDownloader())
    assert romm.calls == []


def test_a_plugin_that_raises_is_reported_not_propagated_raw(tmp_path):
    class Exploding(FakePlugin):
        def enrich(self, rom):
            raise RuntimeError("upstream is down")

    with pytest.raises(EnrichError, match="upstream is down"):
        _run(tmp_path, Exploding(None))


def test_a_romm_failure_is_reported_with_the_rom_id(tmp_path):
    class BrokenRomm(FakeRomm):
        def update_rom(self, rom_id, fields, artwork=None):
            raise RuntimeError("503 from RomM")

    with pytest.raises(EnrichError, match="42"):
        _run(tmp_path, FakePlugin(MetadataPatch(name="Doom")), BrokenRomm())


def test_a_rom_ref_carries_the_identity_and_not_the_library(tmp_path):
    """A RomM rom record has fifty-odd fields. A plugin gets four."""
    ref = rom_ref_from(
        {
            "id": 7,
            "name": "rubik",
            "fs_name": "rubik.zip",
            "platform_slug": "dos",
            "fs_size_bytes": 15420,
            "raw_igdb_metadata": {"secret": "not the plugin's business"},
        },
        7,
    )
    assert ref.rom_id == 7
    assert ref.name == "rubik"
    assert ref.filename == "rubik.zip"
    assert ref.platform == "dos"
    assert ref.size_bytes == 15420
    assert "secret" not in ref.model_dump_json()


def test_a_rom_ref_tolerates_a_sparse_record():
    """A rom with a null name is still a rom worth trying to identify."""
    ref = rom_ref_from({"id": 7, "name": None, "fs_size_bytes": None}, 7)
    assert ref.name == ""
    assert ref.platform is None
    assert ref.size_bytes is None


def test_provider_ids_and_raw_blobs_reach_romm_as_form_fields(tmp_path):
    romm = FakeRomm()
    plugin = FakePlugin(
        MetadataPatch(
            provider_ids={"igdb_id": 7},
            raw_metadata={"raw_igdb_metadata": {"summary": "s"}},
        )
    )
    _run(tmp_path, plugin, romm)
    _, fields, _ = romm.calls[0]
    assert fields["igdb_id"] == "7"
    assert '"summary"' in fields["raw_igdb_metadata"]
