"""Comprehensive offline unit tests for the 3 major mod plugins:

- plugins-dev/curseforge
- plugins-dev/moddb
- plugins-dev/nexusmods

Tests validate load_manifest(), Search, Import (FetchPlan validation),
and Stream (StreamTarget validation) with mocked ctx.http.
"""

import json
from pathlib import Path
import sys
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DEV = REPO_ROOT / "plugins-dev"

# Add each plugin's folder to sys.path so their modules can be imported
sys.path.insert(0, str(PLUGINS_DEV / "curseforge"))
sys.path.insert(0, str(PLUGINS_DEV / "moddb"))
sys.path.insert(0, str(PLUGINS_DEV / "nexusmods"))

from rom_hub.manifest import load_manifest  # noqa: E402
from rom_hub.types import bare_filename  # noqa: E402
from rom_hub_sdk import (  # noqa: E402
    FetchPlan,
    HttpResponse,
    PluginContext,
    SearchResult,
    StreamTarget,
)

# CurseForge imports
import curseforge.importer as cf_importer  # noqa: E402
import curseforge.search as cf_search  # noqa: E402
import curseforge.stream as cf_stream  # noqa: E402
from curseforge.filenames import safe_filename as cf_safe_filename  # noqa: E402

# ModDB imports
import moddb.importer as moddb_importer  # noqa: E402
import moddb.search as moddb_search  # noqa: E402
import moddb.stream as moddb_stream  # noqa: E402
from moddb.filenames import safe_filename as moddb_safe_filename  # noqa: E402

# Nexus Mods imports
import nexusmods.importer as nm_importer  # noqa: E402
import nexusmods.search as nm_search  # noqa: E402
import nexusmods.stream as nm_stream  # noqa: E402
from nexusmods.filenames import safe_filename as nm_safe_filename  # noqa: E402


class MockHttp:
    """Mock HttpClient recording all requests and returning preconfigured responses."""

    def __init__(self, routes: dict[Any, Any] | None = None):
        self.routes: dict[Any, Any] = routes or {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        call_record = {
            "method": "GET",
            "url": url,
            "params": params,
            "headers": headers,
        }
        self.calls.append(call_record)

        # Sort patterns by length descending so longer/more specific URLs match first
        sorted_routes = sorted(
            self.routes.items(),
            key=lambda item: len(item[0][1] if isinstance(item[0], tuple) else item[0]),
            reverse=True,
        )

        for pattern, response in sorted_routes:
            matches = False
            if isinstance(pattern, tuple) and len(pattern) == 2:
                meth, pat = pattern
                if meth == "GET" and pat in url:
                    matches = True
            elif isinstance(pattern, str) and pattern in url:
                matches = True

            if matches:
                if callable(response):
                    return response(url, params, headers)
                return response

        return HttpResponse(status_code=404, text=f"MockHttp: not found {url}")

    def post(
        self,
        url: str,
        *,
        params: dict | None = None,
        json: Any = None,
        data: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        call_record = {
            "method": "POST",
            "url": url,
            "params": params,
            "headers": headers,
            "json": json,
            "data": data,
        }
        self.calls.append(call_record)

        sorted_routes = sorted(
            self.routes.items(),
            key=lambda item: len(item[0][1] if isinstance(item[0], tuple) else item[0]),
            reverse=True,
        )

        for pattern, response in sorted_routes:
            matches = False
            if isinstance(pattern, tuple) and len(pattern) == 2:
                meth, pat = pattern
                if meth == "POST" and pat in url:
                    matches = True
            elif isinstance(pattern, str) and pattern in url:
                matches = True

            if matches:
                if callable(response):
                    return response(url, json or data, headers)
                return response

        return HttpResponse(status_code=404, text=f"MockHttp: not found {url}")


# ==============================================================================
# Manifest Validation Tests
# ==============================================================================

def test_curseforge_manifest_loads_and_validates():
    manifest_path = PLUGINS_DEV / "curseforge" / "manifest.toml"
    assert manifest_path.exists(), "curseforge/manifest.toml must exist"
    m = load_manifest(manifest_path)

    assert m.slug == "curseforge"
    assert m.name == "CurseForge Mods & Addons"
    assert m.version == "0.1.0"
    assert m.rpp_version == "1"
    assert m.license == "MIT"

    assert m.capabilities == {
        "search": "curseforge.search:Search",
        "importer": "curseforge.importer:Importer",
        "stream": "curseforge.stream:Stream",
    }
    assert sorted(m.network) == sorted([
        "api.curseforge.com",
        "www.curseforge.com",
        "edge.forgecdn.net",
        "mediafilez.forgecdn.net",
        "*.forgecdn.net",
    ])
    assert m.config_schema["api_key"]["type"] == "secret"
    assert "default" not in m.config_schema["api_key"]
    assert m.config_schema["game_id"]["type"] == "int"
    assert m.config_schema["game_id"]["default"] == 1


def test_moddb_manifest_loads_and_validates():
    manifest_path = PLUGINS_DEV / "moddb" / "manifest.toml"
    assert manifest_path.exists(), "moddb/manifest.toml must exist"
    m = load_manifest(manifest_path)

    assert m.slug == "moddb"
    assert m.name == "ModDB Mods, Addons & Total Conversions"
    assert m.version == "0.1.0"
    assert m.rpp_version == "1"
    assert m.license == "MIT"

    assert m.capabilities == {
        "search": "moddb.search:Search",
        "importer": "moddb.importer:Importer",
        "stream": "moddb.stream:Stream",
    }
    assert sorted(m.network) == sorted([
        "www.moddb.com",
        "moddb.com",
        "media.moddb.com",
        "*.moddb.com",
    ])
    assert m.config_schema["game_slug"]["type"] == "str"
    assert m.config_schema["game_slug"]["default"] == ""


def test_nexusmods_manifest_loads_and_validates():
    manifest_path = PLUGINS_DEV / "nexusmods" / "manifest.toml"
    assert manifest_path.exists(), "nexusmods/manifest.toml must exist"
    m = load_manifest(manifest_path)

    assert m.slug == "nexusmods"
    assert m.name == "Nexus Mods"
    assert m.version == "0.1.0"
    assert m.rpp_version == "1"
    assert m.license == "MIT"

    assert m.capabilities == {
        "search": "nexusmods.search:Search",
        "importer": "nexusmods.importer:Importer",
        "stream": "nexusmods.stream:Stream",
    }
    assert sorted(m.network) == sorted([
        "api.nexusmods.com",
        "www.nexusmods.com",
        "cf-files.nexusmods.com",
        "*.nexusmods.com",
    ])
    assert m.config_schema["api_key"]["type"] == "secret"
    assert "default" not in m.config_schema["api_key"]
    assert m.config_schema["game_domain"]["type"] == "str"
    assert m.config_schema["game_domain"]["default"] == "diablo2resurrected"


# ==============================================================================
# CurseForge Plugin Tests
# ==============================================================================

def test_curseforge_search_api_with_key():
    api_response = {
        "data": [
            {
                "id": 3358,
                "gameId": 1,
                "name": "Deadly Boss Mods (DBM)",
                "slug": "deadly-boss-mods",
                "summary": "Essential boss encounter notifications.",
                "links": {"websiteUrl": "https://www.curseforge.com/wow/addons/deadly-boss-mods"},
            }
        ]
    }
    http = MockHttp({
        "https://api.curseforge.com/v1/mods/search": HttpResponse(
            status_code=200, text=json.dumps(api_response)
        )
    })
    ctx = PluginContext(
        config={"api_key": "test_cf_key", "game_id": 1},
        http=http,
    )
    searcher = cf_search.Search(ctx)
    results = searcher.search("dbm", platform=None, limit=5)

    assert len(results) == 1
    res = results[0]
    assert res.source_id == "3358"
    assert res.title == "Deadly Boss Mods (DBM)"
    assert res.platform == "World of Warcraft"
    assert res.url == "https://www.curseforge.com/wow/addons/deadly-boss-mods"
    assert res.extra["mod_id"] == "3358"

    # Verify headers and params sent
    last_call = http.calls[0]
    assert last_call["headers"]["x-api-key"] == "test_cf_key"
    assert last_call["params"]["gameId"] == 1
    assert last_call["params"]["searchFilter"] == "dbm"


def test_curseforge_search_query_prefixes():
    http = MockHttp({
        "https://api.curseforge.com/v1/mods/search": HttpResponse(
            status_code=200, text=json.dumps({"data": []})
        )
    })
    ctx = PluginContext(config={"api_key": "key", "game_id": 1}, http=http)
    searcher = cf_search.Search(ctx)

    # Minecraft prefix
    searcher.search("mc:sodium", platform=None, limit=5)
    call_mc = http.calls[-1]
    assert call_mc["params"]["gameId"] == 432
    assert call_mc["params"]["searchFilter"] == "sodium"

    # Stardew Valley prefix
    searcher.search("stardew:smapi", platform=None, limit=5)
    call_sv = http.calls[-1]
    assert call_sv["params"]["gameId"] == 669
    assert call_sv["params"]["searchFilter"] == "smapi"

    # Numeric custom game ID prefix
    searcher.search("432:jei", platform=None, limit=5)
    call_num = http.calls[-1]
    assert call_num["params"]["gameId"] == 432
    assert call_num["params"]["searchFilter"] == "jei"


def test_curseforge_search_web_fallback_json_and_html():
    # Web API JSON success
    web_api_resp = {
        "data": [
            {
                "id": 9999,
                "gameId": 1,
                "name": "Details! Damage Meter",
                "slug": "details",
                "summary": "Deep combat analysis.",
                "links": {"websiteUrl": "https://www.curseforge.com/wow/addons/details"},
            }
        ]
    }
    http = MockHttp({
        "https://www.curseforge.com/api/v1/mods/search": HttpResponse(
            status_code=200, text=json.dumps(web_api_resp)
        )
    })
    ctx = PluginContext(config={"game_id": 1}, http=http)
    searcher = cf_search.Search(ctx)
    results = searcher.search("details", platform=None, limit=5)
    assert len(results) == 1
    assert results[0].source_id == "9999"
    assert results[0].title == "Details! Damage Meter"

    # HTML fallback when web API returns 404 or HTML
    html_page = """
    <html>
      <body>
        <div class="results">
          <a href="/wow/addons/bagnon">Bagnon</a>
          <a href="/wow/addons/weakauras-2">WeakAuras 2</a>
        </div>
      </body>
    </html>
    """
    http_html = MockHttp({
        "https://www.curseforge.com/api/v1/mods/search": HttpResponse(status_code=404, text=""),
        "https://www.curseforge.com/wow/search": HttpResponse(status_code=200, text=html_page),
    })
    ctx_html = PluginContext(config={"game_id": 1}, http=http_html)
    searcher_html = cf_search.Search(ctx_html)
    res_html = searcher_html.search("bagnon", platform=None, limit=5)
    assert len(res_html) == 2
    assert res_html[0].source_id == "bagnon"
    assert res_html[0].title == "Bagnon"
    assert res_html[0].url == "https://www.curseforge.com/wow/addons/bagnon"


def test_curseforge_import_api_download_url():
    files_response = {
        "data": [
            {
                "id": 4567890,
                "fileName": "DBM-Core-10.2.1.zip",
                "fileLength": 1048576,
                "downloadUrl": None,
            }
        ]
    }
    dl_url_response = {
        "data": "https://edge.forgecdn.net/files/4567/890/DBM-Core-10.2.1.zip"
    }

    http = MockHttp({
        "https://api.curseforge.com/v1/mods/3358/files": HttpResponse(
            status_code=200, text=json.dumps(files_response)
        ),
        "https://api.curseforge.com/v1/mods/3358/files/4567890/download-url": HttpResponse(
            status_code=200, text=json.dumps(dl_url_response)
        ),
    })
    ctx = PluginContext(config={"api_key": "test_key"}, http=http)
    importer = cf_importer.Importer(ctx)

    sr = SearchResult(
        source_id="3358",
        title="DBM",
        platform="World of Warcraft",
        extra={"mod_id": "3358"},
    )
    plan = importer.plan(sr)

    assert isinstance(plan, FetchPlan)
    assert plan.collection == "CurseForge"
    assert plan.platform == "World of Warcraft"
    assert len(plan.files) == 1
    f = plan.files[0]
    assert f.url == "https://edge.forgecdn.net/files/4567/890/DBM-Core-10.2.1.zip"
    assert f.filename == "DBM-Core-10.2.1.zip"
    assert f.size_bytes == 1048576
    assert bare_filename(f.filename) == f.filename


def test_curseforge_import_forgecdn_formula_fallback_and_filename_sanitization():
    # Test ForgeCDN formula fallback: https://edge.forgecdn.net/files/{file_id // 1000}/{file_id % 1000}/{file_name}
    # and filename sanitization on dangerous/dirty filename
    file_id = 5123456
    raw_bad_filename = "Cool Mod: The [Best] Edition (v1.0) / CON.zip"
    files_response = {
        "data": [
            {
                "id": file_id,
                "fileName": raw_bad_filename,
                "fileLength": 500000,
                "downloadUrl": None,
            }
        ]
    }

    http = MockHttp({
        f"https://api.curseforge.com/v1/mods/12345/files": HttpResponse(
            status_code=200, text=json.dumps(files_response)
        ),
        # download-url returns 404/null to trigger formula fallback
        f"https://api.curseforge.com/v1/mods/12345/files/{file_id}/download-url": HttpResponse(
            status_code=404, text=""
        ),
    })
    ctx = PluginContext(config={"api_key": "key"}, http=http)
    importer = cf_importer.Importer(ctx)

    sr = SearchResult(
        source_id="12345",
        title="Cool Mod",
        platform="Minecraft",
        extra={"mod_id": "12345"},
    )
    plan = importer.plan(sr)

    assert len(plan.files) == 1
    f = plan.files[0]
    part1 = file_id // 1000
    part2 = file_id % 1000
    expected_url_prefix = f"https://edge.forgecdn.net/files/{part1}/{part2}/"
    assert f.url.startswith(expected_url_prefix)
    # Validate filename was sanitized and passes bare_filename
    assert bare_filename(f.filename) == f.filename
    assert ":" not in f.filename
    assert "/" not in f.filename


def test_curseforge_stream():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = cf_stream.Stream(ctx)
    sr = SearchResult(
        source_id="3358",
        title="Deadly Boss Mods",
        url="https://www.curseforge.com/wow/addons/deadly-boss-mods",
        extra={"slug": "deadly-boss-mods"},
    )
    target = streamer.resolve(sr)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://www.curseforge.com/wow/addons/deadly-boss-mods"
    assert target.mime_type == "text/html"


# ==============================================================================
# ModDB Plugin Tests
# ==============================================================================

def test_moddb_search_with_game_slug_and_prefix():
    html_response = """
    <html>
      <body>
        <div class="row rowcontent">
          <h4><a href="/mods/plugy-the-survival-kit">PlugY, The Survival Kit</a></h4>
          <p>PlugY is a plugin for Diablo II Lord of Destruction that extends your stash.</p>
        </div>
      </body>
    </html>
    """
    http = MockHttp({
        "https://www.moddb.com/games/diablo-2-lod/mods": HttpResponse(
            status_code=200, text=html_response
        )
    })
    ctx = PluginContext(config={"game_slug": "diablo-2-lod"}, http=http)
    searcher = moddb_search.Search(ctx)

    results = searcher.search("plugy", platform=None, limit=5)
    assert len(results) == 1
    res = results[0]
    assert res.source_id == "plugy-the-survival-kit"
    assert res.title == "PlugY, The Survival Kit"
    assert res.url == "https://www.moddb.com/mods/plugy-the-survival-kit"
    assert res.platform == "diablo-2-lod"
    assert "extends your stash" in res.extra["summary"]

    # Verify query prefix overrides config game_slug
    http_prefix = MockHttp({
        "https://www.moddb.com/games/half-life/mods": HttpResponse(
            status_code=200, text=html_response
        )
    })
    ctx_prefix = PluginContext(config={"game_slug": "diablo-2-lod"}, http=http_prefix)
    searcher_prefix = moddb_search.Search(ctx_prefix)
    results_p = searcher_prefix.search("half-life:cs", platform=None, limit=5)
    last_call = http_prefix.calls[-1]
    assert last_call["url"] == "https://www.moddb.com/games/half-life/mods"
    assert last_call["params"]["kw"] == "cs"


def test_moddb_search_global_when_empty_game_slug():
    html_response = """
    <div class="table">
      <a href="/mods/arcane-dimensions">Arcane Dimensions</a>
      <p>Total conversion for Quake.</p>
    </div>
    """
    http = MockHttp({
        "https://www.moddb.com/mods": HttpResponse(status_code=200, text=html_response)
    })
    ctx = PluginContext(config={"game_slug": ""}, http=http)
    searcher = moddb_search.Search(ctx)
    results = searcher.search("arcane", platform=None, limit=5)
    assert len(results) == 1
    assert results[0].source_id == "arcane-dimensions"
    assert results[0].url == "https://www.moddb.com/mods/arcane-dimensions"


def test_moddb_import_flow_and_mirror_resolution():
    downloads_html = """
    <html>
      <body>
        <div class="downloads">
          <a href="/downloads/start/112233">Download PlugY v14.02 Installer</a>
          <a href="/downloads/112234-plugy-zip">Download PlugY v14.02 Zip Package</a>
        </div>
      </body>
    </html>
    """
    start_html_1 = """
    <html>
      <body>
        <p>Your download will start shortly.</p>
        <a href="/downloads/mirror/112233/1/PlugY_The_Survival_Kit_v14.02.exe">Click here if download does not start</a>
      </body>
    </html>
    """
    start_html_2 = """
    <html>
      <body>
        <a href="https://www.moddb.com/downloads/mirror/112234/2/PlugY_v14.02:package.zip">Direct mirror link</a>
      </body>
    </html>
    """

    http = MockHttp({
        "https://www.moddb.com/mods/plugy/downloads": HttpResponse(
            status_code=200, text=downloads_html
        ),
        "https://www.moddb.com/downloads/start/112233": HttpResponse(
            status_code=200, text=start_html_1
        ),
        "https://www.moddb.com/downloads/start/112234": HttpResponse(
            status_code=200, text=start_html_2
        ),
    })

    ctx = PluginContext(config={}, http=http)
    importer = moddb_importer.Importer(ctx)
    sr = SearchResult(
        source_id="plugy",
        title="PlugY",
        platform="diablo-2-lod",
        extra={"slug": "plugy"},
    )

    plan = importer.plan(sr)
    assert isinstance(plan, FetchPlan)
    assert plan.collection == "ModDB"
    assert plan.platform == "diablo-2-lod"
    assert len(plan.files) == 2

    f1 = plan.files[0]
    assert f1.url == "https://www.moddb.com/downloads/mirror/112233/1/PlugY_The_Survival_Kit_v14.02.exe"
    assert f1.filename == "PlugY_The_Survival_Kit_v14.02.exe"
    assert bare_filename(f1.filename) == f1.filename

    f2 = plan.files[1]
    assert f2.url == "https://www.moddb.com/downloads/mirror/112234/2/PlugY_v14.02:package.zip"
    # Ensure ':' in filename is sanitized
    assert ":" not in f2.filename
    assert bare_filename(f2.filename) == f2.filename


def test_moddb_stream():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = moddb_stream.Stream(ctx)
    sr = SearchResult(
        source_id="plugy",
        title="PlugY",
        extra={"slug": "plugy"},
    )
    target = streamer.resolve(sr)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://www.moddb.com/mods/plugy"
    assert target.mime_type == "text/html"


# ==============================================================================
# Nexus Mods Plugin Tests
# ==============================================================================

def test_nexusmods_search_graphql():
    graphql_response = {
        "data": {
            "mods": {
                "nodes": [
                    {
                        "modId": 1,
                        "name": "D2R Remastered Mod",
                        "summary": "Graphics and inventory enhancements for D2R.",
                        "game": {
                            "domainName": "diablo2resurrected",
                            "name": "Diablo II: Resurrected",
                        },
                        "pictureUrl": "https://images.nexusmods.com/test.jpg",
                    }
                ]
            }
        }
    }

    http = MockHttp({
        ("POST", "https://api.nexusmods.com/v2/graphql"): HttpResponse(
            status_code=200, text=json.dumps(graphql_response)
        )
    })
    ctx = PluginContext(config={"game_domain": "diablo2resurrected"}, http=http)
    searcher = nm_search.Search(ctx)

    results = searcher.search("remastered", platform=None, limit=5)
    assert len(results) == 1
    res = results[0]
    assert res.source_id == "diablo2resurrected/1"
    assert res.title == "D2R Remastered Mod"
    assert res.platform == "diablo2resurrected"
    assert res.url == "https://www.nexusmods.com/diablo2resurrected/mods/1"
    assert res.extra["mod_id"] == "1"

    # Verify GraphQL POST body variables
    call = http.calls[0]
    assert call["method"] == "POST"
    payload = call["json"]
    assert payload["variables"]["filter"]["name"]["value"] == "remastered"
    assert payload["variables"]["filter"]["gameDomainName"]["value"] == "diablo2resurrected"


def test_nexusmods_search_query_prefix():
    http = MockHttp({
        ("POST", "https://api.nexusmods.com/v2/graphql"): HttpResponse(
            status_code=200, text=json.dumps({"data": {"mods": {"nodes": []}}})
        )
    })
    ctx = PluginContext(config={"game_domain": "diablo2resurrected"}, http=http)
    searcher = nm_search.Search(ctx)

    searcher.search("skyrimspecialedition:skyui", platform=None, limit=5)
    call = http.calls[-1]
    payload = call["json"]
    assert payload["variables"]["filter"]["gameDomainName"]["value"] == "skyrimspecialedition"
    assert payload["variables"]["filter"]["name"]["value"] == "skyui"


def test_nexusmods_import_with_api_key():
    files_response = {
        "files": [
            {
                "file_id": 101,
                "name": "D2R Stash Mod (Main)",
                "file_name": "D2R_Stash_v1.0.zip",
                "size_in_bytes": 204800,
            }
        ]
    }
    dl_response = [
        {
            "name": "Nexus CDN",
            "URI": "https://cf-files.nexusmods.com/files/101/D2R_Stash_v1.0.zip",
        }
    ]

    http = MockHttp({
        "https://api.nexusmods.com/v1/games/diablo2resurrected/mods/1/files.json": HttpResponse(
            status_code=200, text=json.dumps(files_response)
        ),
        "https://api.nexusmods.com/v1/games/diablo2resurrected/mods/1/files/101/download_link.json": HttpResponse(
            status_code=200, text=json.dumps(dl_response)
        ),
    })

    ctx = PluginContext(
        config={"api_key": "premium_key", "game_domain": "diablo2resurrected"},
        http=http,
    )
    importer = nm_importer.Importer(ctx)

    sr = SearchResult(
        source_id="diablo2resurrected/1",
        title="D2R Stash",
        platform="diablo2resurrected",
    )
    plan = importer.plan(sr)

    assert isinstance(plan, FetchPlan)
    assert plan.collection == "Nexus Mods"
    assert plan.platform == "diablo2resurrected"
    assert len(plan.files) == 1
    f = plan.files[0]
    assert f.url == "https://cf-files.nexusmods.com/files/101/D2R_Stash_v1.0.zip"
    assert f.filename == "D2R_Stash_v1.0.zip"
    assert f.size_bytes == 204800
    assert bare_filename(f.filename) == f.filename


def test_nexusmods_import_missing_api_key_refusal():
    ctx = PluginContext(
        config={"api_key": "", "game_domain": "diablo2resurrected"},
        http=MockHttp(),
    )
    importer = nm_importer.Importer(ctx)
    sr = SearchResult(
        source_id="diablo2resurrected/55",
        title="Some Mod",
    )

    with pytest.raises(nm_importer.ImportRefused) as exc_info:
        importer.plan(sr)

    msg = str(exc_info.value)
    assert "Nexus Mods API key is not configured" in msg
    assert "Stream" in msg
    assert "https://www.nexusmods.com/diablo2resurrected/mods/55?tab=files" in msg


def test_nexusmods_import_403_free_account_refusal():
    http = MockHttp({
        "https://api.nexusmods.com/v1/games/diablo2resurrected/mods/55/files.json": HttpResponse(
            status_code=403, text='{"message": "API file download requires Premium"}'
        ),
    })
    ctx = PluginContext(
        config={"api_key": "free_user_key", "game_domain": "diablo2resurrected"},
        http=http,
    )
    importer = nm_importer.Importer(ctx)
    sr = SearchResult(
        source_id="diablo2resurrected/55",
        title="Some Mod",
    )

    with pytest.raises(nm_importer.ImportRefused) as exc_info:
        importer.plan(sr)

    msg = str(exc_info.value)
    assert "HTTP 403" in msg
    assert "Stream" in msg
    assert "https://www.nexusmods.com/diablo2resurrected/mods/55?tab=files" in msg


def test_nexusmods_stream():
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = nm_stream.Stream(ctx)
    sr = SearchResult(
        source_id="skyrimspecialedition/1234",
        title="SkyUI",
        platform="skyrimspecialedition",
    )
    target = streamer.resolve(sr)
    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert (
        target.target
        == "https://www.nexusmods.com/skyrimspecialedition/mods/1234?tab=files"
    )
    assert target.mime_type == "text/html"


def test_curseforge_import_deduplicates_filenames():
    files_response = {
        "data": [
            {
                "id": 1001,
                "fileName": "addon.zip",
                "fileLength": 1000,
                "downloadUrl": "https://edge.forgecdn.net/files/1/1/addon.zip",
            },
            {
                "id": 1002,
                "fileName": "addon.zip",
                "fileLength": 1000,
                "downloadUrl": "https://edge.forgecdn.net/files/1/2/addon.zip",
            },
        ]
    }
    http = MockHttp({
        "https://api.curseforge.com/v1/mods/100/files": HttpResponse(
            status_code=200, text=json.dumps(files_response)
        )
    })
    ctx = PluginContext(config={"api_key": "k"}, http=http)
    importer = cf_importer.Importer(ctx)
    sr = SearchResult(source_id="100", title="Addon")
    plan = importer.plan(sr)
    assert len(plan.files) == 2
    filenames = [f.filename for f in plan.files]
    assert len(set(filenames)) == 2
    assert "addon.zip" in filenames
    assert "addon_1.zip" in filenames


def test_curseforge_import_no_files_refused():
    http = MockHttp({
        "https://api.curseforge.com/v1/mods/100/files": HttpResponse(
            status_code=200, text=json.dumps({"data": []})
        )
    })
    ctx = PluginContext(config={"api_key": "k"}, http=http)
    importer = cf_importer.Importer(ctx)
    sr = SearchResult(source_id="100", title="Addon")
    with pytest.raises(cf_importer.ImportRefused, match="no files"):
        importer.plan(sr)


def test_moddb_import_no_downloads_refused():
    http = MockHttp({
        "https://www.moddb.com/mods/empty/downloads": HttpResponse(
            status_code=200, text="<html><body>No downloads yet</body></html>"
        )
    })
    ctx = PluginContext(config={}, http=http)
    importer = moddb_importer.Importer(ctx)
    sr = SearchResult(source_id="empty", title="Empty Mod")
    with pytest.raises(moddb_importer.ImportRefused, match="No downloads found"):
        importer.plan(sr)


def test_nexusmods_import_no_files_refused():
    http = MockHttp({
        "https://api.nexusmods.com/v1/games/d2r/mods/1/files.json": HttpResponse(
            status_code=200, text=json.dumps({"files": []})
        )
    })
    ctx = PluginContext(config={"api_key": "k", "game_domain": "d2r"}, http=http)
    importer = nm_importer.Importer(ctx)
    sr = SearchResult(source_id="d2r/1", title="Empty Mod")
    with pytest.raises(nm_importer.ImportRefused, match="lists no downloadable files"):
        importer.plan(sr)


# ==============================================================================
# Filename Sanitization Edge Cases across all 3 plugins
# ==============================================================================

@pytest.mark.parametrize(
    "safe_fn",
    [cf_safe_filename, moddb_safe_filename, nm_safe_filename],
)
def test_safe_filename_edge_cases(safe_fn):
    # Invalid characters: colons, slashes, brackets, reserved stems, trailing dots
    dirty_names = [
        ("C:\\Program Files\\mod:cool.zip", "mod_cool.zip"),
        ("CON.zip", "_CON.zip"),
        ("NUL.tar.gz", "_NUL.tar.gz"),
        ("aux.bin", "_aux.bin"),
        ("mod/sub/file?.bin", "file_.bin"),
        ("trailing.dot.", "trailing.dot"),
        ("spaces at end   ", "spaces at end"),
        ("...dots...", "dots"),
        ("a" * 300 + ".zip", "a" * 195 + ".zip"),
        ("normal_file-name (v1.0).zip", "normal_file-name (v1.0).zip"),
    ]
    for raw, _ in dirty_names:
        sanitized = safe_fn(raw)
        # Every sanitized name must pass bare_filename validation without raising!
        assert bare_filename(sanitized) == sanitized
