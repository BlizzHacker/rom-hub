"""Unit tests for guides and tutorials plugins: GameFAQs, StrategyWiki, Game Manuals, and YouTube Tutorials.

All tests run strictly offline with mocked PluginContext and HttpClient.
No sockets are opened.
"""

import json
import sys
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DEV = WORKSPACE_ROOT / "plugins-dev"

# Add plugin directories to sys.path so modules can be imported
for p in ["gamefaqs", "strategywiki", "game-manuals", "youtube-tutorials"]:
    sys.path.insert(0, str(PLUGINS_DEV / p))

from rom_hub.manifest import load_manifest
from rom_hub.types import FetchFile, FetchPlan, SearchResult, StreamTarget
from rom_hub_sdk.context import HttpResponse, PluginContext

from gamefaqs.filenames import safe_filename as gamefaqs_safe_filename
from gamefaqs.importer import ImportRefused as GamefaqsImportRefused, Importer as GamefaqsImporter
from gamefaqs.search import Search as GamefaqsSearch
from gamefaqs.stream import Stream as GamefaqsStream, StreamRefused as GamefaqsStreamRefused

from strategywiki.filenames import safe_filename as sw_safe_filename
from strategywiki.importer import ImportRefused as StrategywikiImportRefused, Importer as StrategywikiImporter
from strategywiki.search import Search as StrategywikiSearch
from strategywiki.stream import Stream as StrategywikiStream, StreamRefused as StrategywikiStreamRefused

from game_manuals.filenames import safe_filename as gm_safe_filename
from game_manuals.importer import ImportRefused as GameManualsImportRefused, Importer as GameManualsImporter
from game_manuals.search import Search as GameManualsSearch
from game_manuals.stream import Stream as GameManualsStream, StreamRefused as GameManualsStreamRefused

from youtube_tutorials.filenames import safe_filename as yt_safe_filename
from youtube_tutorials.importer import ImportRefused as YoutubeImportRefused, Importer as YoutubeImporter
from youtube_tutorials.search import Search as YoutubeSearch
from youtube_tutorials.stream import Stream as YoutubeStream, StreamRefused as YoutubeStreamRefused


class MockHttp:
    """Mock HTTP client handling GET and POST without opening sockets."""

    def __init__(self, routes=None, default_body="{}", default_status=200):
        self.routes = routes or {}
        self.default_body = default_body
        self.default_status = default_status
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url: str, params=None, headers=None) -> HttpResponse:
        self.calls.append(("GET", url, dict(params or {})))
        return self._respond(url, params)

    def post(self, url: str, params=None, json=None, data=None, headers=None) -> HttpResponse:
        payload = json or data or params or {}
        self.calls.append(("POST", url, payload if isinstance(payload, dict) else {}))
        return self._respond(url, payload)

    def _respond(self, url: str, payload) -> HttpResponse:
        for key, handler in self.routes.items():
            matches = key in url
            if not matches and isinstance(payload, dict):
                matches = key in str(payload) or any(key in f"{k}={v}" for k, v in payload.items())
            if matches:
                if callable(handler):
                    res = handler(url, payload)
                    if isinstance(res, tuple):
                        return HttpResponse(status_code=res[0], text=res[1])
                    return HttpResponse(status_code=200, text=res)
                if isinstance(handler, tuple):
                    return HttpResponse(status_code=handler[0], text=handler[1])
                return HttpResponse(status_code=200, text=handler)
        return HttpResponse(status_code=self.default_status, text=self.default_body)


# ==============================================================================
# Manifest Tests
# ==============================================================================


def test_gamefaqs_manifest():
    path = PLUGINS_DEV / "gamefaqs" / "manifest.toml"
    m = load_manifest(path)
    assert m.slug == "gamefaqs"
    assert m.name == "GameFAQs Guides & FAQs"
    assert m.version == "0.1.1"
    assert m.rpp_version == "1"
    assert m.license == "MIT"
    assert m.capabilities == {
        "search": "gamefaqs.search:Search",
        "importer": "gamefaqs.importer:Importer",
        "stream": "gamefaqs.stream:Stream",
    }
    assert m.network == ["gamefaqs.gamespot.com"]
    assert "collection" in m.config_schema


def test_strategywiki_manifest():
    path = PLUGINS_DEV / "strategywiki" / "manifest.toml"
    m = load_manifest(path)
    assert m.slug == "strategywiki"
    assert m.name == "StrategyWiki Walkthroughs"
    assert m.version == "0.1.0"
    assert m.rpp_version == "1"
    assert m.license == "MIT"
    assert m.capabilities == {
        "search": "strategywiki.search:Search",
        "importer": "strategywiki.importer:Importer",
        "stream": "strategywiki.stream:Stream",
    }
    assert m.network == ["strategywiki.org"]
    assert "collection" in m.config_schema


def test_game_manuals_manifest():
    path = PLUGINS_DEV / "game-manuals" / "manifest.toml"
    m = load_manifest(path)
    assert m.slug == "game-manuals"
    assert m.name == "Game Manuals & Strategy Guides (Archive.org)"
    assert m.version == "0.1.0"
    assert m.rpp_version == "1"
    assert m.license == "MIT"
    assert m.capabilities == {
        "search": "game_manuals.search:Search",
        "importer": "game_manuals.importer:Importer",
        "stream": "game_manuals.stream:Stream",
    }
    assert m.network == ["archive.org", "*.archive.org"]
    assert "collection" in m.config_schema


def test_youtube_tutorials_manifest():
    path = PLUGINS_DEV / "youtube-tutorials" / "manifest.toml"
    m = load_manifest(path)
    assert m.slug == "youtube-tutorials"
    assert m.name == "YouTube Game Tutorials & Walkthroughs"
    assert m.version == "0.1.0"
    assert m.rpp_version == "1"
    assert m.license == "MIT"
    assert m.capabilities == {
        "search": "youtube_tutorials.search:Search",
        "importer": "youtube_tutorials.importer:Importer",
        "stream": "youtube_tutorials.stream:Stream",
    }
    assert m.network == ["www.youtube.com", "www.googleapis.com", "i.ytimg.com"]
    assert m.config_schema["api_key"]["type"] == "secret"


# ==============================================================================
# Plugin 1: GameFAQs Guides & FAQs
# ==============================================================================


def test_gamefaqs_safe_filename():
    assert gamefaqs_safe_filename("Normal Title.html") == "Normal Title.html"
    assert gamefaqs_safe_filename("../../evil/path/Guide.html") == "Guide.html"
    assert gamefaqs_safe_filename("NUL.html") == "_NUL.html"
    assert gamefaqs_safe_filename("Guide: The Quest? <v1>.html") == "Guide_ The Quest_ _v1_.html"


def test_gamefaqs_search_direct_faqs():
    search_html = """
    <html>
      <body>
        <div class="search_results">
          <a href="/snes/563538-chrono-trigger/faqs/31834">Chrono Trigger - Walkthrough</a>
          <a href="/snes/563538-chrono-trigger/faqs/40000">Boss FAQ</a>
        </div>
      </body>
    </html>
    """
    http = MockHttp(routes={"/search": search_html})
    ctx = PluginContext(config={}, http=http)
    searcher = GamefaqsSearch(ctx)

    results = searcher.search("chrono trigger", "snes", 10)
    assert len(results) == 2
    r0 = results[0]
    assert r0.source_id == "snes/563538-chrono-trigger/faqs/31834"
    assert r0.title == "Chrono Trigger - Walkthrough"
    assert r0.platform == "snes"
    assert r0.url == "https://gamefaqs.gamespot.com/snes/563538-chrono-trigger/faqs/31834?single=1&print=1"
    assert r0.extra == {"kind": "guide"}


def test_gamefaqs_search_game_hub_walk():
    search_html = """
    <html>
      <body>
        <a href="/snes/563538-chrono-trigger">Chrono Trigger</a>
      </body>
    </html>
    """
    faqs_html = """
    <html>
      <body>
        <a href="/snes/563538-chrono-trigger/faqs/31834">Full Walkthrough</a>
      </body>
    </html>
    """
    http = MockHttp(routes={
        "/search": search_html,
        "/snes/563538-chrono-trigger/faqs": faqs_html,
    })
    ctx = PluginContext(config={}, http=http)
    searcher = GamefaqsSearch(ctx)

    results = searcher.search("chrono trigger", None, 10)
    assert len(results) == 1
    assert results[0].source_id == "snes/563538-chrono-trigger/faqs/31834"
    assert "Chrono Trigger" in results[0].title
    assert results[0].platform == "snes"


def test_gamefaqs_search_platform_filtering():
    search_html = """
    <html>
      <body>
        <a href="/genesis/123456-game/faqs/111">Genesis Guide</a>
        <a href="/snes/563538-game/faqs/222">SNES Guide</a>
      </body>
    </html>
    """
    http = MockHttp(routes={"/search": search_html})
    ctx = PluginContext(config={}, http=http)
    searcher = GamefaqsSearch(ctx)

    results = searcher.search("game", "snes", 10)
    assert len(results) == 1
    assert results[0].platform == "snes"
    assert results[0].source_id == "snes/563538-game/faqs/222"


def test_gamefaqs_search_empty_and_error():
    from gamefaqs.search import GameFAQsError

    http = MockHttp(default_status=500, default_body="Server error")
    ctx = PluginContext(config={}, http=http)
    searcher = GamefaqsSearch(ctx)

    assert searcher.search("", None, 10) == []
    with pytest.raises(GameFAQsError):
        searcher.search("chrono trigger", None, 10)


def test_gamefaqs_importer_plan():
    ctx = PluginContext(config={"collection": "My Custom Guides"}, http=MockHttp())
    importer = GamefaqsImporter(ctx)

    res = SearchResult(
        source_id="snes/563538-chrono-trigger/faqs/31834",
        title="Chrono Trigger - Walkthrough",
        platform="snes",
    )
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 1
    file0 = plan.files[0]
    assert isinstance(file0, FetchFile)
    assert file0.url == "https://gamefaqs.gamespot.com/snes/563538-chrono-trigger/faqs/31834?single=1&print=1"
    assert file0.filename == "Chrono Trigger - Walkthrough.html"
    assert plan.platform == "snes"
    assert plan.collection == "My Custom Guides"


def test_gamefaqs_importer_refuses_empty_source_id():
    ctx = PluginContext(config={}, http=MockHttp())
    importer = GamefaqsImporter(ctx)
    res = SearchResult(source_id="   ", title="Walkthrough", platform="snes")
    with pytest.raises(GamefaqsImportRefused):
        importer.plan(res)


def test_gamefaqs_stream_resolve():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = GamefaqsStream(ctx)

    res = SearchResult(
        source_id="snes/563538-chrono-trigger/faqs/31834",
        title="Chrono Trigger Walkthrough",
        platform="snes",
    )
    target = streamer.resolve(res)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://gamefaqs.gamespot.com/snes/563538-chrono-trigger/faqs/31834?single=1&print=1"
    assert target.mime_type == "text/html"
    assert target.extra == {"kind": "guide"}


# ==============================================================================
# Plugin 2: StrategyWiki Walkthroughs
# ==============================================================================


def test_strategywiki_safe_filename():
    assert sw_safe_filename("Chrono Trigger - Walkthrough.json") == "Chrono Trigger - Walkthrough.json"
    assert sw_safe_filename("CON.json") == "_CON.json"
    assert sw_safe_filename("Chrono Trigger/Prologue.json") == "Prologue.json"


def test_strategywiki_search():
    search_resp = {
        "query": {
            "search": [
                {"title": "Chrono Trigger", "pageid": 1234},
                {"title": "Chrono Trigger/Walkthrough", "pageid": 5678},
            ]
        }
    }
    http = MockHttp(routes={"/w/api.php": json.dumps(search_resp)})
    ctx = PluginContext(config={}, http=http)
    searcher = StrategywikiSearch(ctx)

    results = searcher.search("chrono trigger", None, 10)
    assert len(results) == 2
    r0 = results[0]
    assert r0.source_id == "Chrono Trigger"
    assert r0.title == "Chrono Trigger"
    assert r0.url == "https://strategywiki.org/wiki/Chrono_Trigger"
    assert r0.extra == {"kind": "walkthrough", "pageid": "1234"}


def test_strategywiki_search_empty_and_error():
    http = MockHttp(default_status=500, default_body="Internal Error")
    ctx = PluginContext(config={}, http=http)
    searcher = StrategywikiSearch(ctx)

    assert searcher.search("", None, 10) == []
    assert searcher.search("test", None, 10) == []


def test_strategywiki_importer_single_page():
    # When allpages returns empty
    allpages_resp = {"query": {"allpages": []}}
    http = MockHttp(routes={"list=allpages": json.dumps(allpages_resp)})
    ctx = PluginContext(config={}, http=http)
    importer = StrategywikiImporter(ctx)

    res = SearchResult(source_id="Super Mario Bros.", title="Super Mario Bros.", platform="nes")
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 1
    assert "action=parse" in plan.files[0].url
    assert plan.files[0].filename == "Super Mario Bros.json"
    assert plan.collection == "StrategyWiki Guides"


def test_strategywiki_importer_with_subpages():
    allpages_resp = {
        "query": {
            "allpages": [
                {"title": "Chrono Trigger/Walkthrough"},
                {"title": "Chrono Trigger/Characters"},
                {"title": "Chrono Trigger/Endings"},
            ]
        }
    }
    http = MockHttp(routes={"list=allpages": json.dumps(allpages_resp)})
    ctx = PluginContext(config={}, http=http)
    importer = StrategywikiImporter(ctx)

    res = SearchResult(source_id="Chrono Trigger", title="Chrono Trigger", platform="snes")
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 4  # Main + 3 subpages

    filenames = [f.filename for f in plan.files]
    assert len(filenames) == len(set(filenames))  # Uniqueness invariant
    assert "Chrono Trigger.json" in filenames
    assert "Chrono Trigger - Walkthrough.json" in filenames
    assert "Chrono Trigger - Characters.json" in filenames


def test_strategywiki_stream_resolve():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = StrategywikiStream(ctx)

    res = SearchResult(source_id="Chrono Trigger", title="Chrono Trigger", platform="snes")
    target = streamer.resolve(res)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://strategywiki.org/wiki/Chrono_Trigger"
    assert target.mime_type == "text/html"


# ==============================================================================
# Plugin 3: Game Manuals & Strategy Guides (Archive.org)
# ==============================================================================


def test_game_manuals_safe_filename():
    assert gm_safe_filename("Zelda_Manual.pdf") == "Zelda_Manual.pdf"
    assert gm_safe_filename("PRN.pdf") == "_PRN.pdf"


def test_game_manuals_search():
    search_resp = {
        "response": {
            "docs": [
                {
                    "identifier": "snes_chrono_trigger_manual",
                    "title": "Chrono Trigger SNES Manual",
                    "year": "1995",
                    "item_size": 25100000,
                }
            ]
        }
    }
    http = MockHttp(routes={"/advancedsearch.php": json.dumps(search_resp)})
    ctx = PluginContext(config={}, http=http)
    searcher = GameManualsSearch(ctx)

    results = searcher.search("chrono trigger", None, 10)
    assert len(results) == 1
    r = results[0]
    assert r.source_id == "snes_chrono_trigger_manual"
    assert r.title == "Chrono Trigger SNES Manual"
    assert r.size_bytes == 25100000
    assert r.url == "https://archive.org/details/snes_chrono_trigger_manual"
    assert r.extra == {"kind": "manual", "year": "1995"}


def test_game_manuals_search_empty_and_error():
    http = MockHttp(default_status=500, default_body="Archive Down")
    ctx = PluginContext(config={}, http=http)
    searcher = GameManualsSearch(ctx)

    assert searcher.search("", None, 10) == []
    assert searcher.search("chrono", None, 10) == []


def test_game_manuals_importer():
    meta_resp = {
        "result": [
            {"name": "manual.pdf", "size": 15000000},
            {"name": "guide.cbz", "size": 35000000},
            {"name": "item_meta.xml", "size": 1200},
            {"name": "item_thumb.jpg", "size": 5000},
            {"name": "book_djvu.txt", "size": 40000},
        ]
    }
    http = MockHttp(routes={"/metadata/": json.dumps(meta_resp)})
    ctx = PluginContext(config={"collection": "Vault Manuals"}, http=http)
    importer = GameManualsImporter(ctx)

    res = SearchResult(source_id="snes_chrono_trigger_manual", title="Chrono Trigger Manual", platform="snes")
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 3  # manual.pdf, guide.cbz, book_djvu.txt

    filenames = [f.filename for f in plan.files]
    assert "manual.pdf" in filenames
    assert "guide.cbz" in filenames
    assert "book_djvu.txt" in filenames
    assert "item_meta.xml" not in filenames
    assert plan.collection == "Vault Manuals"


def test_game_manuals_importer_fallback_zip():
    meta_resp = {
        "result": [
            {"name": "manual_scans.zip", "size": 10000000},
            {"name": "meta.xml", "size": 500},
        ]
    }
    http = MockHttp(routes={"/metadata/": json.dumps(meta_resp)})
    ctx = PluginContext(config={}, http=http)
    importer = GameManualsImporter(ctx)

    res = SearchResult(source_id="item_zip_only", title="Manual Zip Only", platform="snes")
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 1
    assert plan.files[0].filename == "manual_scans.zip"


def test_game_manuals_stream_resolve():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = GameManualsStream(ctx)

    res = SearchResult(source_id="snes_chrono_trigger_manual", title="Chrono Trigger Manual", platform="snes")
    target = streamer.resolve(res)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://archive.org/details/snes_chrono_trigger_manual"
    assert target.mime_type == "text/html"


# ==============================================================================
# Plugin 4: YouTube Game Tutorials & Walkthroughs
# ==============================================================================


def test_youtube_safe_filename():
    assert yt_safe_filename("Walkthrough Ep 1.mp4") == "Walkthrough Ep 1.mp4"
    assert yt_safe_filename("COM1.jpg") == "_COM1.jpg"


def test_youtube_search_api_key():
    api_resp = {
        "items": [
            {
                "id": {"videoId": "test123vid"},
                "snippet": {
                    "title": "Chrono Trigger 100% Walkthrough Part 1",
                    "channelTitle": "RetroChannel",
                },
            }
        ]
    }
    http = MockHttp(routes={"/youtube/v3/search": json.dumps(api_resp)})
    ctx = PluginContext(config={"api_key": "MOCK_KEY"}, http=http)
    searcher = YoutubeSearch(ctx)

    results = searcher.search("chrono trigger", None, 10)
    assert len(results) == 1
    r = results[0]
    assert r.source_id == "test123vid"
    assert r.title == "Chrono Trigger 100% Walkthrough Part 1"
    assert r.url == "https://www.youtube.com/watch?v=test123vid"
    assert r.extra["channel"] == "RetroChannel"


def test_youtube_search_keyless_youtubei():
    yt_data = {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "itemSectionRenderer": {
                                    "contents": [
                                        {
                                            "videoRenderer": {
                                                "videoId": "vid_abc_456",
                                                "title": {"runs": [{"text": "Chrono Trigger Any% Speedrun"}]},
                                                "ownerText": {"runs": [{"text": "SpeedyRunner"}]},
                                                "lengthText": {"simpleText": "45:32"},
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
    }
    http = MockHttp(routes={"/youtubei/v1/search": json.dumps(yt_data)})
    ctx = PluginContext(config={}, http=http)
    searcher = YoutubeSearch(ctx)

    results = searcher.search("chrono trigger", None, 10)
    assert len(results) == 1
    r = results[0]
    assert r.source_id == "vid_abc_456"
    assert r.title == "Chrono Trigger Any% Speedrun"
    assert r.extra["channel"] == "SpeedyRunner"
    assert r.extra["duration"] == "45:32"


def test_youtube_search_keyless_html_initial_data():
    yt_data = {
        "contents": {
            "section": {
                "videoRenderer": {
                    "videoId": "vid_initial_data_789",
                    "title": {"simpleText": "Initial Data Walkthrough"},
                    "ownerText": {"runs": [{"text": "HTMLGamer"}]},
                    "lengthText": {"simpleText": "12:00"},
                }
            }
        }
    }
    html_page = f"<html><body><script>var ytInitialData = {json.dumps(yt_data)};</script></body></html>"
    # youtubei fails with 404, fallback to /results GET
    http = MockHttp(routes={
        "/youtubei/v1/search": (404, "Not Found"),
        "/results": html_page,
    })
    ctx = PluginContext(config={}, http=http)
    searcher = YoutubeSearch(ctx)

    results = searcher.search("chrono trigger", None, 10)
    assert len(results) == 1
    assert results[0].source_id == "vid_initial_data_789"
    assert results[0].title == "Initial Data Walkthrough"


def test_youtube_importer_with_timedtext():
    watch_html = """
    <html>
      <body>
        <script>
          "captionTracks":[{"baseUrl":"https://www.youtube.com/api/timedtext?v=test123vid&lang=en"}]
        </script>
      </body>
    </html>
    """
    http = MockHttp(routes={"/watch": watch_html})
    ctx = PluginContext(config={}, http=http)
    importer = YoutubeImporter(ctx)

    res = SearchResult(source_id="test123vid", title="Chrono Trigger Walkthrough", platform="youtube")
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 3  # oembed.json, hqdefault.jpg, .vtt

    urls = [f.url for f in plan.files]
    assert any("oembed" in u for u in urls)
    assert any("i.ytimg.com" in u for u in urls)
    assert any("timedtext" in u for u in urls)

    filenames = [f.filename for f in plan.files]
    assert len(filenames) == len(set(filenames))  # Uniqueness check
    assert any(fn.endswith(".oembed.json") for fn in filenames)
    assert any(fn.endswith(".jpg") for fn in filenames)
    assert any(fn.endswith(".vtt") for fn in filenames)


def test_youtube_importer_without_timedtext():
    watch_html = "<html><body>No captions here</body></html>"
    http = MockHttp(routes={"/watch": watch_html})
    ctx = PluginContext(config={}, http=http)
    importer = YoutubeImporter(ctx)

    res = SearchResult(source_id="test123vid", title="Walkthrough Without Captions", platform="youtube")
    plan = importer.plan(res)
    assert isinstance(plan, FetchPlan)
    assert len(plan.files) == 2  # oembed.json and jpg thumbnail only


def test_youtube_stream_resolve():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = YoutubeStream(ctx)

    res = SearchResult(source_id="test123vid", title="Chrono Trigger Walkthrough", platform="youtube")
    target = streamer.resolve(res)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://www.youtube.com/watch?v=test123vid"
    assert target.mime_type == "text/html"


# ==============================================================================
# Refusal & Edge Case Tests
# ==============================================================================


def test_gamefaqs_stream_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = GamefaqsStream(ctx)
    res = SearchResult(source_id="   ", title="Title", platform="snes")
    with pytest.raises(GamefaqsStreamRefused):
        streamer.resolve(res)


def test_strategywiki_import_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    importer = StrategywikiImporter(ctx)
    res = SearchResult(source_id="   ", title="   ", platform="guides")
    with pytest.raises(StrategywikiImportRefused):
        importer.plan(res)


def test_strategywiki_stream_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = StrategywikiStream(ctx)
    res = SearchResult(source_id="   ", title="   ", platform="guides")
    with pytest.raises(StrategywikiStreamRefused):
        streamer.resolve(res)


def test_game_manuals_import_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    importer = GameManualsImporter(ctx)
    res = SearchResult(source_id="   ", title="Manual", platform="manuals")
    with pytest.raises(GameManualsImportRefused):
        importer.plan(res)


def test_game_manuals_import_refused_no_files():
    meta_resp = {"result": [{"name": "item_meta.xml", "size": 100}]}
    http = MockHttp(routes={"/metadata/": json.dumps(meta_resp)})
    ctx = PluginContext(config={}, http=http)
    importer = GameManualsImporter(ctx)
    res = SearchResult(source_id="item_no_manuals", title="No Manuals", platform="manuals")
    with pytest.raises(GameManualsImportRefused):
        importer.plan(res)


def test_game_manuals_stream_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = GameManualsStream(ctx)
    res = SearchResult(source_id="   ", title="Manual", platform="manuals")
    with pytest.raises(GameManualsStreamRefused):
        streamer.resolve(res)


def test_game_manuals_cap_32_files():
    # 40 pdf files
    many_files = [{"name": f"manual_part_{i}.pdf", "size": 1000} for i in range(40)]
    meta_resp = {"result": many_files}
    http = MockHttp(routes={"/metadata/": json.dumps(meta_resp)})
    ctx = PluginContext(config={}, http=http)
    importer = GameManualsImporter(ctx)

    res = SearchResult(source_id="giant_collection", title="Giant Manual", platform="manuals")
    plan = importer.plan(res)
    assert len(plan.files) == 32


def test_youtube_import_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    importer = YoutubeImporter(ctx)
    res = SearchResult(source_id="   ", title="Title", platform="youtube")
    with pytest.raises(YoutubeImportRefused):
        importer.plan(res)


def test_youtube_stream_refused_empty():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = YoutubeStream(ctx)
    res = SearchResult(source_id="   ", title="Title", platform="youtube")
    with pytest.raises(YoutubeStreamRefused):
        streamer.resolve(res)


def test_youtube_search_suffix_applied():
    http = MockHttp(routes={"/youtubei/v1/search": json.dumps({"contents": {}})})
    ctx = PluginContext(config={"default_suffix": "speedrun playthrough"}, http=http)
    searcher = YoutubeSearch(ctx)
    searcher.search("Chrono Trigger", None, 5)

    assert len(http.calls) > 0
    post_call = http.calls[0]
    payload = post_call[2]
    assert "query" in payload
    assert payload["query"] == "Chrono Trigger speedrun playthrough"
