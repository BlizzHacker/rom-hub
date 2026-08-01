"""libretro content plugin, replayed against captured buildbot listings.

`tests/fixtures/libretro_content/` holds five verbatim h5ai pages from
`buildbot.libretro.com/assets/cores/`: the root, three system directories
and `Utilities/`. They are there for what they really contain -- a
directory whose name has an apostrophe, a Mega Drive ROM with a `.md`
extension, a NES entry that is a `.zip`, and a directory holding no games
at all -- none of which are invented edge cases.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-content"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro_content"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_content.buildbot import (  # noqa: E402
    LISTINGS,
    BuildbotError,
    directory_url,
    file_url,
    parse_listing,
)
from libretro_content.importer import ImportRefused, Importer  # noqa: E402
from libretro_content.platforms import (  # noqa: E402
    AMBIGUOUS,
    NOT_GAMES,
    SYSTEMS,
    directory_for,
    platform_for,
    why_unmapped,
)
from libretro_content.search import Search  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import SearchResult  # noqa: E402


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


ROOT = fixture("cores_root.html")
NES = fixture("nes.html")
VECTREX = fixture("vectrex.html")
GENESIS = fixture("genesis.html")
UTILITIES = fixture("utilities.html")

NES_DIR = "Nintendo - Nintendo Entertainment System"
VECTREX_DIR = "GCE - Vectrex"
GENESIS_DIR = "Sega - Mega Drive - Genesis"


class FakeHttp:
    """Answers per directory, so a walk that fetches the wrong one shows."""

    def __init__(self, pages: dict, status: int = 200):
        self.pages = pages
        self.status = status
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        for directory, body in self.pages.items():
            if directory_url(directory) == url:
                return HttpResponse(status_code=self.status, text=body)
        return HttpResponse(status_code=404, text="404 - Not Found")


DEFAULT_PAGES = {
    NES_DIR: NES,
    VECTREX_DIR: VECTREX,
    GENESIS_DIR: GENESIS,
    "Utilities": UTILITIES,
}


@pytest.fixture(autouse=True)
def _no_cached_listings():
    """Listings are cached for the life of the plugin process.

    That is the point of it -- `search` then `importer` should not read
    one directory twice -- and it is also process-global state, so a test
    that stocked a different body would otherwise be answered by whatever
    the previous test read.
    """
    LISTINGS.clear()
    yield
    LISTINGS.clear()


def make_search(pages=None, config=None, status=200):
    http = FakeHttp(DEFAULT_PAGES if pages is None else pages, status)
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(pages=None, config=None, status=200):
    http = FakeHttp(DEFAULT_PAGES if pages is None else pages, status)
    return Importer(PluginContext(config=config or {}, http=http)), http


# ------------------------------------------------------------ listing parsing


def test_a_listing_yields_files_and_directories():
    items = parse_listing(ROOT)
    assert items, "the root listing should not be empty"
    assert all(item.is_dir for item in items), "the root holds only directories"
    names = {item.name for item in items}
    assert NES_DIR in names
    # An apostrophe survives, and the `..` row never appears as an entry.
    assert "Jump 'n Bump" in names
    assert "Parent Directory" not in names


def test_files_are_distinguished_from_directories_by_the_icon_not_the_suffix():
    """`Break An Egg.md` is a ROM and `Quake II` is a directory."""
    genesis = {item.name: item for item in parse_listing(GENESIS)}
    assert genesis["Break An Egg.md"].is_dir is False
    root = {item.name: item for item in parse_listing(ROOT)}
    assert root["Quake II"].is_dir is True


def test_the_size_is_carried_as_text_because_h5ai_rounds_it():
    nes = {item.name: item for item in parse_listing(NES)}
    assert nes["Alter Ego.nes"].size_text == "40 KB"


def test_a_document_that_is_not_a_listing_raises():
    """The nointro-archive lesson: a 200 is not evidence of a listing."""
    with pytest.raises(BuildbotError):
        parse_listing("<html><body>We'll be back shortly.</body></html>")
    with pytest.raises(BuildbotError):
        parse_listing("")


# ------------------------------------------------------------------ platforms


def test_the_directory_table_is_an_exact_inversion():
    for directory, slug in SYSTEMS.items():
        assert directory_for(slug) == directory
        assert platform_for(directory) == slug


def test_the_table_uses_the_spellings_this_server_actually_serves():
    """Copying the thumbnail server's names would 404 on two of them."""
    served = {item.name for item in parse_listing(ROOT) if item.is_dir}
    unknown = sorted(set(SYSTEMS) - served)
    assert not unknown, f"mapped directories the buildbot does not serve: {unknown}"
    # The two that differ from thumbnails.libretro.com's spelling.
    assert "Coleco - Colecovision" in SYSTEMS
    assert "Nintendo - GameBoy" in SYSTEMS


def test_an_unmapped_directory_gets_the_reason_that_fits_it():
    needs = why_unmapped("Some New Console")
    assert "needs mapping" in needs
    assert "libretro_content/platforms.py" in needs

    not_a_game = why_unmapped("Utilities")
    assert "holds no games" in not_a_game
    assert "needs mapping" not in not_a_game

    ambiguous = why_unmapped("Nintendo - GameCube - Wii")
    assert "ngc" in ambiguous and "wii" in ambiguous
    assert "--platform" in ambiguous


def test_the_ambiguous_and_non_game_directories_are_never_also_mapped():
    for directory in list(AMBIGUOUS) + list(NOT_GAMES):
        assert platform_for(directory) is None


# --------------------------------------------------------------------- search


def test_platform_scoped_search_costs_exactly_one_request():
    search, http = make_search()
    results = search.search("alter ego", "nes", 25)
    assert len(http.calls) == 1
    assert http.calls[0] == directory_url(NES_DIR)
    assert [r.title for r in results] == ["Alter Ego.nes"]
    assert results[0].source_id == f"{NES_DIR}/Alter Ego.nes"
    assert results[0].platform == "nes"
    assert results[0].extra["system"] == NES_DIR


def test_a_platform_this_source_has_nothing_for_costs_no_request():
    search, http = make_search()
    assert search.search("anything", "jaguar", 25) == []
    assert http.calls == []


def test_every_term_must_appear_but_order_does_not_matter():
    search, _ = make_search()
    forward = search.search("alter ego", "nes", 25)
    backward = search.search("ego alter", "nes", 25)
    assert [r.source_id for r in forward] == [r.source_id for r in backward]
    assert search.search("alter zzz", "nes", 25) == []


def test_an_empty_query_browses_the_directory():
    search, _ = make_search()
    results = search.search("", "vectrex", 100)
    assert len(results) > 10
    assert all(r.platform == "vectrex" for r in results)


def test_the_walk_is_bounded_when_no_platform_is_given():
    search, http = make_search(
        config={"systems": ["nes", "genesis", "vectrex"], "max_systems": 2}
    )
    search.search("zzzzz", None, 25)
    assert len(http.calls) == 2


def test_configured_systems_this_source_has_nothing_for_are_dropped_not_fatal():
    search, http = make_search(config={"systems": ["jaguar", "nes"]})
    results = search.search("alter", None, 25)
    assert len(http.calls) == 1
    assert results and results[0].platform == "nes"


def test_limit_stops_the_walk():
    search, _ = make_search(config={"systems": ["vectrex"]})
    assert len(search.search("", None, 3)) == 3


def test_a_non_200_listing_raises_rather_than_returning_nothing():
    search, _ = make_search(status=500)
    with pytest.raises(BuildbotError):
        search.search("alter", "nes", 25)


# ------------------------------------------------------------------- importer


def test_a_plan_names_the_exact_file_and_a_bare_filename():
    importer, http = make_importer()
    plan = importer.plan(
        SearchResult(source_id=f"{NES_DIR}/Alter Ego.nes", title="Alter Ego.nes")
    )
    assert plan.platform == "nes"
    assert plan.collection == "libretro content"
    assert plan.files[0].url == file_url(NES_DIR, "Alter Ego.nes")
    assert plan.files[0].filename == "Alter Ego.nes"
    # No size is claimed, because h5ai rounds and the plugin does not know.
    assert plan.files[0].size_bytes is None
    assert http.calls == [directory_url(NES_DIR)]


def test_the_listing_is_re_read_and_a_near_miss_refuses():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(
            SearchResult(source_id=f"{NES_DIR}/Alter Ego.zip", title="x")
        )
    assert "no file named" in str(excinfo.value)


def test_a_directory_is_never_planned_as_a_file():
    importer, _ = make_importer(pages={"": ROOT} | DEFAULT_PAGES)
    with pytest.raises(ImportRefused):
        importer.plan(SearchResult(source_id=f"{NES_DIR}/Quake II", title="x"))


def test_an_unmapped_directory_refuses_by_name():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="Utilities/anything.zip", title="x"))
    assert "holds no games" in str(excinfo.value)

    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(
            SearchResult(source_id="Nintendo - GameCube - Wii/x.zip", title="x")
        )
    assert "ngc" in str(excinfo.value)


def test_an_operator_platform_overrides_the_directory():
    """The documented way past the GameCube/Wii ambiguity."""
    importer, _ = make_importer(
        pages={"Nintendo - GameCube - Wii": NES} | DEFAULT_PAGES
    )
    plan = importer.plan(
        SearchResult(
            source_id="Nintendo - GameCube - Wii/Alter Ego.nes",
            title="x",
            platform="wii",
        )
    )
    assert plan.platform == "wii"


def test_a_malformed_source_id_refuses_with_an_example():
    """`SearchResult` already refuses an empty id, so these are the rest."""
    importer, http = make_importer()
    for bad in ("   ", "AlterEgo.nes", f"{NES_DIR}/", "/Alter Ego.nes"):
        with pytest.raises(ImportRefused) as excinfo:
            importer.plan(SearchResult(source_id=bad, title="x"))
        assert "system directory" in str(excinfo.value)
    assert http.calls == [], "a malformed id must not cost a request"


def test_the_directory_is_split_on_the_last_separator():
    """`Sega - Mega Drive - Genesis` carries plenty of punctuation."""
    importer, http = make_importer()
    plan = importer.plan(
        SearchResult(source_id=f"{GENESIS_DIR}/Break An Egg.md", title="x")
    )
    assert plan.platform == "genesis"
    assert http.calls == [directory_url(GENESIS_DIR)]


def test_a_listing_that_is_a_maintenance_page_refuses_rather_than_404ing():
    importer, _ = make_importer(pages={NES_DIR: "<html>back soon</html>"})
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id=f"{NES_DIR}/Alter Ego.nes", title="x"))
    assert "not a directory listing" in str(excinfo.value)


def test_urls_are_quoted_once_and_never_carry_a_raw_space():
    url = file_url(VECTREX_DIR, "Berzerk (World).zip")
    assert url.startswith("https://buildbot.libretro.com/assets/cores/")
    assert " " not in url
    assert "%2520" not in url


# ------------------------------------------------ the widened default walk


def test_the_default_walk_is_every_directory_this_plugin_can_map():
    """It was eight of 29, which reached 104 of the source's 274 files.

    The old reason for the bound was that 29 listings "does not reliably
    finish" inside the host's 30-second ceiling. Timed directory by
    directory on 2026-08-01 it is 131 KB and 12.8 seconds, so the bound
    was costing two-thirds of a small source for nothing.
    """
    from libretro_content.search import DEFAULT_MAX_SYSTEMS, DEFAULT_SYSTEMS

    # `systems` and `DEFAULT_SYSTEMS` carry RomM slugs, not directory
    # names: a slug is what an operator already types at --platform.
    assert set(DEFAULT_SYSTEMS) == set(SYSTEMS.values())
    assert DEFAULT_MAX_SYSTEMS == len(SYSTEMS) == 29


def test_the_biggest_shelves_are_walked_first():
    """A small `--limit` should be answered from where the content is."""
    from libretro_content.search import DEFAULT_SYSTEMS

    assert DEFAULT_SYSTEMS[:3] == (
        "wasm-4",
        "handheld-electronic-lcd",
        "vectrex",
    )


def test_a_directory_is_read_once_across_search_and_import():
    """The runner loads both capabilities into one interpreter, so an
    import that follows a search should cost no request."""
    http = FakeHttp({NES_DIR: NES}, 200)
    ctx = PluginContext(config={"systems": ["nes"]}, http=http)
    Search(ctx).search("alter", None, 5)
    plan = Importer(ctx).plan(
        SearchResult(source_id=f"{NES_DIR}/Alter Ego.nes", title="x")
    )
    assert plan.files[0].filename == "Alter Ego.nes"
    assert len(http.calls) == 1


def test_the_cap_is_the_table_because_there_is_no_thirtieth_directory():
    from libretro_content.search import MAX_SYSTEMS_CAP

    assert MAX_SYSTEMS_CAP == len(SYSTEMS)
