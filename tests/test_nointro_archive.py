"""Directory-index plugin, replayed against captured indexes.

`tests/fixtures/nointro_archive/` holds three verbatim captures:

* `archive_org_nointro_gg.html` — the head of a live Archive.org No-Intro
  item listing, which is what the plugin ships pointed at;
* `myrient_no_intro_game_boy.html` — a real Myrient index, taken from the
  Wayback Machine, because myrient.erista.me itself no longer serves one.
  The plugin no longer *goes* to Myrient (hence the `nointro-archive` name),
  but the parser that reads that layout is retained for the day a mirror
  reproduces it, and this fixture is what keeps it honest;
* `myrient_shutdown.html` — what myrient.erista.me answers *today* for
  every path: 200 OK and a static notice. That one is a test, not a
  curiosity — it is the failure mode a status-code check cannot see;
* `archive_org_atari_lynx_subdir.html` — the **subdirectory** listing at
  `archive.org/download/NoIntro-Atari/Atari - Lynx/`, captured whole
  (95 games) on 2026-08-01. It is the fixture for the shape that unlocked
  five machines: an uploader who put several systems in one item and
  named the folders after them. The item root holds no ROMs at all, so
  before this the Lynx, the Jaguar, the ColecoVision, the VIC-20 and the
  Plus/4 were unreachable however the plugin was configured.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "nointro-archive"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nointro_archive"
sys.path.insert(0, str(PLUGIN_ROOT))

from nointro_archive.filenames import safe_filename  # noqa: E402
from nointro_archive.importer import ImportRefused, Importer  # noqa: E402
from nointro_archive.index import INDEXES, IndexCache, IndexError_, parse_index, parse_size  # noqa: E402
from nointro_archive.platforms import platform_for  # noqa: E402
from nointro_archive.search import (  # noqa: E402
    ConfigError,
    Search,
    base_url,
    index_url,
    score,
    title_key,
)

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import FetchFile, SearchResult  # noqa: E402

ARCHIVE_ORG = (FIXTURES / "archive_org_nointro_gg.html").read_text(encoding="utf-8")
MYRIENT = (FIXTURES / "myrient_no_intro_game_boy.html").read_text(encoding="utf-8")
SHUTDOWN = (FIXTURES / "myrient_shutdown.html").read_text(encoding="utf-8")
LYNX = (FIXTURES / "archive_org_atari_lynx_subdir.html").read_text(encoding="utf-8")

LYNX_DIR = "NoIntro-Atari/Atari - Lynx"
LYNX_URL = "https://archive.org/download/NoIntro-Atari/Atari%20-%20Lynx/"

CONFIG = {
    "base_url": "https://archive.org/download/",
    "collections": ["nointro.gg"],
    "collection": "No-Intro",
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


ENTITY_INDEX = """
<table>
<tr><td><a href="Sonic%20%26amp%3B%20Knuckles%20(USA).zip">x</a></td><td>1.0M</td></tr>
<tr><td><a href="Ren%20%26amp%3B%20Stimpy%20%26lt%3BProto%26gt%3B.gb">x</a></td><td>512K</td></tr>
<tr><td><a href="Sonic%20&amp;%20Tails%20(Japan).zip">x</a></td><td>2.0M</td></tr>
<tr><td><a href="Plain%20Name%20(USA).zip">x</a></td><td>1.0M</td></tr>
</table>
"""


def test_html_entities_are_decoded_out_of_titles():
    """`Sonic &amp; Tails` is not a title; `Sonic & Tails` is.

    The entity survives percent-encoding (`%26amp%3B` holds no `&` for an
    unescape of the raw href to find), so it has to be unescaped again
    after `unquote` or it reaches the operator verbatim -- in the search
    listing, in the source id, and in the file written to disk.

    Every form is asserted together on purpose. The two that already
    worked are the ones Archive.org emits today, so without them here a
    later "simplification" back to a single unescape would pass.
    """
    names = [e.name for e in parse_index(ENTITY_INDEX)]
    assert "Sonic & Knuckles (USA).zip" in names
    assert "Ren & Stimpy <Proto>.gb" in names
    # The other encoding order -- escaped href, percent-encoded space --
    # already worked, and must keep working.
    assert "Sonic & Tails (Japan).zip" in names
    assert not any("&amp;" in n or "&lt;" in n for n in names)


def test_the_href_still_fetches_the_file_after_the_entity_fix():
    """Only the display name is unescaped. The href is what the server
    said, and it is what actually retrieves the bytes."""
    entry = next(e for e in parse_index(ENTITY_INDEX) if e.name.startswith("Sonic &"))
    assert entry.href == "Sonic%20%26amp%3B%20Knuckles%20(USA).zip"


def test_an_entity_title_sanitises_to_a_sensible_filename():
    """What the ROM lands on disk as. Before the fix this was
    `Sonic &amp_ Knuckles (USA).zip` -- `&` survives the allowlist and
    `;` does not."""
    entry = next(e for e in parse_index(ENTITY_INDEX) if e.name.startswith("Sonic &"))
    assert safe_filename(entry.name) == "Sonic & Knuckles (USA).zip"


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


# ------------------------------------------------ subdirectories of an item


def test_a_per_system_subdirectory_of_a_multi_system_item_is_a_directory():
    """`NoIntro-Atari` holds five machines in five folders and no ROMs.

    That layout is why five platforms were unreachable: the item root
    parses to nothing and the plugin does not walk into subdirectories.
    Naming the folder as the directory is the whole fix, and it needs the
    space in `Atari - Lynx` to survive into the URL.
    """
    assert platform_for(LYNX_DIR) == "lynx"
    assert index_url("https://archive.org/download/", LYNX_DIR) == LYNX_URL


def test_the_lynx_subdirectory_parses_as_an_ordinary_index():
    search, http = make_search(
        bodies={LYNX_URL: LYNX}, config={"collections": [LYNX_DIR]}
    )
    results = search.search("", None, 200)
    assert len(results) == 95, "the whole captured Lynx set"
    assert all(r.platform == "lynx" for r in results)
    assert http.calls == [LYNX_URL]


def test_every_atari_2600_directory_is_mapped_now_that_a_deduplicator_exists():
    """Three items carry an Atari 2600 set and all three are mapped.

    This assertion is the reverse of what it used to be, and the reversal
    is the point. The old rule kept `NoIntro-Atari/Atari - 2600` and
    `nointro-2600` out of the table because "mapping both would list every
    Atari game twice" -- true of a search that concatenates directories and
    prints the result.

    It stopped being true when the census landed. `rom_hub.grouping` merges
    on a matching sha1 *before* it consults a name, Archive.org publishes a
    sha1 for every file it holds, and `census.py` puts them in `extra`
    under the keys grouping reads. Measured against the live corpus,
    `nointro-2600` and `NoIntro-Atari` share 523 byte-identical archives.
    So the duplication is detected on evidence, and leaving 1,400 real
    files uncatalogued to avoid a merge the deduplicator performs anyway is
    incompleteness chosen on purpose.
    """
    assert platform_for("nointro.atari-2600") == "atari2600"
    assert platform_for("NoIntro-Atari/Atari - 2600") == "atari2600"
    assert platform_for("nointro-2600") == "atari2600"


def test_an_item_whose_title_lies_is_mapped_from_hash_evidence():
    """`NoIntroNintendo` is titled "No Intro - Nintendo" and is a Virtual
    Boy set: 31 of its 34 files are byte-identical with `NoIntroVirtualBoy`.
    A mapper reading the title would have filed it under the wrong thing or
    refused it; the census's hashes are what settled it."""
    assert platform_for("NoIntroNintendo") == "virtualboy"
    assert platform_for("NoIntroVirtualBoy") == "virtualboy"


def test_the_peripherals_stay_unmapped_and_that_is_still_deliberate():
    """Satellaview and Sufami Turbo are SNES peripherals with their own RomM
    slugs and no EmulatorJS core. The census catalogues their files -- they
    are real and they are counted -- but filing them under `snes` would be
    a remap onto hardware they are not, so they stay unmapped and the
    report lists them as such."""
    assert platform_for("NoIntro_Satellaview") is None
    assert platform_for("NoIntroSufamiTurbo") is None


# ------------------------------------------------------------------ ranking


def test_a_title_key_drops_regions_revisions_and_punctuation():
    assert title_key("Klax (USA, Europe) (Beta).zip") == "klax"
    assert title_key("Sonic The Hedgehog (USA, Europe) (Rev A).7z") == (
        "sonic the hedgehog"
    )
    # No extension, no brackets: still just words.
    assert title_key("Xenophobe") == "xenophobe"


def test_score_separates_exact_from_prefix_from_substring():
    assert score("Klax (USA, Europe).zip", "klax", ["klax"]) == 3
    assert score("Batman Returns (USA, Europe).zip", "batman", ["batman"]) == 2
    assert score(
        "Adventures of Batman & Robin, The (USA).7z", "batman", ["batman"]
    ) == 1
    # A browse scores everything the same, so ordering falls through to
    # the tie-breaks rather than to an invented relevance.
    assert score("anything at all.zip", "", []) == 1


def test_the_base_release_outranks_its_beta_even_though_beta_sorts_first():
    """Both score 3; the shorter name wins, and that is the useful answer.

    Alphabetically `Klax (USA, Europe) (Beta).zip` comes *before*
    `Klax (USA, Europe).zip`, so listing order hands an operator the beta
    first. Nothing about the old first-N-results walk could have done
    otherwise.
    """
    search, _ = make_search(
        bodies={LYNX_URL: LYNX}, config={"collections": [LYNX_DIR]}
    )
    titles = [r.title for r in search.search("klax", None, 2)]
    assert titles == ["Klax (USA, Europe).zip", "Klax (USA, Europe) (Beta).zip"]


def test_a_better_match_in_a_later_directory_beats_a_worse_one_first():
    """The bug this ranking exists to fix.

    The Game Gear index carries four `Adventures of Batman & Robin, The`
    betas, which merely *contain* the word. The Lynx index carries
    `Batman Returns`, which starts with it. Directory order used to
    decide, so a `--limit 2` search returned two betas and never opened
    the second directory.
    """
    gg_url = "https://archive.org/download/nointro.gg/"
    search, http = make_search(
        bodies={gg_url: ARCHIVE_ORG, LYNX_URL: LYNX},
        config={"collections": ["nointro.gg", LYNX_DIR]},
    )
    results = search.search("batman", None, 2)
    assert results[0].title == "Batman Returns (USA, Europe).zip"
    assert results[0].platform == "lynx"
    assert len(http.calls) == 2, "both directories were read before ranking"


def test_a_platform_less_search_is_bounded_by_max_directories():
    """Reading all 25 shipped indexes takes 34.8s; the host kills at 30."""
    bodies = {index_url("https://archive.org/download/", d): ARCHIVE_ORG
              for d in ("nointro.gg", "nointro.md", "nointro.32x", "nointro.ws")}
    search, http = make_search(
        bodies=bodies,
        config={
            "collections": ["nointro.gg", "nointro.md", "nointro.32x", "nointro.ws"],
            "max_directories": 2,
        },
    )
    search.search("nothing matches this", None, 50)
    assert len(http.calls) == 2


def test_a_platform_filter_ignores_the_directory_budget():
    """One platform is one index, so the ceiling has nothing to bound.

    This is what keeps every shipped set reachable: `max_directories`
    shapes a browse, never an answer to a precise question.
    """
    search, http = make_search(
        bodies={LYNX_URL: LYNX},
        config={
            "collections": ["nointro.gg", "nointro.md", LYNX_DIR],
            "max_directories": 1,
        },
    )
    results = search.search("", "lynx", 5)
    assert http.calls == [LYNX_URL]
    assert len(results) == 5


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
    assert plan.collection == "No-Intro"
    assert plan.files[0].url == (
        "https://archive.org/download/nointro.gg/"
        "5%20in%20One%20FunPak%20%28USA%29.7z"
    )
    assert plan.files[0].size_bytes == int(70.2 * 1024)


def test_an_entity_title_round_trips_from_search_to_a_plan():
    """The source id carries the *decoded* name, so the importer has to
    find the same entry the search offered -- and the URL it plans must
    still be the server's own href, not a re-encode of the pretty name."""
    search, _ = make_search(ENTITY_INDEX)
    result = next(r for r in search.search("sonic knuckles", None, 10))
    assert result.title == "Sonic & Knuckles (USA).zip"
    assert result.source_id == "nointro.gg/Sonic & Knuckles (USA).zip"

    importer, _ = make_importer(ENTITY_INDEX)
    plan = importer.plan(SearchResult(source_id=result.source_id, title="x"))
    assert plan.files[0].url == (
        "https://archive.org/download/nointro.gg/"
        "Sonic%20%26amp%3B%20Knuckles%20(USA).zip"
    )
    assert plan.files[0].filename == "Sonic & Knuckles (USA).zip"


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


def test_a_source_id_in_no_directory_this_plugin_knows_is_refused():
    """Refused before any request, and the message names both places a
    directory could have come from."""
    importer, http = make_importer()
    with pytest.raises(ImportRefused, match="does not name a file"):
        importer.plan(SearchResult(source_id="nowhere.at.all/Sonic.7z", title="x"))
    assert http.calls == []


def test_a_mapped_directory_imports_even_when_it_is_not_in_collections():
    """The Hub must not refuse to import a row it just catalogued.

    `rom-hub catalogue build` enumerates all 71 `identifier:nointro*` items;
    `collections` lists 25 directories. The split therefore falls back to
    the platform table, so a census row from an item nobody typed into a
    *search* config key is still importable. Still exact-match: an unmapped
    directory fails, as the test above shows.
    """
    importer, http = make_importer(
        bodies={"https://archive.org/download/nointro.md/": ARCHIVE_ORG},
        # `nointro.md` is deliberately absent from the configured list.
        config={"collections": ["nointro.gg"]},
    )
    directory, name = importer._split("nointro.md/5 in One FunPak (USA).7z")
    assert (directory, name) == ("nointro.md", "5 in One FunPak (USA).7z")


def test_the_longest_matching_directory_wins():
    """`NoIntro-Atari` and `NoIntro-Atari/Atari - Lynx` are both real
    directories and the first is a prefix of the second. Matching the short
    one on a Lynx source id would leave a name with a slash in it."""
    importer, _ = make_importer(config={"collections": []})
    assert importer._split("NoIntro-Atari/Atari - Lynx/Klax (USA).zip") == (
        "NoIntro-Atari/Atari - Lynx",
        "Klax (USA).zip",
    )


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
