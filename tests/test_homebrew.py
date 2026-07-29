"""Homebrew Hub plugin, replayed against captured API responses.

`tests/fixtures/homebrew/` holds four verbatim `hh3.gbdev.io/api/search`
responses. The title-sorted one is there for the awkward records it
happens to contain -- an entry with `title: null`, several with no
`platform` at all, one whose filename is a path inside the entry -- all of
which are live data, not invented edge cases.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "homebrew"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "homebrew"
sys.path.insert(0, str(PLUGIN_ROOT))

from homebrew.filenames import safe_filename  # noqa: E402
from homebrew.hub import HubError, parse_entry, parse_page  # noqa: E402
from homebrew.importer import ImportRefused, Importer  # noqa: E402
from homebrew.platforms import hub_platform_for, platform_for  # noqa: E402
from homebrew.search import Search  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import FetchFile, SearchResult  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SNAKE = fixture("search_snake.json")
SNAKE_PAGE2 = fixture("search_snake_page2.json")
BY_SLUG = fixture("search_slug_super_snake_off.json")
TITLE_ASC = fixture("search_title_asc.json")


class FakeHttp:
    """Answers per page, so a walk that mis-pages is visible."""

    def __init__(self, pages, status=200):
        self.pages = pages if isinstance(pages, dict) else {1: pages}
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        params = params or {}
        self.calls.append((url, params))
        page = int(params.get("page", 1))
        payload = self.pages.get(page, {"results": 0, "page_total": 1, "entries": []})
        return HttpResponse(status_code=self.status, text=json.dumps(payload))


def make_search(pages=None, config=None, status=200):
    http = FakeHttp(pages if pages is not None else {1: SNAKE, 2: SNAKE_PAGE2}, status)
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(pages=None, config=None, status=200):
    http = FakeHttp(pages if pages is not None else {1: BY_SLUG}, status)
    return Importer(PluginContext(config=config or {}, http=http)), http


# ------------------------------------------------------------ result parsing


def test_entries_become_results():
    search, _ = make_search()
    results = search.search("snake", None, 25)
    assert len(results) == 11
    first = next(r for r in results if r.source_id == "johnybot_super-snake-off")
    assert first.title == "Super Snake Off"
    assert first.platform == "nes"
    assert first.url == "https://hh.gbdev.io/g/johnybot_super-snake-off"
    assert first.extra["developer"] == "Johnybot"
    assert first.extra["hub_platform"] == "NES"


def test_the_query_goes_to_the_server():
    search, http = make_search()
    search.search("snake", None, 5)
    assert http.calls[0][1]["q"] == "snake"


def test_an_entry_with_no_title_is_skipped():
    search, _ = make_search({1: TITLE_ASC})
    results = search.search("", None, 25)
    assert all(
        r.source_id != "super-jacked-up-tomato-face-johnson" for r in results
    )
    # ...and the nine usable records around it still come through.
    assert len(results) == len(TITLE_ASC["entries"]) - 1


def test_an_entry_with_no_platform_still_appears_but_claims_none():
    search, _ = make_search({1: TITLE_ASC})
    entry = next(
        r for r in search.search("", None, 25) if r.source_id == "1d-marathon"
    )
    assert entry.platform is None
    assert entry.extra["hub_platform"] == ""


def test_the_typetag_filter_is_passed_through_when_configured():
    search, http = make_search(config={"typetag": "game"})
    search.search("snake", None, 5)
    assert http.calls[0][1]["typetag"] == "game"


def test_no_typetag_is_sent_when_it_is_not_configured():
    search, http = make_search()
    search.search("snake", None, 5)
    assert "typetag" not in http.calls[0][1]


def test_parse_page_rejects_an_answer_with_no_entries_list():
    with pytest.raises(HubError):
        parse_page({"results": 3})


def test_a_non_200_is_a_hard_failure_naming_the_status():
    search, _ = make_search(status=502)
    with pytest.raises(HubError, match="502"):
        search.search("snake", None, 5)


def test_a_non_json_answer_is_a_hard_failure():
    class Broken:
        calls = []

        def get(self, url, params=None):
            return HttpResponse(status_code=200, text="<html>nope</html>")

    search = Search(PluginContext(config={}, http=Broken()))
    with pytest.raises(HubError, match="not JSON"):
        search.search("snake", None, 5)


# ----------------------------------------------------------------- the walk


def test_the_walk_continues_onto_page_two():
    search, http = make_search()
    results = search.search("snake", None, 25)
    assert [p["page"] for _, p in http.calls] == [1, 2]
    assert len(results) == 11


def test_the_walk_stops_at_the_limit_without_a_second_request():
    search, http = make_search()
    assert len(search.search("snake", None, 4)) == 4
    assert len(http.calls) == 1


def test_the_walk_stops_when_the_hub_says_there_are_no_more_pages():
    search, http = make_search({1: BY_SLUG}, config={"max_pages": 5})
    search.search("johnybot_super-snake-off", None, 25)
    assert len(http.calls) == 1


def test_max_pages_bounds_the_walk():
    many = {n: SNAKE for n in range(1, 10)}
    search, http = make_search(many, config={"max_pages": 2})
    search.search("snake", None, 100)
    assert [p["page"] for _, p in http.calls] == [1, 2]


def test_a_nonsense_max_pages_falls_back_to_the_default():
    search, http = make_search({n: TITLE_ASC for n in range(1, 10)},
                               config={"max_pages": "lots"})
    search.search("", None, 100)
    assert len(http.calls) == 3


# ----------------------------------------------------------- platform mapping


@pytest.mark.parametrize(
    "hub,slug", [("GB", "gb"), ("GBC", "gbc"), ("GBA", "gba"), ("NES", "nes")]
)
def test_the_four_hub_platforms_map(hub, slug):
    assert platform_for(hub) == slug
    assert hub_platform_for(slug) == hub


def test_game_boy_color_is_not_softened_into_game_boy():
    assert platform_for("GBC") == "gbc"
    assert platform_for("GBC") != platform_for("GB")


def test_an_unknown_hub_platform_maps_to_nothing():
    assert platform_for("SNES") is None
    assert platform_for("") is None


def test_a_platform_filter_is_translated_and_sent_to_the_server():
    search, http = make_search()
    search.search("snake", "gbc", 5)
    # The Hub's filter is case-sensitive: "gbc" matches nothing there.
    assert http.calls[0][1]["platform"] == "GBC"


def test_a_platform_this_archive_does_not_hold_costs_no_request():
    search, http = make_search()
    assert search.search("snake", "dc", 5) == []
    assert http.calls == []


def test_an_unmapped_hub_platform_refuses_the_import_and_names_it():
    payload = json.loads(json.dumps(BY_SLUG))
    payload["entries"][0]["platform"] = "SUPERVISION"
    importer, _ = make_importer({1: payload})
    with pytest.raises(ImportRefused, match="SUPERVISION"):
        importer.plan(SearchResult(source_id="johnybot_super-snake-off", title="x"))


def test_an_entry_with_no_platform_refuses_rather_than_reading_the_basepath():
    # "database-gb" holds both Game Boy and Game Boy Color titles, so it is
    # not a platform, and this is a live record rather than a contrivance.
    importer, _ = make_importer({1: TITLE_ASC})
    with pytest.raises(ImportRefused, match="declares no platform"):
        importer.plan(SearchResult(source_id="1d-marathon", title="x"))


def test_an_operator_override_settles_a_missing_platform():
    importer, _ = make_importer({1: TITLE_ASC})
    plan = importer.plan(
        SearchResult(source_id="1d-marathon", title="x", platform="gb")
    )
    assert plan.platform == "gb"


# ------------------------------------------------------------------ planning


def test_a_plan_points_at_the_hubs_static_path():
    importer, _ = make_importer()
    plan = importer.plan(
        SearchResult(source_id="johnybot_super-snake-off", title="x")
    )
    assert plan.platform == "nes"
    assert plan.collection == "Homebrew"
    assert plan.files[0].url == (
        "https://hh3.gbdev.io/static/database-nes/entries/"
        "johnybot_super-snake-off/files/SnakeOff.nes"
    )


def test_the_entry_relative_path_stays_in_the_url_and_leaves_the_filename():
    importer, _ = make_importer({1: TITLE_ASC})
    plan = importer.plan(
        SearchResult(source_id="voxel_game-boy-advance-quokka-wokka", title="x")
    )
    assert plan.files[0].url.endswith("/files/quokka-wokka_jam.gba")
    assert plan.files[0].filename == "quokka-wokka_jam.gba"


def test_the_hubs_default_file_wins_over_the_first_listed():
    payload = json.loads(json.dumps(BY_SLUG))
    payload["entries"][0]["files"] = [
        {"filename": "source.zip", "default": False},
        {"filename": "SnakeOff.nes", "default": True},
    ]
    importer, _ = make_importer({1: payload})
    plan = importer.plan(
        SearchResult(source_id="johnybot_super-snake-off", title="x")
    )
    assert plan.files[0].filename == "SnakeOff.nes"


def test_the_default_file_wins_even_when_it_is_not_listed_first():
    """A live record: the .ZIP source drop is listed before the .gb ROM."""
    importer, _ = make_importer({1: TITLE_ASC})
    plan = importer.plan(
        SearchResult(source_id="parallax-starfield", title="x", platform="gbc")
    )
    assert plan.files[0].filename == "Stars.gb"


def test_with_no_default_flag_the_first_listed_file_wins():
    payload = json.loads(json.dumps(BY_SLUG))
    payload["entries"][0]["files"] = [
        {"filename": "first.nes"},
        {"filename": "second.nes"},
    ]
    importer, _ = make_importer({1: payload})
    plan = importer.plan(
        SearchResult(source_id="johnybot_super-snake-off", title="x")
    )
    assert plan.files[0].filename == "first.nes"


def test_an_entry_with_no_files_is_refused():
    payload = json.loads(json.dumps(BY_SLUG))
    payload["entries"][0]["files"] = []
    importer, _ = make_importer({1: payload})
    with pytest.raises(ImportRefused, match="lists no files"):
        importer.plan(SearchResult(source_id="johnybot_super-snake-off", title="x"))


def test_a_near_miss_is_refused_rather_than_imported():
    """`?q=<slug>` is a text search and can answer with something else."""
    importer, _ = make_importer({1: SNAKE})
    with pytest.raises(ImportRefused, match="no Homebrew Hub entry has the slug"):
        importer.plan(SearchResult(source_id="snake-off", title="x"))


def test_an_empty_source_id_is_refused_before_any_request():
    importer, http = make_importer()
    with pytest.raises(ImportRefused, match="no Homebrew Hub slug"):
        importer.plan(SearchResult(source_id="   ", title="x"))
    assert http.calls == []


def test_a_non_200_lookup_is_refused_with_the_status():
    importer, _ = make_importer(status=500)
    with pytest.raises(ImportRefused, match="500"):
        importer.plan(SearchResult(source_id="johnybot_super-snake-off", title="x"))


def test_parse_entry_drops_records_missing_what_a_plan_needs():
    assert parse_entry({"slug": "s", "title": "t"}) is None          # no basepath
    assert parse_entry({"title": "t", "basepath": "b"}) is None      # no slug
    assert parse_entry({"slug": "s", "basepath": "b"}) is None       # no title
    assert parse_entry("not a dict") is None


# ------------------------------------------------------- filename sanitising


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SnakeOff.nes", "SnakeOff.nes"),
        ("files/quokka-wokka_jam.gba", "quokka-wokka_jam.gba"),
        ("OMB-STAR.ZIP", "OMB-STAR.ZIP"),
        ("The_Great_Strategy_(2005).gb", "The_Great_Strategy_(2005).gb"),
        (r"win\path\game.gbc", "game.gbc"),
        ("C:evil.gb", "C_evil.gb"),
        ("aux.gb", "_aux.gb"),
        ("..", "rom.bin"),
    ],
)
def test_sanitised_names_are_accepted_by_the_host(raw, expected):
    name = safe_filename(raw, fallback="rom.bin")
    assert name == expected
    FetchFile(url="https://hh3.gbdev.io/x", filename=name)


def test_every_filename_in_the_captured_pages_survives_sanitising():
    seen = 0
    for payload in (SNAKE, SNAKE_PAGE2, BY_SLUG, TITLE_ASC):
        for raw in payload["entries"]:
            entry = parse_entry(raw)
            if entry is None:
                continue
            for hub_file in entry.files:
                FetchFile(
                    url="https://hh3.gbdev.io/x",
                    filename=safe_filename(hub_file.filename, fallback="rom.bin"),
                )
                seen += 1
    assert seen > 20
