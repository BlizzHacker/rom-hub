"""itch.io plugin, replayed against fixtures captured from the live site.

Nothing here opens a socket. `tests/fixtures/itch_io/` holds verbatim
slices of real responses: one browse listing (`?format=json`), four game
pages trimmed to what the importer routes on, and three trimmed to
`<head>` plus the title heading for what `metadata` reads.

Two more were captured for `stream` on 2026-08-01, each trimmed to its
`<head>`, its `game_title` heading and the block around the embed:

* `page_web_playable.html` -- `13-23.itch.io/petal`, which renders a
  browser build. `html_embed_widget` present once, wrapping a
  `game_frame` with `data-width`/`data-height`;
* `page_download_only.html` -- `redspringstudio.itch.io/touchstarved`,
  which does not. Same page shape, no widget anywhere in it.

That pair is the whole gate: the marker is itch.io's own, and it is
present on exactly the pages with something to play.
"""

import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "itch-io"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "itch_io"
sys.path.insert(0, str(PLUGIN_ROOT))

from itch_io.browse import BrowseError, browse_url, parse_cells  # noqa: E402
from itch_io.filenames import safe_filename  # noqa: E402
from itch_io.importer import ImportRefused, Importer, parse_size  # noqa: E402
from itch_io.platforms import BROWSE_FACETS, facet_for, platform_for  # noqa: E402
from itch_io.metadata import (  # noqa: E402
    Metadata,
    NotIdentified,
    NothingToPropose,
    PageUnusable,
    cover_url,
    heading_title,
    product_name,
)
from itch_io.search import DEFAULT_MAX_PAGES, PAGE_CAP, Search  # noqa: E402
from itch_io.stream import (  # noqa: E402
    Stream,
    StreamRefused,
    has_web_build,
)
from itch_io.stream import NotIdentified as StreamNotIdentified  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import (  # noqa: E402
    FetchFile,
    RomRef,
    SearchResult,
    bare_filename,
)


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


def make_stream(page: str, status: int = 200):
    http = FakeHttp(page, status, paginate=False)
    return Stream(PluginContext(config={}, http=http)), http


WEB_PLAYABLE = fixture("page_web_playable.html")
DOWNLOAD_ONLY = fixture("page_download_only.html")


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
    # The *reason* has to be in the message, not just in the README. A user
    # who reads "refused" without "because itch.io offers no public download
    # route" files this as a bug against the plugin. Both halves of the
    # reason are pinned: the POST/csrf wall and the robots.txt disallow.
    assert "csrf_token" in message
    assert "GET only" in message
    assert "robots.txt" in message
    assert "/game/download/" in message


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


# ------------------------------------------------------------------ metadata


def page_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# Real game pages, trimmed to `<head>` plus the `<h1 class="game_title">`
# block -- verbatim excerpts, captured 2026-07-29. The three between them
# cover what varies: FUN VIDEO STORE's Product JSON-LD leads with `name`,
# Disco Elysium's leads with `aggregateRating` (which is why the block is
# parsed as JSON rather than pattern-matched), and Opossum Country's cover
# is a `.gif` where the others are `.png`.
DISCO = page_fixture("page_disco_elysium_gb.html")
FUN_VIDEO_STORE = page_fixture("page_fun_video_store.html")
DEADEUS = page_fixture("page_deadeus.html")


class PageHttp:
    """Serves one page body, and records the URLs it was asked for."""

    def __init__(self, body=DISCO, status=200):
        self.body = body
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        return HttpResponse(status_code=self.status, text=self.body)


def make_metadata(body=DISCO, config=None, status=200):
    http = PageHttp(body, status)
    return Metadata(PluginContext(config=config or {}, http=http)), http


def a_rom(**kwargs):
    base = {"rom_id": 7, "name": "", "filename": "", "platform": None, "extra": {}}
    return RomRef(**{**base, **kwargs})


def test_a_game_page_yields_the_developers_title_and_cover():
    meta, http = make_metadata()
    patch = meta.enrich(
        a_rom(extra={"source_id": "csbrannan/disco-elysium-game-boy-edition"})
    )
    assert patch.name == "Disco Elysium: Game Boy Edition"
    assert patch.artwork_url == (
        "https://img.itch.zone/aW1nLzQ0MTgzNTcucG5n/original/7GT5BM.png"
    )
    assert patch.artwork_filename == "7GT5BM.png"
    assert http.calls == [
        "https://csbrannan.itch.io/disco-elysium-game-boy-edition"
    ]


def test_the_json_ld_is_parsed_not_pattern_matched():
    """itch.io emits the Product object's keys in different orders on
    different pages. A regex for `"name":"..."` works against whichever
    page it was written for and silently fails on the rest."""
    assert product_name(DISCO) == "Disco Elysium: Game Boy Edition"
    assert product_name(FUN_VIDEO_STORE) == "FUN VIDEO STORE"

    def product_block(page):
        blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            page,
            re.DOTALL,
        )
        return next(b for b in blocks if '"Product"' in b)

    # Same object, three different key orders across three real pages.
    # This is the whole reason the block is parsed rather than matched.
    orders = [
        re.findall(r'"(@?\w+)":', product_block(page))[0]
        for page in (DISCO, FUN_VIDEO_STORE, DEADEUS)
    ]
    assert orders == ["name", "aggregateRating", "@type"]

    # And the naive whole-page version -- the first `"name":"..."` anywhere
    # in the document -- lands in the breadcrumb block on every one of them.
    for page in (DISCO, FUN_VIDEO_STORE, DEADEUS):
        assert re.search(r'"name":"([^"]*)"', page).group(1) == "Games"


def test_the_breadcrumb_json_ld_is_not_mistaken_for_the_product():
    """Every game page carries a BreadcrumbList block too, and it comes
    first. Taking the first block would name the game "Games"."""
    assert '"BreadcrumbList"' in DISCO
    assert product_name(DISCO) != "Games"


def test_the_heading_is_the_fallback_when_there_is_no_product_block():
    page = DISCO.replace("application/ld+json", "application/x-nothing")
    assert product_name(page) == ""
    assert heading_title(page) == "Disco Elysium: Game Boy Edition"
    meta, _ = make_metadata(page)
    patch = meta.enrich(a_rom(extra={"source_id": "csbrannan/disco-elysium"}))
    assert patch.name == "Disco Elysium: Game Boy Edition"


def test_a_percent_encoded_cover_url_yields_a_bare_filename():
    """Live URL: `/original/Z66%2BLw.png`. RomM routes on the extension, so
    it is preserved."""
    meta, _ = make_metadata(FUN_VIDEO_STORE)
    patch = meta.enrich(a_rom(extra={"source_id": "elrosso/fun-video-store"}))
    assert "%2B" in patch.artwork_url
    assert patch.artwork_filename == "Z66+Lw.png"
    bare_filename(patch.artwork_filename)


def test_every_captured_page_produces_a_cover_on_the_declared_host():
    for page in (DISCO, FUN_VIDEO_STORE, DEADEUS):
        url = cover_url(page)
        assert url.startswith("https://img.itch.zone/")


def test_a_cover_off_the_image_host_is_dropped_rather_than_proposed():
    """The broker would refuse it at enrich time as a policy violation,
    which reads as a Hub fault. A patch with a name and no cover is a true
    and useful answer instead."""
    page = DISCO.replace("https://img.itch.zone/", "https://evil.example/")
    assert cover_url(page) == ""
    meta, _ = make_metadata(page)
    patch = meta.enrich(a_rom(extra={"source_id": "csbrannan/disco"}))
    assert patch.artwork_url is None
    assert patch.name


def test_a_page_with_neither_title_nor_cover_refuses():
    meta, _ = make_metadata("<html><head></head><body>nothing here</body></html>")
    with pytest.raises(NothingToPropose, match="left alone"):
        meta.enrich(a_rom(extra={"source_id": "someone/something"}))


def test_a_rom_with_no_game_id_is_told_how_to_get_one():
    """There is no lookup by name on purpose: /search is disallowed and the
    browse listings are a small slice of the catalogue."""
    meta, http = make_metadata()
    with pytest.raises(NotIdentified, match="--source-id"):
        meta.enrich(a_rom(name="Deadeus"))
    assert http.calls == []


def test_a_game_page_url_is_accepted_as_an_id():
    meta, http = make_metadata()
    meta.enrich(a_rom(extra={"source_id": "https://izma.itch.io/deadeus"}))
    assert http.calls == ["https://izma.itch.io/deadeus"]


@pytest.mark.parametrize(
    "evil",
    [
        "../../etc/passwd",
        "izma.itch.io/deadeus",
        "https://evil.example/x",
        "izma/deadeus/extra",
        "https://izma.itch.io.evil.example/deadeus",
    ],
)
def test_a_source_id_that_is_not_a_game_id_is_refused(evil):
    meta, http = make_metadata()
    with pytest.raises(NotIdentified):
        meta.enrich(a_rom(extra={"source_id": evil}))
    assert http.calls == []


def test_a_non_200_names_the_status():
    meta, _ = make_metadata(status=404)
    with pytest.raises(PageUnusable, match="404"):
        meta.enrich(a_rom(extra={"source_id": "izma/deadeus"}))


def test_the_patch_only_carries_what_resolved():
    meta, _ = make_metadata(DEADEUS)
    patch = meta.enrich(a_rom(extra={"source_id": "izma/deadeus"}))
    assert patch.provider_ids == {}
    assert patch.raw_metadata == {}
    assert set(patch.form_fields()) == {"name"}


# ------------------------------------------------- platform, server-side


def test_a_platform_becomes_a_browse_facet_rather_than_a_client_filter():
    """The change that makes `--platform` help instead of hurt.

    It used to be applied to cells already fetched, so a page of 36 games
    mostly without a Linux build yielded two or three and the budget was
    spent the same. itch.io scopes the listing itself.
    """
    search, http = make_search()
    search.search("", "linux", 5)
    url, _ = http.calls[0]
    assert url == "https://itch.io/games/free/platform-linux"


def test_the_facet_is_appended_after_any_configured_filter():
    search, http = make_search(config={"filters": ["tag-gameboy"]})
    search.search("", "browser", 5)
    assert http.calls[0][0] == (
        "https://itch.io/games/free/tag-gameboy/platform-web"
    )


def test_macos_is_platform_osx_because_platform_mac_redirects():
    """`platform-mac` answers 301; `platform-osx` answers 200. Checked live."""
    assert facet_for("mac") == "platform-osx"
    assert BROWSE_FACETS["browser"] == "platform-web"


def test_a_platform_itch_has_no_facet_for_costs_no_request():
    search, http = make_search()
    assert search.search("", "snes", 25) == []
    assert http.calls == []


def test_the_facet_goes_through_the_same_validation_as_a_configured_filter():
    """A facet is a path segment. It is built from a closed table, so this
    cannot fire today -- and the day somebody adds a row with a slash in
    it, it fires here rather than at a different endpoint."""
    for bad in ["../search", "platform web", "PLATFORM-WEB"]:
        with pytest.raises(BrowseError):
            browse_url([], bad)


def test_the_page_ceiling_matches_how_deep_the_listing_goes():
    assert DEFAULT_MAX_PAGES == 12
    assert PAGE_CAP == 200


# ------------------------------------------------------------------ stream


def test_stream_resolves_a_web_game_to_its_page():
    stream, http = make_stream(WEB_PLAYABLE)
    target = stream.resolve(SearchResult(source_id="13-23/petal", title="Petal"))
    assert target.kind == "url"
    assert target.target == "https://13-23.itch.io/petal"
    assert target.mime_type == "text/html"
    assert target.title == "Petal"
    assert target.extra["web_build"] == "true"
    assert target.extra["frame_height"] == "480"
    assert len(http.calls) == 1


def test_stream_refuses_a_download_only_game_and_says_why_it_cannot_fetch():
    stream, _ = make_stream(DOWNLOAD_ONLY)
    with pytest.raises(StreamRefused) as exc:
        stream.resolve(
            SearchResult(source_id="redspringstudio/touchstarved", title="x")
        )
    message = str(exc.value)
    assert "no browser build" in message
    assert "csrf_token" in message, "the refusal should say why import cannot help"


def test_the_web_build_marker_is_itch_ios_own():
    assert has_web_build(WEB_PLAYABLE)
    assert not has_web_build(DOWNLOAD_ONLY)
    assert not has_web_build("")


def test_stream_never_returns_the_embed_url():
    """itch.io's robots.txt Disallows /embed/ and /embed-upload/, and the
    page hands its iframe an html-classic.itch.zone URL. Neither is a
    target this plugin will produce, and neither host is declared."""
    stream, _ = make_stream(WEB_PLAYABLE)
    target = stream.resolve(SearchResult(source_id="13-23/petal", title="Petal"))
    assert "html-classic.itch.zone" in WEB_PLAYABLE, "the fixture has one to leak"
    assert "itch.zone" not in target.target
    assert "/embed" not in target.target


def test_the_stream_target_is_inside_the_declared_allowlist():
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import url_allowed

    allowlist = parse_manifest(
        (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
    ).network
    stream, _ = make_stream(WEB_PLAYABLE)
    target = stream.resolve(SearchResult(source_id="13-23/petal", title="Petal"))
    assert url_allowed(target.target, allowlist)
    # And the host the page would have leaked is not declared, so a
    # future version that returned it would fail the gate rather than
    # quietly work.
    assert not url_allowed(
        "https://html-classic.itch.zone/html/18461285/index.html", allowlist
    )


def test_stream_refuses_a_source_id_that_is_not_a_game_id():
    stream, http = make_stream(WEB_PLAYABLE)
    with pytest.raises(StreamNotIdentified):
        stream.resolve(SearchResult(source_id="not a game id", title="x"))
    assert http.calls == []


def test_stream_reports_a_non_200_rather_than_guessing():
    stream, _ = make_stream(WEB_PLAYABLE, status=404)
    with pytest.raises(StreamRefused, match="404"):
        stream.resolve(SearchResult(source_id="13-23/petal", title="x"))
