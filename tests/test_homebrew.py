"""Homebrew Hub plugin, replayed against captured API responses.

`tests/fixtures/homebrew/` holds six verbatim `hh3.gbdev.io/api/search`
responses. The title-sorted one is there for the awkward records it
happens to contain -- an entry with `title: null`, several with no
`platform` at all, one whose filename is a path inside the entry -- all of
which are live data, not invented edge cases.

Two were captured for `stream` on 2026-08-01:

* `search_slug_ghx_demos.json` -- one of exactly six entries in the whole
  1,571-entry catalogue with **no file flagged `playable`**. Its page
  renders no emulator, so it is the case `stream` has to refuse rather
  than hand back a URL that shows the operator nothing;
* `search_tags_puzzle.json` -- `?tags=Puzzle`, 114 results over 12 pages.
  The server-side tag filter, which is the third real one alongside `q`
  and `platform`.

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
from homebrew.metadata import (  # noqa: E402
    Ambiguous,
    ApiFailed,
    Metadata,
    NoMatch,
)
from homebrew.platforms import hub_platform_for, platform_for  # noqa: E402
from homebrew.search import DEFAULT_MAX_PAGES, PAGE_CAP, Search  # noqa: E402
from homebrew.stream import Stream, StreamRefused  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import FetchFile, RomRef, SearchResult  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SNAKE = fixture("search_snake.json")
SNAKE_PAGE2 = fixture("search_snake_page2.json")
BY_SLUG = fixture("search_slug_super_snake_off.json")
TITLE_ASC = fixture("search_title_asc.json")
#: One of the six entries in the catalogue with no playable file.
NO_PLAYABLE = fixture("search_slug_ghx_demos.json")
#: `?tags=Puzzle` -- 114 results, 12 pages.
TAGS_PUZZLE = fixture("search_tags_puzzle.json")


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


def make_stream(pages=None, config=None, status=200):
    http = FakeHttp(pages if pages is not None else {1: BY_SLUG}, status)
    return Stream(PluginContext(config=config or {}, http=http)), http


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
    assert first.url == "https://hh.gbdev.io/game/johnybot_super-snake-off"
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
    search, http = make_search({n: TITLE_ASC for n in range(1, 30)},
                               config={"max_pages": "lots"})
    search.search("", None, 1000)
    assert len(http.calls) == DEFAULT_MAX_PAGES


# ---------------------------------------------------------- the tag filter


def test_tags_go_to_the_server_as_one_comma_separated_parameter():
    search, http = make_search(
        {1: TAGS_PUZZLE}, config={"tags": ["Open Source", "RPG"]}
    )
    search.search("", None, 5)
    assert http.calls[0][1]["tags"] == "Open Source,RPG"


def test_a_tags_string_is_accepted_as_well_as_a_list():
    """Both spellings turn up in a TOML config and neither is wrong."""
    search, http = make_search({1: TAGS_PUZZLE}, config={"tags": "Puzzle"})
    search.search("", None, 5)
    assert http.calls[0][1]["tags"] == "Puzzle"


def test_no_tags_configured_sends_no_tags_parameter():
    """An empty filter must be absent, not present-and-empty.

    Unknown and empty parameters are both ignored by this API, so sending
    `tags=` would be harmless -- and it would also be a line in the
    request log claiming a filter that is not there.
    """
    search, http = make_search({1: SNAKE})
    search.search("snake", None, 5)
    assert "tags" not in http.calls[0][1]


def test_results_carry_the_hubs_own_licence_tags_and_date():
    search, _ = make_search({1: TAGS_PUZZLE})
    result = search.search("", None, 1)[0]
    assert set(result.extra) >= {"license", "tags", "date", "playable"}


# ------------------------------------------------------------------- stream


def test_stream_resolves_an_entry_to_the_page_that_plays_it():
    stream, http = make_stream()
    target = stream.resolve(
        SearchResult(source_id="johnybot_super-snake-off", title="Super Snake Off")
    )
    assert target.kind == "url"
    assert target.target == "https://hh.gbdev.io/game/johnybot_super-snake-off"
    assert target.mime_type == "text/html"
    assert target.title == "Super Snake Off"
    assert target.extra["platform"] == "nes"
    assert target.extra["rom"] == "files/SnakeOff.nes"
    assert len(http.calls) == 1


def test_stream_refuses_an_entry_with_no_playable_file():
    """Six of 1,571. Their page renders no emulator at all."""
    stream, _ = make_stream({1: NO_PLAYABLE})
    with pytest.raises(StreamRefused, match="playable"):
        stream.resolve(SearchResult(source_id="ghx-demos", title="GHX Demos"))


def test_stream_refuses_a_near_miss_rather_than_playing_it():
    stream, _ = make_stream({1: SNAKE})
    with pytest.raises(StreamRefused, match="no Homebrew Hub entry"):
        stream.resolve(SearchResult(source_id="snake", title="Snake"))


def test_stream_refuses_an_empty_source_id_without_a_request():
    stream, http = make_stream()
    with pytest.raises(StreamRefused):
        stream.resolve(SearchResult(source_id=" ", title="x"))
    assert http.calls == []


def test_the_stream_target_is_inside_the_declared_allowlist():
    """A stream URL is allowlist-gated exactly like a FetchPlan URL."""
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import url_allowed

    allowlist = parse_manifest(
        (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
    ).network
    stream, _ = make_stream()
    target = stream.resolve(
        SearchResult(source_id="johnybot_super-snake-off", title="x")
    )
    assert url_allowed(target.target, allowlist)


def test_a_stream_target_on_an_undeclared_host_would_be_refused():
    """The gate is doing work, not decorating the manifest.

    gbdev's own pages live on two hosts and only those two are declared;
    a third -- say the GitHub repository an entry names as its source --
    fails the check even though it is a perfectly real gbdev URL.
    """
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import url_allowed

    allowlist = parse_manifest(
        (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
    ).network
    assert not url_allowed("https://github.com/gbdev/database", allowlist)
    assert not url_allowed("https://gbdev.io/game/anything", allowlist)


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


# ------------------------------------------------------------------ metadata


class ParamHttp:
    """Serves one payload and remembers the params it was asked with.

    The metadata provider narrows by platform when it can, and that
    narrowing is the difference between one candidate and three in the
    captured data -- so the params have to be observable, not just the
    result.
    """

    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return HttpResponse(status_code=self.status, text=json.dumps(self.payload))


def make_metadata(payload=None, config=None, status=200):
    http = ParamHttp(payload if payload is not None else BY_SLUG, status)
    return Metadata(PluginContext(config=config or {}, http=http)), http


def rom(**kwargs):
    base = {"rom_id": 1, "name": "", "filename": "", "platform": None, "extra": {}}
    return RomRef(**{**base, **kwargs})


def test_a_source_id_resolves_straight_to_the_entry():
    meta, http = make_metadata()
    patch = meta.enrich(rom(extra={"source_id": "johnybot_super-snake-off"}))
    assert patch.name == "Super Snake Off"
    assert patch.artwork_url == (
        "https://hh3.gbdev.io/static/database-nes/entries/"
        "johnybot_super-snake-off/cover.png"
    )
    assert patch.artwork_filename == "cover.png"
    assert len(http.calls) == 1


def test_a_source_id_the_hub_does_not_have_is_a_refusal_not_a_near_miss():
    """`?q=` is a text search, so it answers with something. Taking it would
    attach another game's title and cover."""
    meta, _ = make_metadata()
    with pytest.raises(NoMatch, match="near misses"):
        meta.enrich(rom(extra={"source_id": "not-a-real-slug"}))


def test_a_title_resolves_when_exactly_one_entry_carries_it():
    meta, http = make_metadata(SNAKE)
    patch = meta.enrich(rom(name="Sneaky Snakes"))
    assert patch.name == "Sneaky Snakes"
    assert http.calls[0][1]["q"] == "Sneaky Snakes"


def test_matching_ignores_case_and_punctuation_but_stays_an_equality_test():
    meta, _ = make_metadata(SNAKE)
    assert meta.enrich(rom(name="sneaky  snakes!")).name == "Sneaky Snakes"
    # "Snake" must not pick up "Sneaky Snakes" or "Snake GBDK".
    with pytest.raises(Ambiguous):
        meta.enrich(rom(name="Snake"))


def test_several_entries_with_one_title_refuse_and_name_them():
    """Live data: three Game Boy entries are titled exactly "Snake"."""
    meta, _ = make_metadata(SNAKE)
    with pytest.raises(Ambiguous, match="--source-id") as caught:
        meta.enrich(rom(name="Snake"))
    assert "snake-sanky" in str(caught.value)
    assert "gb-snake-reini1305" in str(caught.value)


def test_the_platform_narrows_the_query_before_it_narrows_the_answer():
    meta, http = make_metadata(SNAKE)
    with pytest.raises(Ambiguous):
        meta.enrich(rom(name="Snake", platform="gb"))
    assert http.calls[0][1]["platform"] == "GB"


def test_a_platform_the_hub_does_not_carry_simply_does_not_filter():
    """The Hub covers four systems. A rom on a fifth is a miss, not a
    fault, and the query still goes out unfiltered rather than raising."""
    meta, http = make_metadata(SNAKE)
    with pytest.raises(NoMatch):
        meta.enrich(rom(name="Sonic", platform="dreamcast"))
    assert "platform" not in http.calls[0][1]


def test_an_entry_without_a_cover_proposes_no_artwork():
    """Absent means leave RomM alone. Half the Hub's entries have no cover
    and promoting a screenshot would fill a library with gameplay stills."""
    meta, _ = make_metadata(SNAKE)
    patch = meta.enrich(rom(name="Sneaky Snakes"))
    assert patch.artwork_url is None
    assert patch.has_artwork() is False
    assert patch.form_fields() == {"name": "Sneaky Snakes"}


def test_set_name_off_leaves_the_libraries_own_spelling():
    meta, _ = make_metadata(config={"set_name": False})
    patch = meta.enrich(rom(extra={"source_id": "johnybot_super-snake-off"}))
    assert patch.name is None
    assert patch.artwork_url is not None


def test_nothing_to_propose_is_a_refusal_rather_than_an_empty_patch():
    """An empty patch would have the host report an enrich that changed
    nothing, which reads as "the source had nothing" instead of "this
    plugin was configured not to offer the only thing it had"."""
    meta, _ = make_metadata(SNAKE, {"set_name": False})
    with pytest.raises(NoMatch, match="set_name"):
        meta.enrich(rom(name="Sneaky Snakes"))


def test_a_rom_with_no_name_falls_back_to_its_filename():
    meta, http = make_metadata(SNAKE)
    with pytest.raises(NoMatch):
        meta.enrich(rom(filename="Sonic_The_Hedgehog.gb"))
    assert http.calls[0][1]["q"] == "Sonic The Hedgehog"


def test_a_rom_with_neither_a_name_nor_a_filename_asks_for_a_source_id():
    meta, http = make_metadata(SNAKE)
    with pytest.raises(NoMatch, match="--source-id"):
        meta.enrich(rom())
    assert http.calls == []


def test_a_non_200_names_the_status():
    meta, _ = make_metadata(status=503)
    with pytest.raises(ApiFailed, match="503"):
        meta.enrich(rom(extra={"source_id": "johnybot_super-snake-off"}))


def test_the_artwork_url_is_on_the_declared_host():
    """The manifest allows exactly one host; the broker checks this URL
    against it before fetching. A cover elsewhere would be a policy
    violation at enrich time rather than a bug here."""
    meta, _ = make_metadata()
    patch = meta.enrich(rom(extra={"source_id": "johnybot_super-snake-off"}))
    assert patch.artwork_url.startswith("https://hh3.gbdev.io/static/")


def test_a_cover_stored_under_a_path_still_yields_a_bare_filename():
    payload = {
        "entries": [
            {
                "slug": "x",
                "title": "X",
                "basepath": "database-gb",
                "platform": "GB",
                "screenshots": ["screenshots/../../cover.png"],
                "files": [],
            }
        ],
        "page_total": 1,
    }
    meta, _ = make_metadata(payload)
    patch = meta.enrich(rom(extra={"source_id": "x"}))
    assert patch.artwork_filename == "cover.png"


def test_only_a_file_named_cover_counts_as_one():
    entry = parse_entry(
        {
            "slug": "x",
            "title": "X",
            "basepath": "database-gb",
            "screenshots": ["screenshot.png", "title.png"],
        }
    )
    assert entry.cover() is None
    entry = parse_entry(
        {
            "slug": "x",
            "title": "X",
            "basepath": "database-gb",
            "screenshots": ["screenshot.png", "cover.jpg"],
        }
    )
    assert entry.cover() == "cover.jpg"
