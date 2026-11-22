"""The gamefaqs `search` capability.

GameFAQs' /search page renders its results with script, so the plugin reads
the JSON behind the site's own search box (/ajax/home_game_search) and then
each game's /faqs page. The JSON rows and FAQ links below are the shapes the
live site served through FlareSolverr on 2026-11-22, trimmed to the fields
the plugin reads. No test opens a socket.
"""

import html
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "gamefaqs"
sys.path.insert(0, str(PLUGIN_ROOT))

from gamefaqs.search import AJAX_SEARCH, GameFAQsError, Search, _ajax_games  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

AJAX_ROWS = [
    {"game_name": "Chrono Trigger", "url": "/snes/563538-chrono-trigger", "has_guides": "1"},
    {"game_name": "BS Chrono Trigger Character Library", "url": "/snes/588776-bs-chrono-trigger",
     "has_guides": "0"},
    {"game_name": "Chrono Trigger", "url": "/ds/950052-chrono-trigger", "has_guides": "1"},
    {"game_name": "Board", "url": "/boards/563538-chrono-trigger", "has_guides": "1"},
]

FAQS_PAGE = """
<ul>
<li><a href="/snes/563538-chrono-trigger/faqs/2275">Guide and Walkthrough</a></li>
<li><a href="/snes/563538-chrono-trigger/faqs/33181">Tech/Combo FAQ</a></li>
</ul>
"""


def pre_wrapped(rows) -> str:
    """How Chromium (and so FlareSolverr) renders a JSON body."""
    return f"<html><body><pre>{html.escape(json.dumps(rows))}</pre></body></html>"


class FakeGameFAQs:
    def __init__(self, ajax_text, ajax_status=200):
        self.ajax_text = ajax_text
        self.ajax_status = ajax_status
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append(url)
        if url.startswith(AJAX_SEARCH):
            return HttpResponse(status_code=self.ajax_status, text=self.ajax_text)
        if url.endswith("/faqs"):
            return HttpResponse(status_code=200, text=FAQS_PAGE.replace("snes/563538-chrono-trigger", url.split(".com/")[1][:-5]))
        # The script-rendered results page: chrome, no game links.
        return HttpResponse(status_code=200, text="<html><title>Game Search - GameFAQs</title></html>")


def search(http, query="Chrono Trigger", limit=10, config=None):
    return Search(PluginContext(config=config or {}, http=http)).search(query, None, limit)


def test_the_json_search_is_read_through_flaresolverrs_pre_wrapper():
    games = _ajax_games(pre_wrapped(AJAX_ROWS), "")
    assert [g[1:3] for g in games] == [
        ("snes", "563538-chrono-trigger"),
        ("ds", "950052-chrono-trigger"),
        ("snes", "588776-bs-chrono-trigger"),
    ], "guide-carrying games first; /boards/ is not a game"


def test_bare_json_is_read_too():
    assert len(_ajax_games(json.dumps(AJAX_ROWS), "")) == 3


def test_a_search_returns_guides_from_each_games_faq_page():
    http = FakeGameFAQs(pre_wrapped(AJAX_ROWS))
    rows = search(http)
    assert rows[0].title == "Chrono Trigger - Guide and Walkthrough"
    assert rows[0].source_id == "snes/563538-chrono-trigger/faqs/2275"
    assert rows[0].url.endswith("?single=1&print=1")
    assert {r.platform for r in rows} == {"snes", "ds"}


def test_the_platform_config_narrows_the_games():
    http = FakeGameFAQs(pre_wrapped(AJAX_ROWS))
    rows = search(http, config={"platform": "ds"})
    assert rows and {r.platform for r in rows} == {"ds"}


def test_a_blocked_json_search_names_flaresolverr():
    with pytest.raises(GameFAQsError, match="FlareSolverr"):
        search(FakeGameFAQs("", ajax_status=403))


def test_no_json_games_falls_back_to_the_results_page():
    http = FakeGameFAQs("[]")
    assert search(http) == []
    assert any("/search?game=" in url for url in http.calls)
