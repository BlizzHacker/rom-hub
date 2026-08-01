"""Aminet plugin, replayed against captured pages and readmes.

`tests/fixtures/aminet/` holds verbatim `aminet.net` bodies of four
shapes and two `.readme` files.

* **search pages** (`search_*.html`) for what they really contain: fifty
  rows in *two different markups* (light rows and dark rows differ), four
  architectures in one directory, and near-identical filenames targeting
  different computers;
* **shelf listings** (`shelf_game_think_p1.html`,
  `shelf_game_race_p1.html`) -- `aminet.net/game/think?page=1`, captured
  2026-08-01. Same table, same count line, and the endpoint that makes a
  browse possible at all. `think` says "Found 910"; `race` says "Found
  75", so one is a shelf with 19 pages and the other is a shelf that ends
  on page 2, which is what makes the stop conditions testable;
* **the search form** (`search_form_no_query.html`) -- what
  `?dir=game` with no `query` actually returns. No count line, no rows.
  This is the fixture that pins the bug: an empty query used to be sent
  to `/search`, and this is what came back.

The two readmes are the same game built for two machines --
`ppc-amigaos` and `ppc-morphos` -- which is exactly the confusion the
platform table exists to prevent.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "aminet"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "aminet"
sys.path.insert(0, str(PLUGIN_ROOT))

from aminet.archive import (  # noqa: E402
    SEARCH,
    AminetError,
    download_url,
    parse_readme,
    parse_results,
    readme_url,
    shelf_url,
    total_matches,
)
from aminet.importer import ImportRefused, Importer  # noqa: E402
from aminet.platforms import (  # noqa: E402
    ARCHITECTURES,
    GAME_DIRS,
    NOT_AN_AMIGA,
    holds_games,
    platform_for,
    why_unmapped,
)
from aminet.search import DEFAULT_SHELVES, Search  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import SearchResult  # noqa: E402


def fixture(name: str) -> str:
    # Aminet declares iso-8859-1. `ctx.http` hands a plugin `str`, so the
    # decoding is the host's problem at runtime -- a file read has to say
    # so itself.
    return (FIXTURES / name).read_text(encoding="latin-1")


TETRIS = fixture("search_tetris_game.html")
QUAKE = fixture("search_quake_game.html")
#: `?query=steel+sky&dir=game` -- one match, and it is on `game/hint`, so
#: every filter drops it and the plugin keeps nothing from a page that was
#: perfectly fine.
STEEL_SKY = fixture("search_steel_sky_game.html")
#: "Found 0 matching packages": a real search page with no table.
NO_MATCHES = fixture("search_no_matches.html")
#: aminet.net/robots.txt -- HTTP 200 whose body is a themed error page.
NOT_FOUND_200 = fixture("not_found_200.html")
#: `aminet.net/game/think?page=1` -- "Found 910 matching packages".
SHELF_THINK = fixture("shelf_game_think_p1.html")
#: `aminet.net/game/race?page=1` -- "Found 75", so page 2 is the last.
SHELF_RACE = fixture("shelf_game_race_p1.html")
#: `aminet.net/search?dir=game` with no `query`: the search *form*.
SEARCH_FORM = fixture("search_form_no_query.html")
README_ABRICK = fixture("readme_abrick.txt")
README_MOS = fixture("readme_abandondedbricks.txt")


class FakeHttp:
    """Search pages by page number, shelf listings by shelf, readmes by path."""

    def __init__(self, pages=None, readmes=None, status=200, shelves=None):
        self.pages = pages if pages is not None else {1: TETRIS}
        self.readmes = readmes or {}
        self.shelves = shelves or {}
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None):
        params = params or {}
        self.calls.append((url, params))
        page = int(params.get("page", 1))
        if url == SEARCH:
            # A page the test did not stock answers the way Aminet does
            # when the results have run out: a real search page carrying
            # "Found 0 matching packages" and no table.
            return HttpResponse(
                status_code=self.status, text=self.pages.get(page, NO_MATCHES)
            )
        for shelf, bodies in self.shelves.items():
            if shelf_url(shelf) == url:
                body = bodies.get(page) if isinstance(bodies, dict) else bodies
                return HttpResponse(
                    status_code=self.status, text=body or NO_MATCHES
                )
        for path, body in self.readmes.items():
            if readme_url(path) == url:
                return HttpResponse(status_code=self.status, text=body)
        return HttpResponse(status_code=404, text="<html>not found</html>")


DEFAULT_READMES = {
    "game/think/abrick.readme": README_ABRICK,
    "game/think/AbandondedBricks.readme": README_MOS,
}


def make_search(pages=None, config=None, status=200, shelves=None):
    http = FakeHttp(pages, DEFAULT_READMES, status, shelves)
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(readmes=None, config=None, status=200):
    http = FakeHttp(
        None, DEFAULT_READMES if readmes is None else readmes, status
    )
    return Importer(PluginContext(config=config or {}, http=http)), http


# ------------------------------------------------------------- result parsing


def test_both_row_markups_are_read():
    """Light and dark rows differ; a regex on `pkg_row">` finds half."""
    packages = parse_results(TETRIS)
    assert len(packages) == 50, "Aminet serves 50 rows a page"
    paths = [p.path for p in packages]
    assert "game/think/AbandondedBricks.lha" in paths  # lightrow
    assert "game/think/abandoned_bricks-mos.lha" in paths  # darkrow


def test_a_row_carries_its_architecture_shelf_size_and_description():
    packages = {p.path: p for p in parse_results(TETRIS)}
    entry = packages["game/think/alleytris_68k.lha"]
    assert entry.filename == "alleytris_68k.lha"
    assert entry.directory == "game/think"
    assert entry.architectures == ("m68k-amigaos",)
    assert entry.description == "(n)curses based tetris game"
    assert entry.size_text == "142K"
    assert entry.date_text == "2013-10-08"


def test_the_site_furniture_is_never_read_as_an_architecture():
    for package in parse_results(TETRIS) + parse_results(QUAKE):
        assert "aminet" not in package.architectures
        assert "aminet_sketch_64" not in package.architectures


def test_four_computers_share_one_directory():
    """The whole reason the architecture is load-bearing."""
    found = set()
    for package in parse_results(TETRIS):
        found.update(package.architectures)
    assert {"m68k-amigaos", "ppc-morphos", "ppc-amigaos"} <= found


def test_the_readme_path_is_a_stem_swap():
    packages = {p.path: p for p in parse_results(TETRIS)}
    entry = packages["game/think/alleytris_68k.lha"]
    assert entry.readme_path == "game/think/alleytris_68k.readme"


def test_the_real_200_error_page_is_refused():
    """`aminet.net/robots.txt` really is a 200 with a themed error body."""
    with pytest.raises(AminetError):
        parse_results(NOT_FOUND_200)
    with pytest.raises(AminetError):
        parse_results("")


def test_a_search_page_with_no_table_is_valid_and_empty():
    """"Found 0 matching packages" is an answer, not a broken source.

    Keyed on the count line rather than the result table, because a page
    that legitimately carries no table is a real thing Aminet serves.
    """
    assert parse_results(NO_MATCHES) == []


# ------------------------------------------------------------ readme parsing


def test_a_readme_header_is_read_and_stops_at_the_blank_line():
    header = parse_readme(README_ABRICK)
    assert header["short"] == "Tetris clone."
    assert header["type"] == "game/think"
    assert header["architecture"] == "ppc-amigaos >= 4.0.0"
    # Prose below the header must not become fields.
    assert "about game" not in header


def test_an_empty_or_headerless_readme_raises():
    with pytest.raises(AminetError):
        parse_readme("")
    with pytest.raises(AminetError):
        parse_readme("just some prose with no colon\n\nand more\n")


# ------------------------------------------------------------------ platforms


def test_only_the_three_amiga_architectures_map():
    assert platform_for("m68k-amigaos") == "amiga"
    assert platform_for("ppc-warpup") == "amiga"
    assert platform_for("ppc-powerup") == "amiga"
    for arch in NOT_AN_AMIGA:
        assert platform_for(arch) is None, arch


def test_the_os_version_qualifier_is_split_off_before_lookup():
    """`Architecture: ppc-amigaos >= 4.0.0` is a real readme line."""
    assert platform_for("m68k-amigaos >= 3.0") == "amiga"
    assert platform_for("ppc-amigaos >= 4.0.0") is None


def test_a_refusal_names_the_machine_rather_than_saying_unknown():
    morphos = why_unmapped("ppc-morphos")
    assert "MorphOS" in morphos
    assert "not a Commodore Amiga" in morphos

    generic = why_unmapped("generic")
    assert "absence of an architecture" in generic

    novel = why_unmapped("riscv-amigaos")
    assert "needs mapping" in novel
    assert "aminet/platforms.py" in novel

    assert "declares no Architecture" in why_unmapped("")


def test_four_game_shelves_hold_no_games():
    assert holds_games("game/think") is True
    assert holds_games("game/demo") is True
    for shelf in ("game/data", "game/edit", "game/hint", "game/patch"):
        assert holds_games(shelf) is False, shelf
    # Not a `game/` shelf at all.
    assert holds_games("util/libs") is None
    assert holds_games("mods/techno") is None


def test_the_shelf_table_matches_aminets_own_tree():
    assert len(GAME_DIRS) == 18
    assert sum(1 for _, holds in GAME_DIRS.values() if holds) == 14


# --------------------------------------------------------------------- search


def test_the_query_goes_to_the_server_and_dir_does_not():
    """`dir=` never scoped anything, so it is no longer sent.

    Verified live 2026-08-01: `?query=tetris`, `&dir=game`, `&dir=demo`
    and `&dir=zzz` return the identical 134 packages. Sending it made the
    plugin *look* server-scoped -- its README said so -- while every
    non-game row arrived anyway and was thrown away here. A parameter a
    server ignores is not a filter, and continuing to send one would keep
    the claim alive in the request log.
    """
    search, http = make_search()
    search.search("tetris", None, 5)
    url, params = http.calls[0]
    assert url == SEARCH
    assert params["query"] == "tetris"
    assert "dir" not in params


def test_the_search_form_is_what_an_empty_query_used_to_get():
    """The fixture that pins the bug this release fixes.

    `?dir=game` with no `query` is not a zero-result search. It is the
    search *form*: no count line, no rows. `parse_results` refuses it --
    correctly -- so a browse used to fail outright rather than return
    nothing.
    """
    with pytest.raises(AminetError) as exc:
        parse_results(SEARCH_FORM)
    assert "matching package" in str(exc.value)


def test_results_carry_the_architecture_whether_or_not_it_maps():
    search, _ = make_search()
    results = {r.source_id: r for r in search.search("tetris", None, 100)}
    mapped = results["game/think/alleytris_68k.lha"]
    assert mapped.platform == "amiga"
    assert mapped.extra["architecture"] == "m68k-amigaos"

    unmapped = results["game/think/AbandondedBricks.lha"]
    assert unmapped.platform is None
    assert unmapped.extra["architecture"] == "ppc-morphos"


def test_the_shelf_description_is_aminets_own():
    search, _ = make_search()
    result = search.search("tetris", None, 1)[0]
    assert result.extra["shelf"] == "Mind games"


def test_platform_filters_client_side_because_aminet_has_no_arch_filter():
    search, _ = make_search()
    results = search.search("tetris", "amiga", 100)
    assert results
    assert all(r.platform == "amiga" for r in results)
    assert all(
        r.extra["architecture"] in ARCHITECTURES for r in results
    )


def test_a_platform_this_source_has_nothing_for_costs_no_request():
    search, http = make_search()
    assert search.search("tetris", "snes", 25) == []
    assert http.calls == []


def test_support_shelves_are_hidden_by_default_and_listable_on_request():
    quiet, _ = make_search(pages={1: QUAKE})
    loud, _ = make_search(pages={1: QUAKE}, config={"include_support": True})
    quiet_dirs = {r.extra["directory"] for r in quiet.search("quake", None, 100)}
    loud_dirs = {r.extra["directory"] for r in loud.search("quake", None, 100)}
    assert quiet_dirs <= loud_dirs
    assert not any(holds_games(d) is not True for d in quiet_dirs)
    assert len(loud_dirs) > len(quiet_dirs), (
        "the quake fixture should contain at least one support shelf"
    )


def test_the_walk_is_bounded_by_max_pages():
    search, http = make_search(pages={1: TETRIS, 2: TETRIS}, config={"max_pages": 2})
    search.search("tetris", None, 500)
    assert [params.get("page") for _, params in http.calls] == [1, 2]


def test_a_short_page_ends_the_walk_even_when_nothing_was_kept():
    """The `steel sky` case, which took a live search down.

    One match, on `game/hint`, so every filter drops it. A walk keyed on
    "no results yet" would ask for page 2 -- a real, valid, tableless page
    that a shape check keyed on the table would then call a dead source.
    """
    search, http = make_search(pages={1: STEEL_SKY}, config={"max_pages": 5})
    assert search.search("steel sky", None, 25) == []
    assert [params.get("page") for _, params in http.calls] == [1]


def test_a_non_200_search_raises():
    search, _ = make_search(status=502)
    with pytest.raises(AminetError):
        search.search("tetris", None, 5)


# ---------------------------------------------------------------------- browse


def test_an_empty_query_browses_shelves_instead_of_searching():
    """The whole point of the shelf endpoint: a browse that works.

    Before this, an empty query went to `/search` with no `query`, and
    Aminet answered with the form. Now it goes to `/game/2play`, which is
    a real listing scoped by the server.
    """
    search, http = make_search(shelves={"game/2play": {1: SHELF_THINK}})
    results = search.search("", None, 5)
    assert len(results) == 5
    url, params = http.calls[0]
    assert url == shelf_url(DEFAULT_SHELVES[0])
    assert params == {"page": 1}
    assert all(call[0] != SEARCH for call in http.calls)


def test_a_browse_walks_from_one_shelf_to_the_next():
    """`game/race` ends at 75 packages; the walk moves on rather than
    asking for a page that is not there."""
    search, http = make_search(
        config={"shelves": ["game/race", "game/think"], "max_pages": 4},
        shelves={
            "game/race": {1: SHELF_RACE, 2: SHELF_RACE},
            "game/think": {1: SHELF_THINK},
        },
    )
    search.search("", None, 500)
    asked = [(url, params.get("page")) for url, params in http.calls]
    assert asked[:2] == [
        (shelf_url("game/race"), 1),
        (shelf_url("game/race"), 2),
    ]
    assert (shelf_url("game/think"), 1) in asked


def test_the_count_line_stops_the_walk_at_the_real_last_page():
    """`Found 75` over 50 rows a page means two pages exist and no more.

    Without reading the count, a full page 2 would look like "there may
    be more" and cost a third request for a page Aminet does not have.
    """
    assert total_matches(SHELF_RACE) == 75
    assert total_matches(SHELF_THINK) == 910
    assert total_matches("no count line here") is None


def test_a_browse_needs_no_client_side_shelf_filter():
    """The shelf is the scope, so every row of it is on that shelf."""
    search, _ = make_search(shelves={"game/2play": {1: SHELF_THINK}})
    results = search.search("", None, 50)
    assert results
    assert {r.extra["directory"] for r in results} == {"game/think"}


def test_the_default_browse_is_the_fourteen_game_shelves():
    assert len(DEFAULT_SHELVES) == 14
    assert all(holds_games(shelf) is True for shelf in DEFAULT_SHELVES)
    for excluded in ("game/data", "game/edit", "game/hint", "game/patch"):
        assert excluded not in DEFAULT_SHELVES


def test_a_shelf_aminet_does_not_have_is_refused_before_the_request():
    search, http = make_search(config={"shelves": ["game/roguelike"]})
    with pytest.raises(AminetError) as exc:
        search.search("", None, 5)
    assert "game/roguelike" in str(exc.value)
    assert http.calls == []


def test_a_shelf_name_cannot_be_a_path_or_a_url():
    for bad in ("../etc/passwd", "game/../util", "https://evil.example/x", ""):
        with pytest.raises(AminetError):
            shelf_url(bad)
    assert shelf_url("game/think") == "https://aminet.net/game/think"


def test_every_browse_url_is_inside_the_declared_allowlist():
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import url_allowed

    allowlist = parse_manifest(
        (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
    ).network
    for shelf in DEFAULT_SHELVES:
        assert url_allowed(shelf_url(shelf), allowlist)
    # And a host nobody declared is refused, so the check is doing work.
    assert not url_allowed("https://mos.aminet.net/game/think", allowlist)


# ------------------------------------------------------------------- importer


def test_a_plan_reads_the_readme_and_uses_its_architecture():
    importer, http = make_importer(
        readmes={"game/think/alleytris_68k.readme": README_ABRICK.replace(
            "ppc-amigaos >= 4.0.0", "m68k-amigaos"
        )}
    )
    plan = importer.plan(
        SearchResult(source_id="game/think/alleytris_68k.lha", title="x")
    )
    assert plan.platform == "amiga"
    assert plan.collection == "Aminet"
    assert plan.files[0].url == download_url("game/think/alleytris_68k.lha")
    assert plan.files[0].filename == "alleytris_68k.lha"
    assert http.calls[0][0] == readme_url("game/think/alleytris_68k.readme")


def test_an_amigaos4_package_refuses_and_names_the_machine():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="game/think/abrick.lha", title="x"))
    message = str(excinfo.value)
    assert "AmigaOS 4" in message
    assert "not a Commodore Amiga" in message


def test_an_operator_platform_overrides_an_architecture_that_would_refuse():
    """The readme is still fetched: it is also the existence proof and the
    `Type:` check, and neither of those is what --platform overrides."""
    importer, http = make_importer()
    plan = importer.plan(
        SearchResult(
            source_id="game/think/abrick.lha", title="x", platform="amiga-cd32"
        )
    )
    assert plan.platform == "amiga-cd32"
    assert http.calls[0][0] == readme_url("game/think/abrick.readme")


def test_a_support_shelf_refuses_with_aminets_own_description():
    importer, http = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="game/hint/walkthrough.lha", title="x"))
    assert "Game hint documents" in str(excinfo.value)
    assert http.calls == [], "a shelf refusal must not cost a request"


def test_a_path_outside_the_game_tree_refuses():
    importer, http = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="util/libs/powersdl.lha", title="x"))
    assert "not in Aminet's game tree" in str(excinfo.value)
    assert http.calls == []


def test_a_readme_whose_type_disagrees_with_the_path_refuses():
    """The readme is the uploader's statement; a mismatch is not ours to
    resolve."""
    importer, _ = make_importer(
        readmes={"game/shoot/abrick.readme": README_ABRICK}  # declares game/think
    )
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="game/shoot/abrick.lha", title="x"))
    assert "disagree" in str(excinfo.value)


def test_a_missing_package_refuses_because_aminet_answers_200_for_one():
    importer, _ = make_importer(readmes={})
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="game/think/gone.lha", title="x"))
    assert "could not be confirmed" in str(excinfo.value)


def test_a_200_error_page_instead_of_a_readme_refuses():
    importer, _ = make_importer(
        readmes={"game/think/x.readme": "<html><title>not found</title></html>"}
    )
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="game/think/x.lha", title="x"))
    assert "no readable .readme header" in str(excinfo.value)


def test_a_url_or_a_traversal_is_refused_rather_than_sanitised():
    importer, http = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(
            SearchResult(source_id="https://evil.example/x.lha", title="x")
        )
    assert "is a URL" in str(excinfo.value)
    for bad in ("game/../../etc/passwd", "game", "   ", "game//x.lha"):
        with pytest.raises(ImportRefused):
            importer.plan(SearchResult(source_id=bad, title="x"))
    assert http.calls == []
