import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.search import Search  # noqa: E402

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


def test_limit_is_passed_as_rows():
    search, http = make_search()
    search.search("oregon", None, 7)
    assert http.calls[0][1]["rows"] == 7


def test_empty_response_returns_no_results():
    search, _ = make_search(payload={"response": {"numFound": 0, "docs": []}})
    assert search.search("nothing", None, 25) == []
