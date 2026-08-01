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

from rom_hub.backends.base import ARTWORK, METADATA, CapabilityUnsupported
from rom_hub.metadata import EnrichError, rom_ref_from, run_enrich
from rom_hub.types import MetadataPatch, RomRef

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
    """A `LibraryBackend` with only the two members an enrich reaches.

    `capabilities()` is the one addition the seam demanded: `run_enrich`
    now refuses up front when the active backend cannot write metadata or
    cannot take a cover, and a stand-in that declared nothing would be
    refused too.
    """

    name = "fake"

    def __init__(self, capabilities=(METADATA, ARTWORK)):
        self.calls: list[tuple] = []
        self._capabilities = frozenset(capabilities)

    def capabilities(self):
        return self._capabilities

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
        backend=romm or FakeRomm(),
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
    from rom_hub.types import MAX_ARTWORK_BYTES

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


# -- the provider-id gate -------------------------------------------------
#
# A provider id is the one field a library *acts* on rather than storing,
# so it is the one field the library gets a say in. Measured against RomM
# 4.9.2: ten of the eleven are stored when the provider is not configured
# and `ra_id` answers 500, because RomM re-fetches from RetroAchievements
# whenever it changes and has no key to do it with.


class PolicyBackend(FakeRomm):
    """A backend with an opinion about provider ids."""

    def __init__(self, verdicts, **kwargs):
        super().__init__(**kwargs)
        self._verdicts = verdicts

    def provider_id_policy(self):
        return self._verdicts


def _verdict(field, allowed=True, enriches=False, reason="because"):
    from rom_hub.backends.base import ProviderIdVerdict

    return ProviderIdVerdict(
        field=field, allowed=allowed, enriches=enriches, reason=reason
    )


def test_a_refused_id_is_dropped_and_the_rest_of_the_patch_is_written(tmp_path):
    """Losing a name and a summary because one id would have upset the
    server is the trade `backends.base` refuses to make for artwork."""
    plugin = FakePlugin(
        MetadataPatch(
            name="Kirby's Adventure",
            summary="A platformer.",
            provider_ids={"ra_id": 515, "igdb_id": 1074},
        )
    )
    backend = PolicyBackend(
        {
            "ra_id": _verdict("ra_id", allowed=False, reason="no RA credentials"),
            "igdb_id": _verdict("igdb_id"),
        }
    )
    result = run_enrich(plugin, REF, backend=backend, work_dir=tmp_path)

    _rom_id, fields, _artwork = backend.calls[0]
    assert set(fields) == {"name", "summary", "igdb_id"}
    assert result.withheld_ids == {"ra_id": "no RA credentials"}
    assert "no RA credentials" in result.message


def test_a_patch_of_only_refused_ids_writes_nothing_and_says_why(tmp_path):
    plugin = FakePlugin(MetadataPatch(provider_ids={"ra_id": 515}))
    backend = PolicyBackend(
        {"ra_id": _verdict("ra_id", allowed=False, reason="no RA credentials")}
    )
    result = run_enrich(plugin, REF, backend=backend, work_dir=tmp_path)

    assert backend.calls == []
    assert result.changed is False
    assert result.withheld_ids == {"ra_id": "no RA credentials"}
    assert "no RA credentials" in result.message


def test_an_id_that_makes_the_library_go_and_fetch_is_reported(tmp_path):
    """The half worth having even on a yes: an igdb_id written to a RomM
    that holds IGDB credentials pulls in genre, summary and screenshots,
    and the same id written to one without them does nothing."""
    plugin = FakePlugin(MetadataPatch(name="Kirby", provider_ids={"igdb_id": 1074}))
    backend = PolicyBackend(
        {"igdb_id": _verdict("igdb_id", enriches=True, reason="will fetch from IGDB")}
    )
    result = run_enrich(plugin, REF, backend=backend, work_dir=tmp_path)

    assert result.enriching_ids == {"igdb_id": "will fetch from IGDB"}
    assert "will fetch from IGDB" in result.message
    assert result.withheld_ids == {}


def test_a_backend_with_no_opinion_writes_every_id_as_given(tmp_path):
    """`provider_id_policy` is optional, and a backend that never grows one
    behaves exactly as it did before the gate existed."""
    plugin = FakePlugin(MetadataPatch(provider_ids={"ra_id": 515, "moby_id": 9}))
    backend = FakeRomm()
    result = run_enrich(plugin, REF, backend=backend, work_dir=tmp_path)

    _rom_id, fields, _artwork = backend.calls[0]
    assert fields == {"ra_id": "515", "moby_id": "9"}
    assert result.withheld_ids == {}


def test_a_backend_whose_policy_raises_is_permissive_not_fatal(tmp_path):
    """The opposite direction to `capabilities_of`, deliberately. A backend
    that cannot say which ids are safe has told us nothing about them, and
    withholding every id on a transport blip would make every enrich
    quietly poorer -- where the failure this returns to is one loud 500."""

    class Broken(FakeRomm):
        def provider_id_policy(self):
            raise RuntimeError("heartbeat unreachable")

    plugin = FakePlugin(MetadataPatch(provider_ids={"ra_id": 515}))
    backend = Broken()
    run_enrich(plugin, REF, backend=backend, work_dir=tmp_path)
    assert backend.calls[0][1] == {"ra_id": "515"}


def test_the_summary_reaches_the_library_as_a_form_field(tmp_path):
    """RomM stores `summary` where it discards every `raw_*_metadata`."""
    plugin = FakePlugin(MetadataPatch(summary="Developed by HAL Laboratory."))
    backend = FakeRomm()
    run_enrich(plugin, REF, backend=backend, work_dir=tmp_path)
    assert backend.calls[0][1] == {"summary": "Developed by HAL Laboratory."}
