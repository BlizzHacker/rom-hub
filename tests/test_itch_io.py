"""itch.io plugin, replayed against fixtures captured from the live site.

Nothing here opens a socket. `tests/fixtures/itch_io/` holds verbatim
slices of real responses -- one browse listing (`?format=json`) and four
game pages, one per routing outcome the importer has to tell apart.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "itch-io"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "itch_io"
sys.path.insert(0, str(PLUGIN_ROOT))

from itch_io.browse import BrowseError, browse_url, parse_cells  # noqa: E402
from itch_io.filenames import safe_filename  # noqa: E402
from itch_io.importer import ImportRefused, Importer, parse_size  # noqa: E402
from itch_io.platforms import platform_for  # noqa: E402
from itch_io.search import Search  # noqa: E402

from romm_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from romm_hub.types import FetchFile, SearchResult  # noqa: E402


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


BROWSE = json.loads(fixture("browse_free_gameboy.json"))


EMPTY_PAGE = json.dumps({"page": 2, "num_items": 0, "content": ""})


class FakeHttp:
    """Answers a fixed body, and an empty listing past page 1.

    itch.io runs out of games; a fake that served the same 36 cells for
    every page would let a bug in the walk look like a working one.
    """

    def __init__(self, body, status=200, paginate=True):
        self.body = body
        self.status = status
        self.paginate = paginate
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if self.paginate and (params or {}).get("page", 1) != 1:
            return HttpResponse(status_code=200, text=EMPTY_PAGE)
        return HttpResponse(status_code=self.status, text=self.body)


def make_search(body=None, config=None):
    http = FakeHttp(body if body is not None else json.dumps(BROWSE))
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(page: str, status: int = 200):
    http = FakeHttp(page, status)
    return Importer(PluginContext(config={}, http=http)), http


# --------------------------------------------------------------- browse URLs


def test_browse_url_is_always_scoped_to_free_games():
    assert browse_url([]) == "https://itch.io/games/free"
    assert browse_url(["tag-gameboy"]) == "https://itch.io/games/free/tag-gameboy"


def test_browse_filters_that_could_address_another_endpoint_are_refused():
    # /search is disallowed by itch.io's robots.txt; a filter is not a place
    # to smuggle a path into.
    for bad in ["../search", "tag-x/../../search", "TAG-Gameboy", "a b"]:
        with pytest.raises(BrowseError):
            browse_url([bad])


# ------------------------------------------------------------- result parsing


def test_every_cell_in_the_listing_becomes_a_result():
    search, _ = make_search()
    results = search.search("", None, 25)
    assert len(results) == 6
    assert results[0].title == "FUN VIDEO STORE"
    assert results[0].source_id == "elrosso/fun-video-store"
    assert results[0].url == "https://elrosso.itch.io/fun-video-store"


def test_author_and_game_id_are_carried_in_extra():
    search, _ = make_search()
    first = search.search("", None, 25)[0]
    assert first.extra["itch_game_id"].isdigit()
    assert first.extra["author"]


def test_query_terms_must_all_match():
    search, _ = make_search()
    assert [r.source_id for r in search.search("video store", None, 25)] == [
        "elrosso/fun-video-store"
    ]
    assert search.search("video nonexistentword", None, 25) == []


def test_matching_is_case_insensitive_and_reaches_the_blurb():
    search, _ = make_search()
    assert search.search("GAME BOY COLOR", None, 25)


def test_limit_stops_the_walk_after_one_page():
    search, http = make_search()
    assert len(search.search("", None, 3)) == 3
    assert len(http.calls) == 1


def test_a_single_platform_is_reported_and_several_are_not():
    search, _ = make_search()
    by_id = {r.source_id: r for r in search.search("", None, 25)}
    browser_only = by_id["elrosso/fun-video-store"]
    assert browser_only.platform == "browser"
    multi = by_id["tuyoki/dwellers-empty-path"]
    assert multi.platform is None
    assert set(multi.extra["platforms"].split(",")) == {"win", "mac"}


def test_platform_filter_keeps_only_matching_cells():
    search, _ = make_search()
    results = search.search("", "mac", 25)
    assert results
    assert all("mac" in r.extra["platforms"].split(",") for r in results)


def test_an_empty_listing_ends_the_walk_without_error():
    search, http = make_search(json.dumps({"page": 1, "num_items": 0, "content": ""}))
    assert search.search("anything", None, 25) == []
    assert len(http.calls) == 1


def test_a_non_json_first_page_is_a_hard_failure():
    # itch.io answers some URL shapes with a Cloudflare challenge and a 200.
    search, _ = make_search("<!DOCTYPE html><title>Just a moment...</title>")
    with pytest.raises(BrowseError):
        search.search("x", None, 5)


def test_a_cell_on_a_developers_own_domain_is_skipped():
    # The manifest allows itch.io and *.itch.io only, so a result the plugin
    # could never fetch is worse than no result.
    payload = json.loads(json.dumps(BROWSE))
    payload["content"] = payload["content"].replace(
        "https://elrosso.itch.io/fun-video-store", "https://elrosso.example.com/fvs"
    )
    search, _ = make_search(json.dumps(payload))
    assert all(r.source_id != "elrosso/fun-video-store" for r in search.search("", None, 25))


def test_walk_stops_at_max_pages():
    # Every page answers the same fixture here, so the walk only ends
    # because max_pages says so.
    http = FakeHttp(json.dumps(BROWSE), paginate=False)
    search = Search(PluginContext(config={"max_pages": 2}, http=http))
    search.search("nothing-matches-this-query", None, 25)
    assert [p["page"] for _, p in http.calls] == [1, 2]


def test_parse_cells_survives_a_malformed_chunk():
    fragment = '<div data-game_id="1">garbage</div>' + BROWSE["content"]
    assert len(parse_cells(fragment)) == 6


# ---------------------------------------------------------- platform mapping


@pytest.mark.parametrize(
    "label,slug",
    [
        ("Download for Windows", "win"),
        ("Download for macOS", "mac"),
        ("Download for Linux", "linux"),
        ("Download for Android", "android"),
        ("Web", "browser"),
        ("os x", "mac"),
    ],
)
def test_known_platform_labels_map(label, slug):
    assert platform_for(label) == slug


def test_an_unknown_platform_label_maps_to_nothing():
    assert platform_for("Download for Dreamcast") is None
    assert platform_for("") is None


def test_an_unmapped_platform_refuses_the_import_and_names_the_value():
    page = fixture("game_free_windows.html").replace(
        "Download for Windows", "Download for Steam Deck"
    )
    importer, _ = make_importer(page)
    with pytest.raises(ImportRefused, match="Steam Deck"):
        importer.plan(SearchResult(source_id="dark-knife15/your-happy-place", title="x"))


# ------------------------------------------------------------ importer routing


def test_a_name_your_own_price_game_is_refused_as_a_checkout():
    importer, _ = make_importer(fixture("game_name_your_own_price.html"))
    with pytest.raises(ImportRefused, match="checkout"):
        importer.plan(SearchResult(source_id="tuyoki/dwellers-empty-path", title="x"))


def test_the_checkout_refusal_repeats_what_itch_called_the_gate():
    importer, _ = make_importer(fixture("game_name_your_own_price.html"))
    with pytest.raises(ImportRefused, match="Name your own price"):
        importer.plan(SearchResult(source_id="tuyoki/dwellers-empty-path", title="x"))


def test_files_listed_with_no_button_and_no_checkout_read_as_key_gated():
    page = fixture("game_name_your_own_price.html").replace("buy_btn", "other_btn")
    importer, _ = make_importer(page)
    with pytest.raises(ImportRefused, match="download key"):
        importer.plan(SearchResult(source_id="tuyoki/dwellers-empty-path", title="x"))


def test_a_browser_only_game_is_refused_with_nothing_to_import():
    importer, _ = make_importer(fixture("game_browser_only.html"))
    with pytest.raises(ImportRefused, match="no downloadable files"):
        importer.plan(SearchResult(source_id="13-23/petal", title="x"))


def test_a_free_download_is_routed_all_the_way_and_then_refused_at_the_post_wall():
    importer, _ = make_importer(fixture("game_free_windows.html"))
    with pytest.raises(ImportRefused) as exc:
        importer.plan(SearchResult(source_id="dark-knife15/your-happy-place", title="x"))
    message = str(exc.value)
    # Everything it worked out is in the refusal: file, platform, and why.
    assert "YourHappyPlace (Windows)" in message
    assert "'win'" in message
    assert "csrf_token" in message


def test_the_largest_upload_wins_and_a_soundtrack_does_not():
    # The Disco Elysium page lists two .gb builds and three OST archives;
    # the 26 MB wav OST is the largest, so this also pins the rule rather
    # than the outcome anyone would prefer.
    importer, _ = make_importer(fixture("game_free_direct.html"))
    with pytest.raises(ImportRefused) as exc:
        importer.plan(
            SearchResult(
                source_id="csbrannan/disco-elysium-game-boy-edition",
                title="x",
                platform="gb",
            )
        )
    assert "DEGB-OST(wav).zip" in str(exc.value)


def test_a_free_download_with_no_platform_icons_asks_for_platform():
    importer, _ = make_importer(fixture("game_free_direct.html"))
    with pytest.raises(ImportRefused, match="--platform"):
        importer.plan(
            SearchResult(source_id="csbrannan/disco-elysium-game-boy-edition", title="x")
        )


def test_a_multi_platform_upload_refuses_rather_than_choosing():
    page = fixture("game_free_windows.html").replace(
        '<span class="download_platforms">',
        '<span class="download_platforms">'
        '<span aria-hidden="true" title="Download for Linux" class="icon icon-tux"></span>',
    )
    importer, _ = make_importer(page)
    with pytest.raises(ImportRefused, match="will not make it for you|will not make"):
        importer.plan(SearchResult(source_id="dark-knife15/your-happy-place", title="x"))


def test_a_source_id_that_is_not_a_game_path_is_refused_before_any_request():
    importer, http = make_importer("")
    with pytest.raises(ImportRefused, match="not an itch.io game id"):
        importer.plan(SearchResult(source_id="../../etc/passwd", title="x"))
    assert http.calls == []


def test_a_non_200_game_page_is_refused_with_the_status():
    importer, _ = make_importer("", status=404)
    with pytest.raises(ImportRefused, match="404"):
        importer.plan(SearchResult(source_id="nobody/nothing", title="x"))


def test_the_importer_asks_for_the_developers_subdomain():
    importer, http = make_importer(fixture("game_browser_only.html"))
    with pytest.raises(ImportRefused):
        importer.plan(SearchResult(source_id="13-23/petal", title="x"))
    assert http.calls[0][0] == "https://13-23.itch.io/petal"


@pytest.mark.parametrize(
    "raw,expected",
    [("137 MB", 137_000_000), ("38 kB", 38_000), ("1,024 GB", 1_024_000_000_000)],
)
def test_itch_sizes_are_decimal(raw, expected):
    assert parse_size(raw) == expected


def test_an_unparseable_size_is_none_rather_than_an_error():
    assert parse_size("a few blocks") is None
    assert parse_size("") is None


# ------------------------------------------------------- filename sanitising


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Disco_Elysium-GBEdition (Music).gb", "Disco_Elysium-GBEdition (Music).gb"),
        ("sub/dir/game.gb", "game.gb"),
        (r"sub\dir\game.gb", "game.gb"),
        ("C:evil.zip", "C_evil.zip"),
        ("..", "download.bin"),
        ("...   ", "download.bin"),
        ("NUL.gb", "_NUL.gb"),
        ("com1.zip", "_com1.zip"),
        ("game.zip.", "game.zip"),
        ("game .zip ", "game .zip"),
        ("Pokémon Mini.gb", "Pokémon Mini.gb"),
        ("a:b*c?d.gb", "a_b_c_d.gb"),
    ],
)
def test_sanitised_names_are_what_the_host_accepts(raw, expected):
    name = safe_filename(raw)
    assert name == expected
    # The real contract: whatever comes out, FetchFile must take it.
    FetchFile(url="https://itch.io/x", filename=name)


def test_a_very_long_name_is_truncated_but_keeps_its_extension():
    name = safe_filename("x" * 400 + ".gb")
    assert len(name) <= 200
    assert name.endswith(".gb")
    FetchFile(url="https://itch.io/x", filename=name)


def test_sanitising_is_deterministic():
    raw = "Wild/Name: *bad* (v1.0).gb"
    assert safe_filename(raw) == safe_filename(raw)
