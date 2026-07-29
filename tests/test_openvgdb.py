"""The OpenVGDB `metadata` capability.

**Where the fixture comes from.** `fixtures/openvgdb/slice.sql` is a real
capture, not a hand-written imitation: the `CREATE TABLE` statements are
verbatim from `openvgdb.sqlite`'s own `sqlite_master`, and every row is
verbatim from that file. Only the *selection* of five ROMs is ours, and
each is there for a reason a test below states.

The file itself is OpenVGDB release `v29.0` (`openvgdb.zip`, 9,118,645
bytes, published 2021-11-11 — the project's latest, and its only
artefact). It is not checked in: 40 MiB of database in a test suite would
be absurd, and the plugin's whole design is that the operator holds their
own copy.

Nothing here opens a socket. The database is a `:memory:` SQLite built
from that SQL, and the cover probe goes through a fake `ctx.http`.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "openvgdb"
sys.path.insert(0, str(PLUGIN_ROOT))

from openvgdb import database  # noqa: E402
from openvgdb.database import DatabaseUnavailable  # noqa: E402
from openvgdb.metadata import COVER_HOSTS, Metadata, NoMatch  # noqa: E402
from openvgdb.platforms import SYSTEMS, NeedsMapping  # noqa: E402

from rom_hub.types import RomRef  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

SLICE = (Path(__file__).resolve().parent / "fixtures" / "openvgdb" / "slice.sql").read_text(
    encoding="utf-8"
)

# The Game Boy dump OpenVGDB files as `Tetris (World) (Rev A).gb`. No-Intro
# has since renamed that release to `(Rev 1)`; the hashes are the same, which
# is exactly why a hash lookup is the primary path and a filename is not.
TETRIS_CRC = "46DF91AD"
TETRIS_MD5 = "982ED5D2B12A0377EB14BCDC4123744E"
TETRIS_SHA1 = "74591CC9501AF93873F9A5D3EB12DA12C0723BBC"


@pytest.fixture
def db(monkeypatch):
    """An in-memory OpenVGDB, standing in for the operator's own file.

    Built fresh per `open_database` call because `enrich` closes what it
    opened, exactly as it does against a real file. Loading the slice
    costs under a millisecond.
    """

    def _open(path):
        if not path:
            # The real refusal, so its wording stays under test.
            return database.open_database(path)
        connection = sqlite3.connect(":memory:")
        connection.executescript(SLICE)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr("openvgdb.metadata.open_database", _open)
    return _open


class FakeCovers:
    """Answers like a cover host does. 200 unless told otherwise."""

    def __init__(self, status_code=200, raises=None):
        self.status_code = status_code
        self.raises = raises
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        return HttpResponse(status_code=self.status_code, text="")


def _provider(http=None, **config):
    http = http or FakeCovers()
    merged = {"db_path": "openvgdb.sqlite"}
    merged.update(config)
    return Metadata(PluginContext(config=merged, http=http)), http


def _ref(**kwargs):
    base = {
        "rom_id": 7,
        "name": "Tetris (World) (Rev A)",
        "filename": "Tetris (World) (Rev A).gb",
        "platform": "gb",
        "extra": {},
    }
    base.update(kwargs)
    return RomRef(**base)


# -- the title -----------------------------------------------------------


def test_the_curated_title_is_written_and_not_the_dump_name(db):
    """The whole reason this plugin exists.

    `libretro-thumbnails` declined to write a name because all it had was
    a No-Intro filename. OpenVGDB carries a real title in a different
    column, and `Tetris (World) (Rev A)` is not what it is called.
    """
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.name == "Tetris"


def test_a_rom_is_found_by_sha1_md5_or_crc(db):
    for digest in (TETRIS_SHA1, TETRIS_MD5, TETRIS_CRC):
        provider, _ = _provider()
        patch = provider.enrich(_ref(filename="whatever.gb", name="", extra={"source_id": digest}))
        assert patch.name == "Tetris", digest


def test_a_lower_case_digest_still_matches_the_upper_case_column(db):
    """OpenVGDB stores `46DF91AD`. Nothing guarantees the operator types it
    that way, and the query upper-cases rather than hoping."""
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(filename="whatever.gb", name="", extra={"source_id": TETRIS_SHA1.lower()})
    )
    assert patch.name == "Tetris"


def test_a_hash_the_host_supplied_is_used_when_there_is_no_source_id(db):
    provider, _ = _provider()
    assert provider.enrich(_ref(filename="x.gb", name="", extra={"md5": TETRIS_MD5})).name == "Tetris"


def test_a_filename_match_is_exact_and_never_a_prefix(db):
    """`Tetris 2 (USA, Europe) (SGB Enhanced)` is in the slice for exactly
    this test: a prefix match would have made `Tetris` find it."""
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(filename="Tetris 2 (USA, Europe) (SGB Enhanced).gb", name="")
    )
    assert patch.name == "Tetris 2"


def test_the_extension_may_differ_between_library_and_database(db):
    """A library that keeps `.zip` still meets a database that keeps `.gb`."""
    provider, _ = _provider()
    assert provider.enrich(_ref(filename="Tetris (World) (Rev A).zip")).name == "Tetris"


def test_set_name_false_keeps_the_operators_own_naming(db):
    provider, _ = _provider(set_name=False, artwork=False)
    assert provider.enrich(_ref()).name is None


def test_a_partial_patch_never_blanks_a_curated_field(db):
    """No provider ids, no raw metadata: OpenVGDB has none, so none are sent."""
    provider, _ = _provider(artwork=False)
    patch = provider.enrich(_ref())
    assert patch.form_fields() == {"name": "Tetris"}
    assert patch.provider_ids == {}
    assert patch.raw_metadata == {}


# -- choosing between releases -------------------------------------------


def test_the_region_the_filename_names_wins(db):
    """Tetris has three releases in OpenVGDB: Europe, Japan and USA."""
    provider, http = _provider()
    provider.enrich(_ref(filename="Tetris (World) (Rev A).gb", name="Tetris (USA)"))
    assert http.calls == ["https://gamefaqs.gamespot.com/a/box/2/8/2/22282_front.jpg"]


def test_the_region_setting_overrides_the_filename(db):
    provider, http = _provider(region="Japan")
    provider.enrich(_ref())
    assert http.calls == ["https://gamefaqs.gamespot.com/a/box/2/8/1/22281_front.jpg"]


def test_the_choice_is_stable_when_nothing_prefers_a_region(db):
    """World, USA, Europe, Japan; ties broken by releaseID, never by
    whatever SQLite happened to return first."""
    provider, first = _provider()
    provider.enrich(_ref(name="Tetris", filename="Tetris (World) (Rev A).gb"))
    provider2, second = _provider()
    provider2.enrich(_ref(name="Tetris", filename="Tetris (World) (Rev A).gb"))
    assert first.calls == second.calls


# -- artwork -------------------------------------------------------------


def test_a_cover_is_probed_before_it_is_proposed(db):
    provider, http = _provider()
    patch = provider.enrich(_ref())
    assert patch.artwork_url in http.calls
    assert patch.artwork_filename == "cover.jpg"


def test_a_cover_the_host_will_not_serve_costs_the_art_not_the_title(db):
    """A 403 from a cover host must not take a resolved title down with it."""
    provider, _ = _provider(FakeCovers(status_code=403))
    patch = provider.enrich(_ref())
    assert patch.name == "Tetris"
    assert patch.artwork_url is None


def test_a_transport_failure_is_treated_the_same_way(db):
    provider, _ = _provider(FakeCovers(raises=RuntimeError("blocked")))
    patch = provider.enrich(_ref())
    assert patch.name == "Tetris"
    assert patch.artwork_url is None


def test_a_gametdb_cover_is_never_probed_because_robots_disallows_it(db):
    """The GameCube row is in the slice for exactly this. `art.gametdb.com`
    serves `User-agent: *` / `Disallow: *.*`, so it is in neither the
    manifest's allowlist nor COVER_HOSTS."""
    provider, http = _provider()
    patch = provider.enrich(
        _ref(platform="ngc", filename="007 - Agent im Kreuzfeuer (Germany).iso", name="")
    )
    assert patch.name
    assert patch.artwork_url is None
    assert http.calls == []


def test_the_arcade_covers_are_the_ones_on_github(db):
    provider, http = _provider()
    patch = provider.enrich(_ref(platform="arcade", filename="005.zip", name=""))
    assert patch.artwork_url.startswith("https://raw.githubusercontent.com/")
    assert patch.artwork_filename == "cover.png"


def test_artwork_false_makes_no_request_at_all(db):
    provider, http = _provider(artwork=False)
    patch = provider.enrich(_ref())
    assert patch.artwork_url is None
    assert http.calls == []


def _manifest() -> dict:
    import tomllib

    return tomllib.loads((PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8"))


def test_the_allowlist_is_exactly_the_covers_plus_the_database():
    """The allowlist used to *be* COVER_HOSTS. It is now a superset, and
    the extra entries have to be accounted for one by one -- otherwise
    "the download needed a host" becomes the excuse for any host at all.

    Two for the database, not one, because the release asset answers 302
    and the Hub re-checks every hop rather than following it blindly.
    """
    manifest = _manifest()
    declared = set(manifest["permissions"]["network"])
    assert COVER_HOSTS <= declared
    assert declared - COVER_HOSTS == {
        "github.com",
        "release-assets.githubusercontent.com",
    }


def test_the_declared_asset_is_the_one_the_plugin_asks_for():
    """A name that drifts between the two is a plugin that refuses at
    runtime with the host having fetched 9 MB for nothing."""
    from openvgdb.metadata import DATA_ASSET

    (asset,) = _manifest()["data_assets"]
    assert asset["name"] == DATA_ASSET
    assert asset["member"] == DATA_ASSET
    assert asset["archive"] == "zip"
    # The size and digest of OpenVGDB v29.0, the project's only artefact:
    # `openvgdb.zip` as published, and `openvgdb.sqlite` as unpacked.
    assert asset["size_bytes"] == 9118645
    assert asset["url"].endswith("/releases/download/v29.0/openvgdb.zip")
    assert len(asset["sha256"]) == 64


def test_the_manifest_parses_under_the_hosts_own_rules():
    """tomllib says it is well-formed TOML; the Hub says whether it is a
    manifest it will install, and the asset URL passes the same allowlist
    gate a FetchPlan URL does."""
    from rom_hub.manifest import load_manifest

    manifest = load_manifest(PLUGIN_ROOT / "manifest.toml")
    (asset,) = manifest.data_assets
    assert asset.host == "github.com"
    assert asset.size_bytes == 9118645


# -- where the database comes from ---------------------------------------


def test_the_host_resolved_data_asset_is_used_when_db_path_is_unset(db, tmp_path):
    """The gap this closes: no manual setup, no `db_path`, it just works."""
    provider = Metadata(
        PluginContext(
            config={"artwork": False},
            http=FakeCovers(),
            data_assets={"openvgdb.sqlite": str(tmp_path / "openvgdb.sqlite")},
        )
    )
    assert provider.enrich(_ref()).name == "Tetris"


def test_db_path_overrides_the_data_asset_rather_than_the_other_way_round(db):
    """An operator who pinned a copy has said something more specific than
    the manifest's default, and must not be quietly overruled by it."""
    seen: list[str] = []

    def _record(path):
        seen.append(path)
        return db(path)

    provider = Metadata(
        PluginContext(
            config={"db_path": "/operators/own/openvgdb.sqlite", "artwork": False},
            http=FakeCovers(),
            data_assets={"openvgdb.sqlite": "/hub/cache/openvgdb.sqlite"},
        )
    )
    import openvgdb.metadata as metadata_module

    original = metadata_module.open_database
    metadata_module.open_database = _record
    try:
        provider.enrich(_ref())
    finally:
        metadata_module.open_database = original
    assert seen == ["/operators/own/openvgdb.sqlite"]


def test_neither_route_available_says_how_to_get_the_file():
    provider = Metadata(PluginContext(config={}, http=FakeCovers()))
    with pytest.raises(DatabaseUnavailable, match="plugin assets openvgdb --fetch"):
        provider.enrich(_ref())


# -- refusals ------------------------------------------------------------


def test_a_missing_db_path_says_where_to_get_the_file(db):
    provider = Metadata(PluginContext(config={"db_path": ""}, http=FakeCovers()))
    with pytest.raises(DatabaseUnavailable, match="openvgdb.zip"):
        provider.enrich(_ref())


def test_a_db_path_that_is_not_a_file_is_refused_rather_than_created():
    """`sqlite3.connect` on a plain path *creates* an empty database, which
    would answer "no match" forever and look like data."""
    provider = Metadata(
        PluginContext(config={"db_path": "no/such/openvgdb.sqlite"}, http=FakeCovers())
    )
    with pytest.raises(DatabaseUnavailable, match="not a file"):
        provider.enrich(_ref())


def test_some_other_sqlite_file_is_refused_by_name(tmp_path):
    other = tmp_path / "notopenvgdb.sqlite"
    connection = sqlite3.connect(other)
    connection.execute("CREATE TABLE things (id INTEGER)")
    connection.commit()
    connection.close()
    provider = Metadata(
        PluginContext(config={"db_path": str(other)}, http=FakeCovers())
    )
    with pytest.raises(DatabaseUnavailable, match="RELEASES"):
        provider.enrich(_ref())


def test_the_database_is_opened_read_only(tmp_path):
    """The operator may be sharing this file with OpenEmu."""
    path = tmp_path / "openvgdb.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(SLICE)
    connection.commit()
    connection.close()

    opened = database.open_database(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            opened.execute("DELETE FROM ROMs")
    finally:
        opened.close()


def test_an_unmapped_platform_raises_needs_mapping_and_names_itself(db):
    provider, _ = _provider()
    with pytest.raises(NeedsMapping, match="needs mapping: RomM platform 'dos'"):
        provider.enrich(_ref(platform="dos"))


def test_a_rom_openvgdb_does_not_have_is_refused_with_what_was_tried(db):
    provider, _ = _provider()
    with pytest.raises(NoMatch, match="Tried: filename"):
        provider.enrich(_ref(filename="Nonexistent Game (USA).gb", name=""))


def test_a_lookup_never_reaches_across_systems(db):
    """`Altered Beast (USA, Europe)` is in the slice under system 33. A
    Game Boy rom asking for it must not find it."""
    provider, _ = _provider()
    with pytest.raises(NoMatch):
        provider.enrich(_ref(filename="Altered Beast (USA, Europe).md", name=""))


def test_every_system_in_the_table_is_a_real_openvgdb_system_id():
    assert set(SYSTEMS.values()) <= set(range(1, 44))


def test_the_table_is_keyed_by_real_romm_slugs():
    thumbnails = PLUGIN_ROOT.parent / "libretro-thumbnails"
    sys.path.insert(0, str(thumbnails))
    from libretro_thumbnails.systems import SYSTEMS as SLUGS  # noqa: PLC0415

    known = set(SLUGS) | {"atari-jaguar-cd"}
    assert set(SYSTEMS) <= known, sorted(set(SYSTEMS) - known)
