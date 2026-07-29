"""Directory-index plugin, replayed against captured indexes.

`tests/fixtures/myrient/` holds three verbatim captures:

* `archive_org_nointro_gg.html` — the head of a live Archive.org No-Intro
  item listing, which is what the plugin ships pointed at;
* `myrient_no_intro_game_boy.html` — a real Myrient index, taken from the
  Wayback Machine, because myrient.erista.me itself no longer serves one;
* `myrient_shutdown.html` — what myrient.erista.me answers *today* for
  every path: 200 OK and a static notice. That one is a test, not a
  curiosity — it is the failure mode a status-code check cannot see.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "myrient"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "myrient"
sys.path.insert(0, str(PLUGIN_ROOT))

from myrient.filenames import safe_filename  # noqa: E402
from myrient.importer import ImportRefused, Importer  # noqa: E402
from myrient.index import INDEXES, IndexCache, IndexError_, parse_index, parse_size  # noqa: E402
from myrient.platforms import platform_for  # noqa: E402
from myrient.search import ConfigError, Search, base_url, index_url  # noqa: E402

from romm_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from romm_hub.types import FetchFile, SearchResult  # noqa: E402

ARCHIVE_ORG = (FIXTURES / "archive_org_nointro_gg.html").read_text(encoding="utf-8")
MYRIENT = (FIXTURES / "myrient_no_intro_game_boy.html").read_text(encoding="utf-8")
SHUTDOWN = (FIXTURES / "myrient_shutdown.html").read_text(encoding="utf-8")

CONFIG = {
    "base_url": "https://archive.org/download/",
    "collections": ["nointro.gg"],
    "collection": "Myrient",
}


class FakeHttp:
    def __init__(self, bodies, status=200):
        # bodies: a str for every URL, or a {url: str} mapping.
        self.bodies = bodies
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        body = self.bodies if isinstance(self.bodies, str) else self.bodies.get(url, "")
        return HttpResponse(status_code=self.status, text=body)


@pytest.fixture(autouse=True)
def _clear_index_cache():
    # The cache is process-wide by design; tests must not inherit each
    # other's fetches.
    INDEXES.clear()
    yield
    INDEXES.clear()


def make_search(bodies=ARCHIVE_ORG, config=None, status=200):
    http = FakeHttp(bodies, status)
    return Search(PluginContext(config={**CONFIG, **(config or {})}, http=http)), http


def make_importer(bodies=ARCHIVE_ORG, config=None):
    http = FakeHttp(bodies)
    return Importer(PluginContext(config={**CONFIG, **(config or {})}, http=http)), http


# ---------------------------------------------------------------- index parse


def test_archive_org_rows_become_entries():
    entries = parse_index(ARCHIVE_ORG)
    names = [e.name for e in entries]
    assert "5 in One FunPak (USA).7z" in names
    assert all(not e.is_dir for e in entries)


def test_archive_orgs_view_contents_twin_is_not_a_second_entry():
    entries = parse_index(ARCHIVE_ORG)
    assert len(entries) == len({e.name for e in entries})
    assert not any(e.name.endswith(".7z/") for e in entries)


def test_the_parent_directory_link_is_not_an_entry():
    for document in (ARCHIVE_ORG, MYRIENT):
        assert all(e.name not in ("..", ".") for e in parse_index(document))


def test_sort_links_are_not_entries():
    # Myrient's header row is four "?C=N&O=A" links.
    assert all("C=N" not in e.href for e in parse_index(MYRIENT))


def test_a_real_myrient_index_parses_too():
    entries = parse_index(MYRIENT)
    names = [e.name for e in entries]
    assert "10-Pin Bowling (USA) (Proto).zip" in names
    assert "4 in 1 (Europe) (4B-001, Sachen-Commin) (Unl).zip" in names


def test_percent_encoding_is_decoded_for_the_name_and_kept_for_the_href():
    entry = next(
        e for e in parse_index(MYRIENT) if e.name.startswith("10-Pin Bowling")
    )
    assert entry.name == "10-Pin Bowling (USA) (Proto).zip"
    assert entry.href == "10-Pin%20Bowling%20%28USA%29%20%28Proto%29.zip"


@pytest.mark.parametrize(
    "tail,expected",
    [
        ("<td>26-Jan-2016 00:38</td><td>1.0M</td>", 1024**2),
        ('<td class="size">35.9 KiB</td>', int(35.9 * 1024)),
        ("<td>898.0B</td>", 898),
        ('<td class="size">-</td><td class="date">01-Dec-2025 19:50</td>', None),
        ("", None),
    ],
)
def test_sizes_are_read_from_either_dialect(tail, expected):
    assert parse_size(tail) == expected


def test_a_date_alone_is_not_read_as_a_size():
    assert parse_size("<td>26-Jan-2016 00:38</td>") is None


def test_archive_org_metadata_files_are_not_payloads():
    entries = {e.name: e for e in parse_index(ARCHIVE_ORG)}
    meta = next(n for n in entries if n.endswith("_meta.xml"))
    assert not entries[meta].is_payload


# ------------------------------------------------ the mirror that is not gone


def test_the_myrient_shutdown_page_is_reported_as_not_an_index():
    """myrient.erista.me answers 200 for every path it ever served.

    A plugin that trusted the status code would report "no results"
    forever. This is the whole reason the plugin ships pointed elsewhere.
    """
    search, _ = make_search(SHUTDOWN)
    with pytest.raises(IndexError_, match="not a directory index"):
        search.search("tetris", None, 5)


def test_a_non_200_index_names_the_status():
    search, _ = make_search(ARCHIVE_ORG, status=503)
    with pytest.raises(IndexError_, match="503"):
        search.search("x", None, 5)


# --------------------------------------------------------------- result shape


def test_results_carry_directory_platform_size_and_url():
    search, _ = make_search()
    result = next(r for r in search.search("FunPak", None, 25))
    assert result.source_id == "nointro.gg/5 in One FunPak (USA).7z"
    assert result.platform == "gamegear"
    assert result.size_bytes == int(70.2 * 1024)
    assert result.url == (
        "https://archive.org/download/nointro.gg/"
        "5%20in%20One%20FunPak%20%28USA%29.7z"
    )
    assert result.extra["directory"] == "nointro.gg"


def test_query_terms_must_all_match():
    search, _ = make_search()
    assert [r.title for r in search.search("batman robin", None, 25)]
    assert search.search("batman nonexistentword", None, 25) == []


def test_metadata_files_never_reach_results():
    search, _ = make_search()
    assert all(
        not r.title.endswith(("_meta.xml", "_files.xml", "_archive.torrent"))
        for r in search.search("", None, 50)
    )


def test_limit_is_honoured():
    search, _ = make_search()
    assert len(search.search("", None, 2)) == 2


# ------------------------------------------------------------------- caching


def test_a_directory_is_fetched_once_per_process():
    search, http = make_search()
    search.search("funpak", None, 5)
    search.search("batman", None, 5)
    assert http.calls == ["https://archive.org/download/nointro.gg/"]


def test_the_importer_reuses_the_index_the_search_already_fetched():
    http = FakeHttp(ARCHIVE_ORG)
    ctx = PluginContext(config=CONFIG, http=http)
    Search(ctx).search("funpak", None, 5)
    plan = Importer(ctx).plan(
        SearchResult(source_id="nointro.gg/5 in One FunPak (USA).7z", title="x")
    )
    assert plan.files[0].filename == "5 in One FunPak (USA).7z"
    assert len(http.calls) == 1


def test_the_cache_is_bounded_and_evicts_oldest_first():
    cache = IndexCache(max_indexes=2)
    http = FakeHttp(ARCHIVE_ORG)
    for url in ("https://h/a/", "https://h/b/", "https://h/c/"):
        cache.get(http, url)
    cache.get(http, "https://h/a/")
    # a evicted by c, so a is fetched twice: 4 fetches for 3 directories.
    assert cache.fetches == 4


def test_a_platform_filter_skips_directories_before_fetching_them():
    search, http = make_search(
        config={"collections": ["nointro.gg", "nointro.md"]}
    )
    search.search("", "genesis", 5)
    assert http.calls == ["https://archive.org/download/nointro.md/"]


def test_the_walk_stops_once_the_limit_is_reached():
    search, http = make_search(config={"collections": ["nointro.gg", "nointro.md"]})
    search.search("", None, 1)
    assert len(http.calls) == 1


# --------------------------------------------------------- platform mapping


@pytest.mark.parametrize(
    "directory,slug",
    [
        ("nointro.gg", "gamegear"),
        ("nointro.sg", "supergrafx"),
        ("nointro.ms-mkiii", "sms"),
        ("nointro.ca", "amiga"),
        ("No-Intro/Nintendo - Game Boy", "gb"),
        ("No-Intro/Nintendo - Game Boy/", "gb"),
    ],
)
def test_known_directories_map(directory, slug):
    assert platform_for(directory) == slug


def test_an_unmapped_directory_is_not_guessed_from_its_family():
    # A prefix rule over "nointro.*" would answer here; that it does not is
    # the point.
    assert platform_for("nointro.saturn") is None
    assert platform_for("") is None


def test_search_refuses_an_unmapped_directory_before_any_request():
    search, http = make_search(config={"collections": ["nointro.saturn"]})
    with pytest.raises(ConfigError, match="needs mapping"):
        search.search("x", None, 5)
    assert http.calls == []


def test_the_importer_refuses_an_unmapped_directory_and_names_it():
    importer, http = make_importer(config={"collections": ["nointro.saturn"]})
    with pytest.raises(ImportRefused, match="nointro.saturn"):
        importer.plan(SearchResult(source_id="nointro.saturn/Game.7z", title="x"))
    assert http.calls == []


def test_no_collections_configured_is_a_config_error():
    search, _ = make_search(config={"collections": []})
    with pytest.raises(ConfigError, match="no collections configured"):
        search.search("x", None, 5)


# ------------------------------------------------------------------- base_url


def test_base_url_is_normalised_to_a_trailing_slash():
    assert base_url("https://archive.org/download") == "https://archive.org/download/"


def test_a_non_https_base_url_is_refused_with_the_reason():
    with pytest.raises(ConfigError, match="https"):
        base_url("http://archive.org/download/")


def test_a_multi_segment_directory_keeps_its_slashes_but_encodes_spaces():
    assert index_url("https://m/files/", "No-Intro/Nintendo - Game Boy") == (
        "https://m/files/No-Intro/Nintendo%20-%20Game%20Boy/"
    )


# ------------------------------------------------------------------ planning


def test_a_plan_points_at_the_hrefs_the_server_printed():
    importer, _ = make_importer()
    plan = importer.plan(
        SearchResult(source_id="nointro.gg/5 in One FunPak (USA).7z", title="x")
    )
    assert plan.platform == "gamegear"
    assert plan.collection == "Myrient"
    assert plan.files[0].url == (
        "https://archive.org/download/nointro.gg/"
        "5%20in%20One%20FunPak%20%28USA%29.7z"
    )
    assert plan.files[0].size_bytes == int(70.2 * 1024)


def test_a_file_missing_from_the_index_is_refused_rather_than_fetched():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused, match="not in the directory index"):
        importer.plan(
            SearchResult(source_id="nointro.gg/Renamed Last Year.7z", title="x")
        )


def test_metadata_files_are_refused_even_when_asked_for_by_name():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused, match="bookkeeping"):
        importer.plan(
            SearchResult(source_id="nointro.gg/nointro.gg_meta.xml", title="x")
        )


def test_a_source_id_outside_the_configured_directories_is_refused():
    importer, http = make_importer()
    with pytest.raises(ImportRefused, match="does not name a file"):
        importer.plan(SearchResult(source_id="nointro.md/Sonic.7z", title="x"))
    assert http.calls == []


def test_an_empty_source_id_is_refused():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused, match="no source id"):
        importer.plan(SearchResult(source_id="   ", title="x"))


def test_a_multi_segment_directory_splits_on_the_configured_prefix():
    importer, http = make_importer(
        bodies={
            "https://myrient.example/files/No-Intro/Nintendo%20-%20Game%20Boy/": MYRIENT
        },
        config={
            "base_url": "https://myrient.example/files/",
            "collections": ["No-Intro/Nintendo - Game Boy"],
        },
    )
    plan = importer.plan(
        SearchResult(
            source_id="No-Intro/Nintendo - Game Boy/10-Pin Bowling (USA) (Proto).zip",
            title="x",
        )
    )
    assert plan.platform == "gb"
    assert plan.files[0].filename == "10-Pin Bowling (USA) (Proto).zip"


# ------------------------------------------------------- filename sanitising


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5 in One FunPak (USA).7z", "5 in One FunPak (USA).7z"),
        ("Adventures of Batman & Robin, The (USA, Europe).7z",
         "Adventures of Batman & Robin, The (USA, Europe).7z"),
        ("dir/Game.7z", "Game.7z"),
        ("C:evil.7z", "C_evil.7z"),
        ("LPT1.zip", "_LPT1.zip"),
        ("..", "rom.zip"),
        ("Game.zip ", "Game.zip"),
    ],
)
def test_sanitised_names_are_accepted_by_the_host(raw, expected):
    name = safe_filename(raw, fallback="rom.zip")
    assert name == expected
    FetchFile(url="https://archive.org/x", filename=name)


def test_every_name_in_a_real_index_survives_sanitising():
    for entry in parse_index(MYRIENT) + parse_index(ARCHIVE_ORG):
        FetchFile(
            url="https://archive.org/x",
            filename=safe_filename(entry.name, fallback="rom.zip"),
        )
