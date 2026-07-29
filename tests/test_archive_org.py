import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "archive_org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.search import Search, build_query  # noqa: E402

from romm_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

# Trimmed from a real advancedsearch.php response captured during design.
FIXTURE = {
    "response": {
        "numFound": 8903,
        "docs": [
            {
                "identifier": "msdos_Oregon_Trail_The_1990",
                "title": "The Oregon Trail",
                "collection": ["softwarelibrary_msdos_games", "stream_only", "emulation"],
                "item_size": 359527,
            },
            {
                "identifier": "msdos_Old_Gold_1995",
                "title": "Old Gold",
                "collection": ["softwarelibrary_msdos_games"],
                "item_size": 12345,
            },
            {"identifier": "no_title_item", "collection": []},
        ],
    }
}


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return HttpResponse(status_code=200, text=json.dumps(self.payload))


def make_search(payload=FIXTURE, config=None):
    http = FakeHttp(payload)
    ctx = PluginContext(config=config or {}, http=http)
    return Search(ctx), http


def test_returns_results_for_each_valid_doc():
    search, _ = make_search()
    results = search.search("oregon", None, 25)
    assert [r.source_id for r in results] == [
        "msdos_Oregon_Trail_The_1990",
        "msdos_Old_Gold_1995",
    ]


def test_stream_only_is_flagged_without_a_second_request():
    search, http = make_search()
    results = search.search("oregon", None, 25)
    assert results[0].extra["stream_only"] == "true"
    assert results[1].extra["stream_only"] == "false"
    assert len(http.calls) == 1


def test_docs_without_a_title_are_skipped():
    search, _ = make_search()
    assert all(r.source_id != "no_title_item" for r in search.search("x", None, 25))


def test_size_is_carried_through():
    search, _ = make_search()
    assert search.search("x", None, 25)[0].size_bytes == 359527


def test_url_points_at_the_item_details_page():
    search, _ = make_search()
    result = search.search("x", None, 25)[0]
    assert result.url == "https://archive.org/details/msdos_Oregon_Trail_The_1990"


def test_query_is_scoped_to_configured_collections():
    search, http = make_search(config={"collections": ["softwarelibrary_msdos_games"]})
    search.search("oregon", None, 25)
    _, params = http.calls[0]
    assert "softwarelibrary_msdos_games" in params["q"]
    assert "oregon" in params["q"]


# --------------------------------------------------------- query construction
#
# The bug these pin: `({query}) AND collection:({scope})` put a bare term in
# Archive.org's default field -- the whole record -- so `sonic` ranked in
# `Die Hard (2004)(Die Chefrocker)` and `oregon trail` ranked in
# `Great Hierophant's .WOZ Archive`. Terms are confined to the title now.


def test_terms_are_confined_to_the_title_not_the_whole_record():
    q = build_query("sonic", ["softwarelibrary"])
    assert q == 'title:("sonic") AND collection:(softwarelibrary)'


def test_every_term_of_a_multi_word_query_must_be_in_the_title():
    assert build_query("prince of persia", ["softwarelibrary"]) == (
        'title:("prince" AND "of" AND "persia") AND collection:(softwarelibrary)'
    )


def test_a_multi_word_query_is_terms_and_not_a_phrase():
    """Adjacency is deliberately not required.

    `title:("prince of persia")` also removes the junk, but verified live it
    returns zero results for `persia prince` and `hedgehog sonic`. Narrowing
    until a reasonable query returns nothing is a worse bug than the one
    being fixed, so the terms are ANDed and word order does not matter.
    """
    q = build_query("prince of persia", ["softwarelibrary"])
    assert '"prince" AND "of" AND "persia"' in q
    assert '"prince of persia"' not in q


def test_collection_scope_survives_and_still_ORs_several_collections():
    q = build_query("sonic", ["softwarelibrary", "softwarelibrary_msdos_games"])
    assert q.endswith(
        "AND collection:(softwarelibrary OR softwarelibrary_msdos_games)"
    )


def test_an_empty_query_browses_the_collection_instead_of_emitting_title_of_nothing():
    """`title:()` is a syntax error and `title:("")` matches nothing.

    Browsing a collection with no search terms has to keep working, so the
    title clause is dropped rather than built empty.
    """
    for empty in ("", "   ", None):
        assert build_query(empty, ["softwarelibrary"]) == "collection:(softwarelibrary)"


def test_a_term_cannot_break_out_of_its_quoted_phrase():
    """Quoting is what neutralises Lucene's operators, so it must hold.

    Only the quote and the backslash can end a phrase early; everything else
    (`-`, `&`, `:`) is literal once quoted, which is why `r-type` and
    `sonic & knuckles` need no special handling and were verified live.
    """
    q = build_query('sonic" OR collection:(porn) OR title:("x', ["softwarelibrary"])

    # Replace every *well-formed* quoted phrase with a marker. Whatever is
    # left is the query's real structure -- so if a term had escaped its
    # quotes, its operators would show up here instead of staying inert
    # text. Checking for the injected substring directly would not do: it
    # legitimately appears inside a phrase, harmlessly.
    skeleton = re.sub(r'"(?:[^"\\]|\\.)*"', "TERM", q)
    assert skeleton == (
        "title:(TERM AND TERM AND TERM AND TERM AND TERM) "
        "AND collection:(softwarelibrary)"
    )

    assert build_query("back\\slash", ["softwarelibrary"]) == (
        'title:("back\\\\slash") AND collection:(softwarelibrary)'
    )


def test_lucene_operators_in_a_real_title_are_left_alone():
    # Verified live: both return the games you would expect.
    assert build_query("r-type", ["softwarelibrary"]) == (
        'title:("r-type") AND collection:(softwarelibrary)'
    )
    assert '"&"' in build_query("sonic & knuckles", ["softwarelibrary"])


# ------------------------------------------------- relevance, against capture
#
# Captured live from advancedsearch.php with the query form above, via
# scripts the plugin itself builds. The point of these is not the exact
# titles -- it is that everything that came back is *about* what was asked
# for, which is what the old query could not manage.


RELEVANCE_CAPTURES = [
    ("sonic", "search_sonic.json"),
    ("oregon trail", "search_oregon_trail.json"),
    ("prince of persia", "search_prince_of_persia.json"),
]


@pytest.mark.parametrize("query,filename", RELEVANCE_CAPTURES)
def test_a_live_capture_returns_only_titles_that_match_the_query(query, filename):
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    search, _ = make_search(payload)
    results = search.search(query, None, 25)

    assert results, f"{query!r} returned nothing -- the query is now too narrow"
    for result in results:
        title = result.title.lower()
        missing = [t for t in query.split() if t not in title]
        assert not missing, f"{result.title!r} does not contain {missing}"


@pytest.mark.parametrize("query,filename", RELEVANCE_CAPTURES)
def test_a_live_capture_is_not_narrowed_to_a_handful(query, filename):
    """Relevance was bought without gutting recall.

    numFound at capture time: sonic 285, oregon trail 34, prince of persia
    49. A title scope that had over-narrowed would show up here as a couple
    of hits, not dozens.
    """
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    assert payload["response"]["numFound"] >= 25


def test_the_junk_the_old_query_ranked_in_is_gone():
    """Named, because these are the actual reported symptoms.

    Every one of these was a real first-page hit for its query under
    `({query}) AND collection:({scope})`: they match somewhere in a
    description or a subject tag, which is not a claim anybody searching a
    ROM library is making.
    """
    junk = {
        "search_sonic.json": ["die hard"],
        "search_oregon_trail.json": ["woz archive", "a2r images", "goonies"],
        "search_prince_of_persia.json": ["total replay", "monmallineun"],
    }
    for filename, unwanted in junk.items():
        payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        titles = " | ".join(
            str(d.get("title", "")).lower() for d in payload["response"]["docs"]
        )
        for phrase in unwanted:
            assert phrase not in titles, f"{phrase!r} came back in {filename}"


def test_limit_is_passed_as_rows():
    search, http = make_search()
    search.search("oregon", None, 7)
    assert http.calls[0][1]["rows"] == 7


def test_empty_response_returns_no_results():
    search, _ = make_search(payload={"response": {"numFound": 0, "docs": []}})
    assert search.search("nothing", None, 25) == []


def test_one_malformed_doc_does_not_cost_every_other_result():
    """size_bytes is a ge=0 pydantic field fed straight from upstream JSON.

    A single bad item_size used to raise ValidationError out of search() and
    lose the whole response for that plugin.
    """
    payload = json.loads(json.dumps(FIXTURE))
    payload["response"]["docs"].insert(
        0, {"identifier": "bad_size", "title": "Bad Size", "item_size": -5}
    )
    payload["response"]["docs"].insert(
        1, {"identifier": "junk_size", "title": "Junk Size", "item_size": "enormous"}
    )
    search, _ = make_search(payload)
    results = search.search("oregon", None, 25)
    assert [r.source_id for r in results] == [
        "msdos_Oregon_Trail_The_1990",
        "msdos_Old_Gold_1995",
    ]
