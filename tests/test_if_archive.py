"""The if-archive plugin, replayed against captured IF Archive indexes.

**Where the fixtures come from.** `tests/fixtures/if_archive/` holds real
`ifarchive.org` index pages captured on 2026-07-31. `hugo.html` is a whole
directory index, byte for byte (42 files, 28,988 bytes). The other four are
*slices*: the header, the description block, the subdirectory list and the
footer are verbatim, and so is every `<dt>` entry kept -- only the
selection is ours, because `zcode` alone is 491,887 bytes and 811 files.
Each kept entry is there for something a test below asserts:

* `905.z5` and `curses.z5` -- ordinary Z-code, one with a multi-line blurb
* `905notes.txt`, `9Dancers.zip`, `Enhanced.tar.Z` -- not story files
* `Acheton.z8` -- carries a `SymLinkRef` block after its description
* `Escape%21.zblorb`, `Ancient%20Treasure%2C%20Secret%20Spider.zblorb`,
  `Apollo18%2B20.zip` -- percent-encoded hrefs whose `id` attributes are
  escaped a *different* way (`=21=`, `=2B=`)
* `The%20Cruel%20Count%27s%20Castle.gblorb` -- a **Glulx** file filed under
  `zcode`, which is why the extension decides and the directory does not
* `zenspeak.blb`, `glkebook.blb`, `advent.blb` -- Blorb containers, one of
  which is a sound-resource file and one an eBook reader
* `3monkeys.taf` -- ADRIFT, a real format RomM has no platform for

`not_found.html` is the archive's real 404 body.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "if-archive"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "if_archive"
sys.path.insert(0, str(PLUGIN_ROOT))

from if_archive.formats import (  # noqa: E402
    FORMATS,
    PLATFORMS,
    extension_of,
    format_for,
)
from if_archive.importer import ImportRefused, Importer, download_url  # noqa: E402
from if_archive.index import (  # noqa: E402
    DEFAULT_DIRECTORIES,
    Index,
    IndexUnavailable,
    archive_path,
    clean_directory,
    parse_index,
)
from if_archive.search import Search, normalise  # noqa: E402

from rom_hub.types import FetchPlan, SearchResult, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


ZCODE = fixture("zcode.html")
GLULX = fixture("glulx.html")
TADS = fixture("tads.html")
HUGO = fixture("hugo.html")
ADRIFT = fixture("adrift.html")
NOT_FOUND = fixture("not_found.html")

PAGES = {
    "https://ifarchive.org/indexes/if-archive/games/zcode/": ZCODE,
    "https://ifarchive.org/indexes/if-archive/games/glulx/": GLULX,
    "https://ifarchive.org/indexes/if-archive/games/tads/": TADS,
    "https://ifarchive.org/indexes/if-archive/games/hugo/": HUGO,
    "https://ifarchive.org/indexes/if-archive/games/adrift/": ADRIFT,
}


class FakeHttp:
    """A `ctx.http` that answers from the captured pages and counts calls."""

    def __init__(self, pages=None, status=200):
        self.pages = dict(PAGES if pages is None else pages)
        self.status = status
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        if url in self.pages:
            return HttpResponse(status_code=self.status, text=self.pages[url])
        return HttpResponse(status_code=404, text=NOT_FOUND)


def context(config=None, http=None) -> PluginContext:
    return PluginContext(config=dict(config or {}), http=http or FakeHttp())


# ------------------------------------------------------- the format table


def test_the_four_mapped_runtimes_are_the_four_romm_has_slugs_for():
    """RomM 4.9.2's supported-platform list carries exactly these four IF
    runtimes, and this plugin names no others."""
    assert PLATFORMS == {"z-machine", "glulx", "tads", "hugo"}


def test_z_machine_and_glulx_are_not_collapsed():
    assert format_for("905.z5").platform == "z-machine"
    assert format_for("advent.ulx").platform == "glulx"
    assert format_for("game.zblorb").platform == "z-machine"
    assert format_for("game.gblorb").platform == "glulx"


def test_every_extension_romm_issue_2140_names_is_known():
    """The issue this plugin answers lists the formats Parchment plays."""
    for extension in ("z3", "z4", "z5", "z8", "zlb", "zblorb"):
        assert FORMATS[extension].platform == "z-machine"
    for extension in ("ulx", "glb", "gblorb"):
        assert FORMATS[extension].platform == "glulx"
    for extension in ("gam", "t3"):
        assert FORMATS[extension].platform == "tads"
    assert FORMATS["hex"].platform == "hugo"
    # The issue asks for ADRIFT 4 too. It is known, and it has no slug.
    assert FORMATS["taf"].platform is None


def test_an_unmapped_format_is_known_rather_than_invisible():
    blorb = format_for("advent.blb")
    assert blorb is not None
    assert blorb.platform is None
    assert "ZCOD" not in blorb.unmapped_reason  # it names chunks in words
    assert "Blorb container" in blorb.unmapped_reason


def test_a_file_that_is_not_a_story_file_has_no_format():
    for name in ("905notes.txt", "9Dancers.zip", "map.pdf", "Enhanced.tar.Z"):
        assert format_for(name) is None


def test_the_extension_is_the_last_one_not_the_first():
    assert extension_of("Enhanced.tar.Z") == "z"
    assert extension_of("A.Mind.Forever.z5") == "z5"
    assert extension_of("how_to_play_these_games") == ""


# ------------------------------------------------------------- the parser


def test_the_href_is_the_filename_and_the_id_is_not():
    """`<dt id="Apollo18=2B=20.zip">` and `href=".../Apollo18%2B20.zip"`
    are two different escapings of one name, and only one of them is a
    filename. Reading the id would produce `Apollo18+20` for a file the
    archive calls `Apollo18+20.zip` -- and `The=20=Cruel=20=Count=27=s`
    round-trips through nothing at all."""
    names = {e.filename for e in parse_index(ZCODE, "zcode")}
    assert "Apollo18+20.zip" in names
    assert "The Cruel Count's Castle.gblorb" in names
    assert not any("=2B=" in name or "=27=" in name for name in names)


def test_percent_decoded_names_are_all_legal_rom_filenames():
    """The bug this guards against is the one that dropped every GoodTools
    `[!]` name elsewhere in this project: narrowing the plugin's own idea
    of a filename instead of decoding. Decoded, every one of these passes
    the host's validator untouched."""
    encoded = [
        e for e in parse_index(ZCODE, "zcode") if "%" in e.path or " " in e.filename
    ]
    assert encoded, "the fixture is supposed to carry percent-encoded hrefs"
    for entry in encoded:
        assert bare_filename(entry.filename) == entry.filename


def test_the_url_round_trips_from_the_decoded_path():
    entry = _by_name(ZCODE, "zcode", "Escape!.zblorb")
    assert entry.url == (
        "https://ifarchive.org/if-archive/games/zcode/Escape%21.zblorb"
    )


def test_dates_and_the_archives_own_descriptions_are_read():
    entry = _by_name(ZCODE, "zcode", "905.z5")
    assert entry.date == "02-Aug-2012"
    assert entry.description.startswith("9:05 by Adam Cadre")
    # Entities unescaped, tags stripped, whitespace collapsed to one line.
    assert "<" not in entry.description and "\n" not in entry.description


def test_only_the_prose_dd_is_read_as_the_description():
    """An entry carries several `<dd>` blocks -- an IFDB link, an IFWiki
    link, a `[linked from ...]` symlink note. Only the `<p>` one is prose
    about the game."""
    entry = _by_name(ZCODE, "zcode", "Acheton.z8")
    assert entry.description.startswith("Acheton, by David Seal")
    assert "linked from" not in entry.description
    assert "ifdb.org" not in entry.description


def test_a_whole_real_index_parses_and_every_hugo_story_file_maps():
    entries = parse_index(HUGO, "hugo")
    assert len(entries) == 42
    story = [e for e in entries if format_for(e.filename)]
    assert len(story) == 21
    assert {format_for(e.filename).platform for e in story} == {"hugo"}


def test_a_document_that_is_not_an_index_is_refused_not_read_as_empty():
    """Reading zero files out of an unrecognised page looks exactly like an
    empty directory, and would turn any upstream change into a silent total
    failure."""
    with pytest.raises(IndexUnavailable) as exc:
        parse_index(NOT_FOUND, "zcode")
    assert "filelist" in str(exc.value)


def test_cross_tree_links_in_an_entry_are_not_read_as_files():
    """Descriptions link to `games/pc/905.exe` and `games/source/inform/`.
    The parser anchors on the entry's own `<dt>` anchor, so those never
    become entries -- and anything outside `if-archive/games/` is refused
    by `archive_path` anyway."""
    for entry in parse_index(ZCODE, "zcode"):
        assert entry.path.startswith("if-archive/games/zcode/")


# -------------------------------------------------------- path validation


def test_a_path_outside_the_games_tree_is_refused():
    for bad in (
        "if-archive/programming/inform6/library.zip",
        "if-archive/games",
        "https://evil.example/x.z5",
        "../../etc/passwd",
    ):
        with pytest.raises(IndexUnavailable):
            archive_path(bad)


def test_traversal_and_separators_are_refused_after_decoding():
    for bad in (
        "if-archive/games/zcode/../../../etc/passwd",
        "if-archive/games/zcode/%2E%2E/%2E%2E/secret",
        "if-archive/games/zcode/C:evil.z5",
        "if-archive/games/zcode/sub\\evil.z5",
        "if-archive/games/zcode//double.z5",
    ):
        with pytest.raises(IndexUnavailable):
            archive_path(bad)


def test_an_encoded_and_a_decoded_path_are_the_same_path():
    assert archive_path("/if-archive/games/zcode/Escape%21.zblorb") == archive_path(
        "if-archive/games/zcode/Escape!.zblorb"
    )


def test_a_directory_name_is_an_allowlist_not_a_denylist():
    assert clean_directory("zcode") == "zcode"
    assert clean_directory("/zcode/german/") == "zcode/german"
    for bad in ("", "..", "zcode/../..", "zcode;rm", "zcode?x=1", "//"):
        with pytest.raises(IndexUnavailable):
            clean_directory(bad)


# ------------------------------------------------------------------ index


def test_an_index_is_fetched_once_per_process():
    http = FakeHttp()
    index = Index(http)
    index.entries("zcode")
    index.entries("zcode")
    index.entries("zcode")
    assert http.calls == ["https://ifarchive.org/indexes/if-archive/games/zcode/"]


def test_a_non_200_index_raises_rather_than_returning_nothing():
    index = Index(FakeHttp(pages={}))
    with pytest.raises(IndexUnavailable) as exc:
        index.entries("zcode")
    assert "404" in str(exc.value)


# ----------------------------------------------------------------- search


def test_camel_case_is_split_before_the_case_is_folded():
    assert normalise("HouseOfDreamOfMoon") == "house of dream of moon"
    assert normalise("A_Beauty_Cold_and_Austere") == "a beauty cold and austere"
    assert normalise("905.z5") == "905 z5"


def test_a_search_reads_the_four_default_directories():
    http = FakeHttp()
    Search(context(http=http)).search("advent", None, 50)
    assert len(http.calls) == len(DEFAULT_DIRECTORIES) == 4


def test_a_second_search_in_one_process_costs_no_request():
    http = FakeHttp()
    search = Search(context(http=http))
    search.search("advent", None, 50)
    before = len(http.calls)
    search.search("cook", None, 50)
    assert len(http.calls) == before


def test_terms_may_arrive_in_any_order():
    search = Search(context())
    forward = search.search("beauty cold austere", None, 50)
    backward = search.search("austere beauty", None, 50)
    assert [r.source_id for r in forward] == [r.source_id for r in backward]
    assert forward and forward[0].title == "A_Beauty_Cold_and_Austere.gblorb"


def test_results_carry_the_runtime_and_the_archives_description():
    (result,) = [
        r for r in Search(context()).search("905", None, 50) if r.title == "905.z5"
    ]
    assert result.platform == "z-machine"
    assert result.extra["runtime"] == "Z-machine"
    assert result.extra["directory"] == "zcode"
    assert result.extra["date"] == "02-Aug-2012"
    assert result.extra["description"].startswith("9:05 by Adam Cadre")
    assert result.source_id == "if-archive/games/zcode/905.z5"


def test_non_story_files_never_appear_in_results():
    titles = {r.title for r in Search(context()).search("", None, 200)}
    for name in ("905notes.txt", "9Dancers.zip", "Enhanced.tar.Z"):
        assert name not in titles


def test_a_known_format_with_no_platform_still_appears_with_none():
    """Hiding a game somebody can see on the archive's own site would be
    worse than showing why it will not import."""
    results = Search(context({"directories": ["glulx"]})).search("advent", None, 50)
    blorb = [r for r in results if r.title == "advent.blb"]
    assert blorb and blorb[0].platform is None
    assert blorb[0].extra["runtime"] == "Blorb"


def test_a_glulx_file_filed_under_zcode_is_reported_as_glulx():
    """The extension is the format. The directory is where somebody put
    it, which is not the same claim."""
    (result,) = [
        r
        for r in Search(context({"directories": ["zcode"]})).search("cruel", None, 50)
        if r.title.endswith(".gblorb")
    ]
    assert result.extra["directory"] == "zcode"
    assert result.platform == "glulx"


def test_the_description_is_a_fallback_and_says_so():
    """`Zen Speaks` appears only in a description, never in a filename."""
    results = Search(context({"directories": ["zcode"]})).search("zen speaks", None, 50)
    assert results and all(r.extra["matched_on"] == "description" for r in results)


def test_a_description_match_never_pads_out_a_filename_match():
    """Found live: the archive's blurbs carry Inform serial numbers, so
    `905` matched `905.z5` on its name and then four unrelated games on
    "Serial number 990905". A fallback that fills the remaining room is
    not a fallback."""
    results = Search(context({"directories": ["zcode"]})).search("905", None, 50)
    assert [r.title for r in results] == ["905.z5"]
    assert results[0].extra["matched_on"] == "filename"


def test_a_platform_filter_selects_one_runtime():
    results = Search(context()).search("", "hugo", 200)
    assert results and {r.platform for r in results} == {"hugo"}


def test_a_platform_this_source_has_nothing_for_costs_no_request():
    http = FakeHttp()
    assert Search(context(http=http)).search("mario", "snes", 50) == []
    assert http.calls == []


def test_the_limit_is_honoured():
    assert len(Search(context()).search("", None, 3)) == 3


def test_too_many_directories_is_refused_before_any_request():
    http = FakeHttp()
    search = Search(context({"directories": [f"d{i}" for i in range(20)]}, http))
    with pytest.raises(IndexUnavailable) as exc:
        search.search("x", None, 10)
    assert "at most" in str(exc.value)
    assert http.calls == []


# --------------------------------------------------------------- importer


def test_a_plan_costs_no_http_request_at_all():
    http = FakeHttp()
    plan = Importer(context(http=http)).plan(
        SearchResult(source_id="if-archive/games/zcode/905.z5", title="905.z5")
    )
    assert http.calls == []
    assert isinstance(plan, FetchPlan)
    assert plan.platform == "z-machine"
    assert plan.collection == "IF Archive"
    assert plan.files[0].filename == "905.z5"
    assert plan.files[0].url == "https://ifarchive.org/if-archive/games/zcode/905.z5"


def test_a_percent_encoded_file_plans_a_bare_decoded_filename():
    plan = Importer(context()).plan(
        SearchResult(
            source_id="if-archive/games/zcode/Escape%21.zblorb", title="Escape!.zblorb"
        )
    )
    assert plan.files[0].filename == "Escape!.zblorb"
    assert plan.files[0].url.endswith("/Escape%21.zblorb")


def test_every_story_file_in_every_fixture_plans_or_refuses_by_name():
    """No silent skips: each of the captured entries either produces a plan
    whose filename the host accepts, or a refusal that names the file."""
    importer = Importer(context())
    planned = refused = 0
    for page, directory in ((ZCODE, "zcode"), (GLULX, "glulx"), (TADS, "tads")):
        for entry in parse_index(page, directory):
            result = SearchResult(source_id=entry.path, title=entry.filename)
            try:
                plan = importer.plan(result)
            except ImportRefused as exc:
                assert entry.filename in str(exc)
                refused += 1
            else:
                assert plan.files[0].filename == entry.filename
                planned += 1
    assert planned and refused


def test_an_unmapped_format_refuses_and_names_the_format():
    with pytest.raises(ImportRefused) as exc:
        Importer(context()).plan(
            SearchResult(
                source_id="if-archive/games/adrift/3monkeys.taf", title="3monkeys.taf"
            )
        )
    message = str(exc.value)
    assert "3monkeys.taf" in message
    assert "ADRIFT" in message
    assert "no platform slug" in message
    assert "needs mapping" in message


def test_a_blorb_refuses_because_the_container_does_not_say_which_runtime():
    with pytest.raises(ImportRefused) as exc:
        Importer(context()).plan(
            SearchResult(
                source_id="if-archive/games/glulx/advent.blb", title="advent.blb"
            )
        )
    assert "Blorb container" in str(exc.value)


def test_a_zip_refuses_rather_than_importing_a_bundle_as_a_rom():
    with pytest.raises(ImportRefused) as exc:
        Importer(context()).plan(
            SearchResult(
                source_id="if-archive/games/zcode/9Dancers.zip", title="9Dancers.zip"
            )
        )
    assert "not an interactive fiction story file" in str(exc.value)


def test_a_path_outside_the_games_tree_refuses():
    for bad in (
        "if-archive/programming/inform6/lib.zip",
        "https://evil.example/x.z5",
        "if-archive/games/zcode/../../../etc/passwd",
    ):
        with pytest.raises(ImportRefused):
            Importer(context()).plan(SearchResult(source_id=bad, title="x"))


def test_an_empty_source_id_refuses_with_an_example():
    with pytest.raises(ImportRefused) as exc:
        Importer(context()).plan(SearchResult(source_id=" ", title="x"))
    assert "if-archive/games/zcode/905.z5" in str(exc.value)


def test_the_collection_is_configurable():
    plan = Importer(context({"collection": "Text Adventures"})).plan(
        SearchResult(source_id="if-archive/games/hugo/spur.hex", title="spur.hex")
    )
    assert plan.collection == "Text Adventures"
    assert plan.platform == "hugo"


def test_the_download_url_is_built_from_the_path_not_from_the_index():
    assert download_url("if-archive/games/tads/Cook-Off.t3") == (
        "https://ifarchive.org/if-archive/games/tads/Cook-Off.t3"
    )


# --------------------------------------------------------------- manifest


def test_the_manifest_declares_the_redirect_target():
    """`ifarchive.org` 302s a Glulx, TADS or Hugo download to
    `ukrestrict.ifarchive.org`, and the Hub re-checks every hop. A manifest
    without the second host would import Z-code and nothing else."""
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import url_allowed

    manifest = parse_manifest((PLUGIN_ROOT / "manifest.toml").read_text("utf-8"))
    assert manifest.network == ["ifarchive.org", "ukrestrict.ifarchive.org"]
    assert url_allowed(
        "https://ukrestrict.ifarchive.org/if-archive/games/hugo/spur.hex",
        manifest.network,
    )
    # ifdb.org is never requested, so it is not declared.
    assert not url_allowed("https://ifdb.org/viewgame?id=x", manifest.network)


def test_every_planned_url_is_inside_the_declared_allowlist():
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import url_allowed

    manifest = parse_manifest((PLUGIN_ROOT / "manifest.toml").read_text("utf-8"))
    importer = Importer(context())
    for entry in parse_index(HUGO, "hugo"):
        if format_for(entry.filename) is None:
            continue
        plan = importer.plan(SearchResult(source_id=entry.path, title=entry.filename))
        assert url_allowed(plan.files[0].url, manifest.network)


# ------------------------------------------------------------------ tools


def _by_name(page: str, directory: str, filename: str):
    (entry,) = [e for e in parse_index(page, directory) if e.filename == filename]
    return entry
