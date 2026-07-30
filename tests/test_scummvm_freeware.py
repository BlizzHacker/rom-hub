"""ScummVM freeware plugin, replayed against captured download listings.

`tests/fixtures/scummvm_freeware/` holds five verbatim Apache indexes from
`downloads.scummvm.org/frs/extras/`. Four are freeware games; the fifth,
`Blade Runner/`, is **not** -- it holds a subtitle pack for a game still on
sale, and it is checked in precisely so a test can prove the plugin cannot
reach it.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "scummvm-freeware"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "scummvm_freeware"
sys.path.insert(0, str(PLUGIN_ROOT))

from scummvm_freeware.downloads import (  # noqa: E402
    DownloadsError,
    directory_url,
    file_url,
    is_payload,
    parse_listing,
)
from scummvm_freeware.games import GAMES, game_for, slug_for_directory  # noqa: E402
from scummvm_freeware.importer import ImportRefused, Importer  # noqa: E402
from scummvm_freeware.search import Search  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.types import SearchResult  # noqa: E402


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


BASS = fixture("beneath_a_steel_sky.html")
SOLTYS = fixture("soltys.html")
DREAMWEB = fixture("dreamweb.html")
MYSTERY_HOUSE = fixture("mystery_house.html")
BLADE_RUNNER = fixture("blade_runner.html")

PAGES = {
    "Beneath a Steel Sky": BASS,
    "Soltys": SOLTYS,
    "Dreamweb": DREAMWEB,
    "Mystery House": MYSTERY_HOUSE,
    "Blade Runner": BLADE_RUNNER,
}


#: A real index with no files in it, for directories a test does not care
#: about. Not `""` and not a 404 body: both of those are separately a
#: refusal, and a test about bounding should not depend on either.
EMPTY_INDEX = (
    "<html><head><title>Index of /frs/extras/x</title></head><body>"
    "<h1>Index of /frs/extras/x</h1><pre>"
    '<a href="/frs/extras/">Parent Directory</a>   -\n'
    "</pre></body></html>"
)


class FakeHttp:
    def __init__(self, pages=None, status=200, fallback=None):
        self.pages = PAGES if pages is None else pages
        self.status = status
        self.fallback = fallback
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        for directory, body in self.pages.items():
            if directory_url(directory) == url:
                return HttpResponse(status_code=self.status, text=body)
        if self.fallback is not None:
            return HttpResponse(status_code=200, text=self.fallback)
        return HttpResponse(status_code=404, text="<html>404</html>")


def make_search(pages=None, config=None, status=200, fallback=None):
    http = FakeHttp(pages, status, fallback)
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(pages=None, config=None, status=200, fallback=None):
    http = FakeHttp(pages, status, fallback)
    return Importer(PluginContext(config=config or {}, http=http)), http


# ------------------------------------------------------------ listing parsing


def test_a_listing_yields_its_files():
    names = [d.filename for d in parse_listing(SOLTYS)]
    assert "soltys-en-v1.0.zip" in names
    assert "Parent Directory" not in names
    # Sort links (`?C=N;O=D`) are navigation, not files.
    assert not any(n.startswith("?") for n in names)


def test_the_size_and_date_columns_are_carried_as_text():
    files = {d.filename: d for d in parse_listing(SOLTYS)}
    assert files["soltys-en-v1.0.zip"].size_text == "3.3M"
    assert files["soltys-en-v1.0.zip"].date_text == "2011-11-14 23:43"


def test_checksum_sidecars_and_manuals_are_not_payloads():
    assert is_payload("soltys-en-v1.0.zip")
    assert not is_payload("soltys-en-v1.0.zip.sha256")
    assert not is_payload("dreamweb-manuals-en-highres.zip")
    assert not is_payload("nippon-manual-addons-1.0.zip")
    assert not is_payload("")


def test_a_language_data_file_with_an_odd_extension_is_still_a_payload():
    """`lang_he.b25c` is real Broken Sword 2.5 data; an extension
    allowlist would have dropped it."""
    assert is_payload("lang_he.b25c")


def test_a_document_that_is_not_an_index_raises():
    with pytest.raises(DownloadsError):
        parse_listing("<html><body>Not found</body></html>")
    with pytest.raises(DownloadsError):
        parse_listing("")


# ----------------------------------------------------------------- the table


def test_the_directory_index_is_an_exact_inversion():
    for slug, game in GAMES.items():
        assert slug_for_directory(game.directory) == slug
        assert game_for(slug) is game


def test_every_game_states_its_own_platform_and_why_it_is_free():
    for slug, game in GAMES.items():
        assert game.platform, slug
        assert game.freed_by, slug
        assert len(game.freed_by) > 20, slug


def test_a_directory_that_is_not_a_freeware_game_has_no_slug():
    """The allowlist, stated as a property rather than a comment."""
    for directory in ("Blade Runner", "Toonstruck", "Elvira 2", "SLUDGE"):
        assert slug_for_directory(directory) is None


# --------------------------------------------------------------------- search


def test_matching_happens_in_memory_so_a_miss_costs_no_request():
    search, http = make_search()
    assert search.search("zzzzzzz", None, 25) == []
    assert http.calls == []


def test_one_result_per_file_not_per_game():
    search, http = make_search()
    results = search.search("soltys", None, 25)
    assert len(http.calls) == 1
    assert {r.source_id for r in results} == {
        "soltys/soltys-en-v1.0.zip",
        "soltys/soltys-es-v1.0.zip",
        "soltys/soltys-pl-v1.0.zip",
    }
    assert all(r.platform == "scummvm" for r in results)
    assert all("Sołtys" in r.title for r in results)


def test_the_freeing_rights_holder_travels_with_the_result():
    search, _ = make_search()
    result = search.search("soltys", None, 1)[0]
    assert "L.K. Avalon" in result.extra["freed_by"]


def test_sidecars_and_manuals_never_become_results():
    search, _ = make_search()
    results = search.search("dreamweb", None, 100)
    names = {r.extra["filename"] for r in results}
    assert "dreamweb-uk-1.1.zip" in names
    assert not any(n.endswith(".sha256") for n in names)
    assert not any("manual" in n for n in names)


def test_the_slug_is_searched_as_well_as_the_title():
    search, _ = make_search()
    assert search.search("beneath-a-steel-sky", None, 5)
    assert search.search("steel sky", None, 5)


def test_a_platform_this_source_has_nothing_for_costs_no_request():
    search, http = make_search()
    assert search.search("", "snes", 25) == []
    assert http.calls == []


def test_the_walk_is_bounded():
    search, http = make_search(config={"max_games": 2}, fallback=EMPTY_INDEX)
    search.search("", None, 500)
    assert len(http.calls) == 2
    assert len(GAMES) > 2, "the bound has to be smaller than the catalogue"


def test_a_non_200_listing_raises():
    search, _ = make_search(status=503)
    with pytest.raises(DownloadsError):
        search.search("soltys", None, 5)


# ------------------------------------------------------------------- importer


def test_a_plan_names_the_exact_archive():
    importer, http = make_importer()
    plan = importer.plan(
        SearchResult(source_id="soltys/soltys-en-v1.0.zip", title="x")
    )
    assert plan.platform == "scummvm"
    assert plan.collection == "ScummVM freeware"
    assert plan.files[0].url == file_url("Soltys", "soltys-en-v1.0.zip")
    assert plan.files[0].filename == "soltys-en-v1.0.zip"
    assert http.calls == [directory_url("Soltys")]


def test_the_directory_underscore_is_preserved():
    """`Drascula_ The Vampire Strikes Back` 404s on a near miss."""
    assert GAMES["drascula"].directory == "Drascula_ The Vampire Strikes Back"


def test_a_directory_outside_the_allowlist_is_unreachable():
    """The whole safety model, as a test.

    `Blade Runner/` really is on the server and really does parse; the
    only thing keeping its subtitle pack out of a library is that no
    `source_id` naming it resolves.
    """
    importer, http = make_importer()
    for bad in (
        "blade-runner/Blade_Runner_Subtitles-v9.zip",
        "Blade Runner/Blade_Runner_Subtitles-v9.zip",
        "toonstruck/Toonstruck_Subtitles.zip",
    ):
        with pytest.raises(ImportRefused) as excinfo:
            importer.plan(SearchResult(source_id=bad, title="x"))
        assert "allowlist" in str(excinfo.value)
    assert http.calls == [], "an unknown game must not cost a request"


def test_a_sidecar_or_manual_refuses_even_when_it_is_really_there():
    importer, http = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(
            SearchResult(source_id="soltys/soltys-en-v1.0.zip.sha256", title="x")
        )
    assert "not a game file" in str(excinfo.value)
    assert http.calls == []


def test_a_name_that_is_no_longer_listed_refuses_and_says_what_is():
    importer, _ = make_importer()
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(
            SearchResult(source_id="soltys/soltys-en-v0.9.zip", title="x")
        )
    message = str(excinfo.value)
    assert "no file named" in message
    assert "soltys-en-v1.0.zip" in message


def test_a_malformed_source_id_refuses_with_an_example():
    importer, _ = make_importer()
    for bad in ("soltys", "   ", "/soltys-en-v1.0.zip"):
        with pytest.raises(ImportRefused) as excinfo:
            importer.plan(SearchResult(source_id=bad, title="x"))
        assert "<game>/<filename>" in str(excinfo.value)


def test_an_operator_platform_overrides_the_table():
    importer, _ = make_importer()
    plan = importer.plan(
        SearchResult(
            source_id="mystery-house/MYSTHOUS.zip", title="x", platform="appleii"
        )
    )
    assert plan.platform == "appleii"


def test_urls_are_quoted_once_and_never_carry_a_raw_space():
    url = file_url("Beneath a Steel Sky", "BASS-Floppy-1.3.zip")
    assert url.startswith("https://downloads.scummvm.org/frs/extras/")
    assert " " not in url
    assert "%2520" not in url
