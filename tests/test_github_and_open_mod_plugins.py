"""Comprehensive unit tests for the 4 new RPP v1 plugins:
- github-games
- modrinth
- thunderstore
- gamebanana

Validates load_manifest(), Search, Import (FetchPlan & netpolicy validation),
and Stream (StreamTarget & netpolicy validation) with mocked ctx.http.
"""

import importlib
import json
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DEV = REPO_ROOT / "plugins-dev"

for plugin_dir in ["github-games", "modrinth", "thunderstore", "gamebanana"]:
    path_str = str(PLUGINS_DEV / plugin_dir)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from rom_hub.manifest import load_manifest
from rom_hub.netpolicy import check_url, url_allowed
from rom_hub.types import FetchPlan, SearchResult, StreamTarget, bare_filename
from rom_hub_sdk import ImportProvider, SearchProvider, StreamProvider
from rom_hub_sdk.context import HttpResponse, PluginContext

import github_games.search
import github_games.importer
import github_games.stream
import github_games.filenames

import modrinth.search
import modrinth.importer
import modrinth.stream
import modrinth.filenames

import thunderstore.search
import thunderstore.importer
import thunderstore.stream
import thunderstore.filenames

import gamebanana.search
import gamebanana.importer
import gamebanana.stream
import gamebanana.filenames


class MockHttp:
    """Mock HTTP client that routes responses based on URL matching and records calls."""

    def __init__(self, routes: dict[str, tuple[int, dict | list | str]] | None = None):
        self.routes = routes or {}
        self.calls: list[dict] = []

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> HttpResponse:
        self.calls.append({"method": "GET", "url": url, "params": params or {}, "headers": headers or {}})

        # Exact match or prefix match
        if url in self.routes:
            status, body = self.routes[url]
        else:
            matched = False
            for route_prefix, (status, body) in self.routes.items():
                if url.startswith(route_prefix):
                    matched = True
                    break
            if not matched:
                return HttpResponse(status_code=404, text=json.dumps({"error": "not found"}))

        if isinstance(body, (dict, list)):
            text = json.dumps(body)
        else:
            text = str(body)

        return HttpResponse(status_code=status, text=text)


# ============================================================================
# 1. Manifest Loading and Schema Tests
# ============================================================================

@pytest.mark.parametrize(
    "slug,expected_name,expected_caps,expected_net",
    [
        (
            "github-games",
            "GitHub Open-Source Games, Engines & Bots",
            {"search", "importer", "stream"},
            {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com", "codeload.github.com"},
        ),
        (
            "modrinth",
            "Modrinth Mods, Modpacks & Shaders",
            {"search", "importer", "stream"},
            {"api.modrinth.com", "cdn.modrinth.com", "modrinth.com"},
        ),
        (
            "thunderstore",
            "Thunderstore Game Mods (100+ Games)",
            {"search", "importer", "stream"},
            {"thunderstore.io", "cdn.thunderstore.io", "*.thunderstore.io"},
        ),
        (
            "gamebanana",
            "GameBanana Mods, Skins & Tools",
            {"search", "importer", "stream"},
            {"gamebanana.com", "*.gamebanana.com"},
        ),
    ],
)
def test_manifest_loads_and_verifies(slug, expected_name, expected_caps, expected_net):
    manifest_path = PLUGINS_DEV / slug / "manifest.toml"
    assert manifest_path.exists(), f"manifest.toml missing for {slug}"

    m = load_manifest(manifest_path)
    assert m.slug == slug
    assert m.name == expected_name
    assert m.rpp_version == "1"
    assert m.version == "0.1.0"
    assert m.license == "MIT"
    assert set(m.capabilities.keys()) == expected_caps
    assert set(m.network) == expected_net

    # Verify entrypoints resolve to actual Capability classes
    for cap_name, ep in m.capabilities.items():
        mod_name, cls_name = ep.split(":")
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        if cap_name == "search":
            assert issubclass(cls, SearchProvider)
        elif cap_name == "importer":
            assert issubclass(cls, ImportProvider)
        elif cap_name == "stream":
            assert issubclass(cls, StreamProvider)


# ============================================================================
# 2. GitHub Games Plugin Tests
# ============================================================================

def test_github_games_search_enrichment_and_auth():
    manifest = load_manifest(PLUGINS_DEV / "github-games" / "manifest.toml")

    fake_items = [
        {
            "id": 101,
            "name": "open-doom",
            "full_name": "id-community/open-doom",
            "owner": {"login": "id-community"},
            "stargazers_count": 1420,
            "language": "C",
            "size": 5120,
            "html_url": "https://github.com/id-community/open-doom",
        }
    ]

    mock_http = MockHttp({
        "https://api.github.com/search/repositories": (200, {"items": fake_items})
    })

    # Test 'games' mode adds topic enrichment
    ctx = PluginContext(
        config={"mode": "games", "github_token": "ghp_secret123"},
        http=mock_http,
    )
    searcher = github_games.search.Search(ctx)
    results = searcher.search("doom", platform="pc", limit=5)

    assert len(results) == 1
    r = results[0]
    assert r.source_id == "id-community/open-doom"
    assert r.title == "open-doom"
    assert r.platform == "pc"
    assert r.url == "https://github.com/id-community/open-doom"
    assert r.size_bytes == 5120 * 1024
    assert r.extra["stars"] == "1420"
    assert r.extra["language"] == "C"

    # Verify HTTP call and headers
    call = mock_http.calls[0]
    assert call["headers"].get("Authorization") == "Bearer ghp_secret123"
    assert "topic:game OR topic:source-port" in call["params"]["q"]
    assert "doom" in call["params"]["q"]


@pytest.mark.parametrize("mode,expected_token", [
    ("bots", "topic:bot OR game bot"),
    ("tools", "topic:mod-manager OR topic:modding"),
    ("all", ""),
])
def test_github_games_search_modes(mode, expected_token):
    mock_http = MockHttp({
        "https://api.github.com/search/repositories": (200, {"items": []})
    })
    ctx = PluginContext(config={"mode": mode}, http=mock_http)
    searcher = github_games.search.Search(ctx)
    searcher.search("tester", platform="pc", limit=5)

    call = mock_http.calls[0]
    q = call["params"]["q"]
    assert "tester" in q
    if expected_token:
        assert expected_token in q


def test_github_games_importer_release_assets_and_source():
    manifest = load_manifest(PLUGINS_DEV / "github-games" / "manifest.toml")

    rel_url = "https://api.github.com/repos/id-community/open-doom/releases/latest"
    repo_url = "https://api.github.com/repos/id-community/open-doom"

    mock_http = MockHttp({
        rel_url: (200, {
            "assets": [
                {
                    "name": "open-doom-x86_64.zip",
                    "browser_download_url": "https://github.com/id-community/open-doom/releases/download/v1.0/open-doom-x86_64.zip",
                    "size": 1048576,
                },
                {
                    "name": "open-doom-win64.zip",
                    "browser_download_url": "https://github.com/id-community/open-doom/releases/download/v1.0/open-doom-win64.zip",
                    "size": 2097152,
                },
            ]
        }),
        repo_url: (200, {"default_branch": "master"}),
    })

    ctx = PluginContext(config={"include_source": True}, http=mock_http)
    importer = github_games.importer.Importer(ctx)

    sr = SearchResult(
        source_id="id-community/open-doom",
        title="open-doom",
        platform="pc",
    )
    plan = importer.plan(sr)

    assert isinstance(plan, FetchPlan)
    assert plan.platform == "pc"
    assert len(plan.files) == 3  # 2 binary assets + 1 source zip

    # Validate netpolicy: every URL in plan must be allowed by manifest
    for f in plan.files:
        check_url(f.url, manifest.network)
        bare_filename(f.filename)

    assert plan.files[0].filename == "open-doom-x86_64.zip"
    assert plan.files[1].filename == "open-doom-win64.zip"
    assert plan.files[2].filename == "open-doom-source.zip"
    assert plan.files[2].url == "https://codeload.github.com/id-community/open-doom/zip/refs/heads/master"


def test_github_games_importer_no_assets_fallback_source():
    manifest = load_manifest(PLUGINS_DEV / "github-games" / "manifest.toml")

    mock_http = MockHttp({
        "https://api.github.com/repos/foo/bar/releases/latest": (404, {"message": "Not Found"}),
        "https://api.github.com/repos/foo/bar": (200, {"default_branch": "main"}),
    })

    ctx = PluginContext(config={"include_source": False}, http=mock_http)
    importer = github_games.importer.Importer(ctx)
    sr = SearchResult(source_id="foo/bar", title="bar", platform="pc")
    plan = importer.plan(sr)

    # When no binary assets exist, source zip is included regardless of include_source
    assert len(plan.files) == 1
    assert plan.files[0].filename == "bar-source.zip"
    assert plan.files[0].url == "https://codeload.github.com/foo/bar/zip/refs/heads/main"
    check_url(plan.files[0].url, manifest.network)


def test_github_games_stream():
    manifest = load_manifest(PLUGINS_DEV / "github-games" / "manifest.toml")
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = github_games.stream.Stream(ctx)

    sr = SearchResult(source_id="owner/repo", title="Cool Game", platform="pc")
    target = streamer.resolve(sr)

    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://github.com/owner/repo"
    assert target.mime_type == "text/html"
    check_url(target.target, manifest.network)


# ============================================================================
# 3. Modrinth Plugin Tests
# ============================================================================

def test_modrinth_search_facets_and_results():
    manifest = load_manifest(PLUGINS_DEV / "modrinth" / "manifest.toml")

    fake_hits = [
        {
            "slug": "fabric-api",
            "title": "Fabric API",
            "description": "Essential hooks for Fabric mods.",
            "project_type": "mod",
            "author": "modmuss50",
            "downloads": 5432100,
        }
    ]

    mock_http = MockHttp({
        "https://api.modrinth.com/v2/search": (200, {"hits": fake_hits, "total_hits": 1})
    })

    ctx = PluginContext(config={"project_type": "mod"}, http=mock_http)
    searcher = modrinth.search.Search(ctx)
    results = searcher.search("fabric", platform="pc", limit=10)

    assert len(results) == 1
    r = results[0]
    assert r.source_id == "fabric-api"
    assert r.title == "Fabric API"
    assert r.platform == "pc"
    assert r.url == "https://modrinth.com/project/fabric-api"
    assert r.extra["project_type"] == "mod"
    assert r.extra["author"] == "modmuss50"

    call = mock_http.calls[0]
    assert call["headers"].get("User-Agent") == "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"
    assert 'project_type:mod' in call["params"]["facets"]


def test_modrinth_importer_latest_version_files():
    manifest = load_manifest(PLUGINS_DEV / "modrinth" / "manifest.toml")

    fake_versions = [
        {
            "id": "v1.2.3",
            "name": "Fabric API 1.2.3",
            "files": [
                {
                    "url": "https://cdn.modrinth.com/data/P7dR8mSH/versions/v1.2.3/fabric-api-1.2.3.jar",
                    "filename": "fabric-api-1.2.3.jar",
                    "size": 1823456,
                    "primary": True,
                },
                {
                    "url": "https://cdn.modrinth.com/data/P7dR8mSH/versions/v1.2.3/fabric-api-1.2.3-sources.jar",
                    "filename": "fabric-api-1.2.3-sources.jar",
                    "size": 912345,
                    "primary": False,
                },
            ],
        }
    ]

    mock_http = MockHttp({
        "https://api.modrinth.com/v2/project/fabric-api/version": (200, fake_versions)
    })

    ctx = PluginContext(config={}, http=mock_http)
    importer = modrinth.importer.Importer(ctx)

    sr = SearchResult(source_id="fabric-api", title="Fabric API", platform="pc")
    plan = importer.plan(sr)

    assert isinstance(plan, FetchPlan)
    assert plan.platform == "pc"
    assert plan.collection == "Modrinth"
    assert len(plan.files) == 2

    for f in plan.files:
        check_url(f.url, manifest.network)
        bare_filename(f.filename)

    assert plan.files[0].filename == "fabric-api-1.2.3.jar"
    assert plan.files[0].size_bytes == 1823456
    assert plan.files[1].filename == "fabric-api-1.2.3-sources.jar"


def test_modrinth_stream():
    manifest = load_manifest(PLUGINS_DEV / "modrinth" / "manifest.toml")
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = modrinth.stream.Stream(ctx)

    sr = SearchResult(source_id="sodium", title="Sodium", platform="pc")
    target = streamer.resolve(sr)

    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://modrinth.com/project/sodium"
    assert target.mime_type == "text/html"
    check_url(target.target, manifest.network)


# ============================================================================
# 4. Thunderstore Plugin Tests
# ============================================================================

def test_thunderstore_search_community_and_syntax():
    manifest = load_manifest(PLUGINS_DEV / "thunderstore" / "manifest.toml")

    fake_pkgs = [
        {
            "name": "ValheimPlus",
            "full_name": "Grantapher-ValheimPlus-0.9.9",
            "owner": "Grantapher",
            "package_url": "https://thunderstore.io/c/valheim/p/Grantapher/ValheimPlus/",
            "description": "An advanced gameplay mod for Valheim.",
            "versions": [
                {
                    "version_number": "0.9.9",
                    "file_size": 2500000,
                    "download_url": "https://thunderstore.io/package/download/Grantapher/ValheimPlus/0.9.9/",
                }
            ],
        },
        {
            "name": "BiggerChests",
            "full_name": "Author-BiggerChests-1.0.0",
            "owner": "Author",
            "package_url": "https://thunderstore.io/c/valheim/p/Author/BiggerChests/",
            "description": "Increases chest size.",
            "versions": [
                {
                    "version_number": "1.0.0",
                    "file_size": 120000,
                    "download_url": "https://thunderstore.io/package/download/Author/BiggerChests/1.0.0/",
                }
            ],
        },
    ]

    mock_http = MockHttp({
        "https://thunderstore.io/c/valheim/api/v1/package/": (200, fake_pkgs),
        "https://thunderstore.io/c/lethal-company/api/v1/package/": (200, [
            {
                "name": "MoreSuits",
                "full_name": "x753-MoreSuits-1.4.1",
                "owner": "x753",
                "package_url": "https://thunderstore.io/c/lethal-company/p/x753/MoreSuits/",
                "description": "Adds more suits to the rack.",
                "versions": [
                    {
                        "version_number": "1.4.1",
                        "file_size": 340000,
                        "download_url": "https://thunderstore.io/package/download/x753/MoreSuits/1.4.1/",
                    }
                ],
            }
        ]),
    })

    # Test standard search in config community
    ctx = PluginContext(config={"community": "valheim"}, http=mock_http)
    searcher = thunderstore.search.Search(ctx)
    results = searcher.search("valheimplus", platform="pc", limit=5)
    assert len(results) == 1
    assert results[0].source_id == "valheim:Grantapher/ValheimPlus"
    assert results[0].title == "ValheimPlus"
    assert results[0].size_bytes == 2500000

    # Test "community:query" syntax override
    results2 = searcher.search("lethal-company:suits", platform="pc", limit=5)
    assert len(results2) == 1
    assert results2[0].source_id == "lethal-company:x753/MoreSuits"
    assert results2[0].title == "MoreSuits"


def test_thunderstore_importer():
    manifest = load_manifest(PLUGINS_DEV / "thunderstore" / "manifest.toml")

    fake_pkgs = [
        {
            "name": "ValheimPlus",
            "owner": "Grantapher",
            "versions": [
                {
                    "version_number": "0.9.9",
                    "file_size": 2500000,
                    "download_url": "https://thunderstore.io/package/download/Grantapher/ValheimPlus/0.9.9/",
                }
            ],
        }
    ]

    mock_http = MockHttp({
        "https://thunderstore.io/c/valheim/api/v1/package/": (200, fake_pkgs)
    })

    ctx = PluginContext(config={"community": "valheim"}, http=mock_http)
    importer = thunderstore.importer.Importer(ctx)

    sr = SearchResult(
        source_id="valheim:Grantapher/ValheimPlus",
        title="ValheimPlus",
        platform="pc",
    )
    plan = importer.plan(sr)

    assert isinstance(plan, FetchPlan)
    assert plan.platform == "pc"
    assert plan.collection == "Thunderstore - valheim"
    assert len(plan.files) == 1

    f = plan.files[0]
    check_url(f.url, manifest.network)
    bare_filename(f.filename)

    assert f.url == "https://thunderstore.io/package/download/Grantapher/ValheimPlus/0.9.9/"
    assert f.filename == "Grantapher-ValheimPlus-0.9.9.zip"
    assert f.size_bytes == 2500000


def test_thunderstore_stream():
    manifest = load_manifest(PLUGINS_DEV / "thunderstore" / "manifest.toml")
    ctx = PluginContext(config={"community": "valheim"}, http=MockHttp())
    streamer = thunderstore.stream.Stream(ctx)

    sr = SearchResult(source_id="valheim:Grantapher/ValheimPlus", title="ValheimPlus", platform="pc")
    target = streamer.resolve(sr)

    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://thunderstore.io/c/valheim/p/Grantapher/ValheimPlus/"
    assert target.mime_type == "text/html"
    check_url(target.target, manifest.network)


# ============================================================================
# 5. GameBanana Plugin Tests
# ============================================================================

def test_gamebanana_search():
    manifest = load_manifest(PLUGINS_DEV / "gamebanana" / "manifest.toml")

    fake_records = [
        {
            "_idRow": 45678,
            "_sModelName": "Mod",
            "_sName": "HD Textures Pack",
            "_sProfileUrl": "https://gamebanana.com/mods/45678",
            "_aGame": {"_idRow": 8, "_sName": "Smash Ultimate"},
            "_aSubmitter": {"_sName": "ModderOne"},
        }
    ]

    mock_http = MockHttp({
        "https://gamebanana.com/apiv11/Util/Search/Results": (200, {"_aRecords": fake_records})
    })

    ctx = PluginContext(config={"game_id": "8", "models": "Mod,Skin"}, http=mock_http)
    searcher = gamebanana.search.Search(ctx)
    results = searcher.search("textures", platform="pc", limit=5)

    assert len(results) == 1
    r = results[0]
    assert r.source_id == "Mod:45678"
    assert r.title == "HD Textures Pack"
    assert r.platform == "pc"
    assert r.url == "https://gamebanana.com/mods/45678"
    assert r.extra["model"] == "Mod"
    assert r.extra["id"] == "45678"
    assert r.extra["game"] == "Smash Ultimate"
    assert r.extra["author"] == "ModderOne"

    call = mock_http.calls[0]
    assert call["params"]["_sSearchString"] == "textures"
    assert call["params"]["_csvModelNames"] == "Mod,Skin"
    assert call["params"]["_idGameRow"] == "8"


def test_gamebanana_importer_all_files():
    manifest = load_manifest(PLUGINS_DEV / "gamebanana" / "manifest.toml")

    fake_profile = {
        "_aFiles": [
            {
                "_idRow": 1001,
                "_sFile": "textures_part1.7z",
                "_sDownloadUrl": "https://gamebanana.com/dl/1001",
                "_nFilesize": 50000000,
            },
            {
                "_idRow": 1002,
                "_sFile": "textures_part2.7z",
                "_sDownloadUrl": "https://files.gamebanana.com/dl/1002",
                "_nFilesize": 48000000,
            },
        ]
    }

    mock_http = MockHttp({
        "https://gamebanana.com/apiv11/Mod/45678/ProfilePage": (200, fake_profile)
    })

    ctx = PluginContext(config={}, http=mock_http)
    importer = gamebanana.importer.Importer(ctx)

    sr = SearchResult(
        source_id="Mod:45678",
        title="HD Textures Pack",
        platform="pc",
        extra={"game": "Smash Ultimate"},
    )
    plan = importer.plan(sr)

    assert isinstance(plan, FetchPlan)
    assert plan.platform == "pc"
    assert plan.collection == "GameBanana - Smash Ultimate"
    assert len(plan.files) == 2

    for f in plan.files:
        check_url(f.url, manifest.network)
        bare_filename(f.filename)

    assert plan.files[0].filename == "textures_part1.7z"
    assert plan.files[0].url == "https://gamebanana.com/dl/1001"
    assert plan.files[1].filename == "textures_part2.7z"
    assert plan.files[1].url == "https://files.gamebanana.com/dl/1002"


def test_gamebanana_stream():
    manifest = load_manifest(PLUGINS_DEV / "gamebanana" / "manifest.toml")
    ctx = PluginContext(config={}, http=MockHttp())
    streamer = gamebanana.stream.Stream(ctx)

    sr = SearchResult(source_id="Mod:45678", title="HD Textures Pack", platform="pc")
    target = streamer.resolve(sr)

    assert isinstance(target, StreamTarget)
    assert target.kind == "url"
    assert target.target == "https://gamebanana.com/mods/45678"
    assert target.mime_type == "text/html"
    check_url(target.target, manifest.network)


# ============================================================================
# 6. Filename Sanitisation & Deduplication Edge Cases
# ============================================================================

@pytest.mark.parametrize("mod", [
    github_games.filenames,
    modrinth.filenames,
    thunderstore.filenames,
    gamebanana.filenames,
])
def test_filenames_sanitization_edge_cases(mod):
    # Windows device stem
    safe = mod.safe_filename("NUL.zip")
    assert bare_filename(safe)
    assert safe == "_NUL.zip"

    # Trailing dots/spaces
    safe2 = mod.safe_filename("bad.name...  ")
    assert bare_filename(safe2)
    assert not safe2.endswith((".", " "))

    # Path traversal and slashes
    safe3 = mod.safe_filename("../../../etc/passwd.txt")
    assert bare_filename(safe3)
    assert "/" not in safe3 and "\\" not in safe3

    # Long name (> 200 chars)
    long_raw = "a" * 250 + ".tar.gz"
    safe4 = mod.safe_filename(long_raw)
    assert bare_filename(safe4)
    assert len(safe4) <= 200

    # Uniqueness helper
    seen = set()
    f1 = mod.make_unique_filename("mod.zip", seen)
    f2 = mod.make_unique_filename("mod.zip", seen)
    f3 = mod.make_unique_filename("MOD.ZIP", seen)
    assert f1 == "mod.zip"
    assert f2 == "mod-1.zip"
    assert f3.lower() == "mod-2.zip"
    for name in [f1, f2, f3]:
        assert bare_filename(name)


# ============================================================================
# 7. Additional Robustness & Edge-Case Tests
# ============================================================================

def test_github_games_importer_asset_cap_and_duplicates():
    # 35 assets with duplicate filenames
    assets = [
        {
            "name": f"asset_{i % 3}.zip",
            "browser_download_url": f"https://github.com/org/repo/releases/download/v1/asset_{i}.zip",
            "size": 1000,
        }
        for i in range(35)
    ]
    mock_http = MockHttp({
        "https://api.github.com/repos/org/repo/releases/latest": (200, {"assets": assets}),
        "https://api.github.com/repos/org/repo": (200, {"default_branch": "main"}),
    })
    ctx = PluginContext(config={"include_source": True}, http=mock_http)
    importer = github_games.importer.Importer(ctx)
    sr = SearchResult(source_id="org/repo", title="repo", platform="pc")
    plan = importer.plan(sr)

    # Assets are capped at 32 + 1 source zip = 33 files
    assert len(plan.files) == 33
    # FetchPlan validator ensures distinct destinations
    destinations = [f.filename.casefold() for f in plan.files]
    assert len(destinations) == len(set(destinations))


def test_github_games_importer_refusal_on_invalid_id():
    ctx = PluginContext(config={}, http=MockHttp())
    importer = github_games.importer.Importer(ctx)
    sr = SearchResult(source_id="invalid-no-slash", title="bad", platform="pc")
    with pytest.raises(github_games.importer.ImportRefused):
        importer.plan(sr)


def test_modrinth_importer_duplicates_and_refusal():
    # Duplicate file names in version
    fake_version = {
        "files": [
            {"url": "https://cdn.modrinth.com/data/1.jar", "filename": "mod.jar", "size": 100},
            {"url": "https://cdn.modrinth.com/data/2.jar", "filename": "mod.jar", "size": 200},
            {"url": "https://cdn.modrinth.com/data/3.jar", "filename": "MOD.JAR", "size": 300},
        ]
    }
    mock_http = MockHttp({
        "https://api.modrinth.com/v2/project/test-mod/version": (200, [fake_version]),
        "https://api.modrinth.com/v2/project/empty-mod/version": (200, []),
    })
    ctx = PluginContext(config={}, http=mock_http)
    importer = modrinth.importer.Importer(ctx)

    sr = SearchResult(source_id="test-mod", title="Test Mod", platform="pc")
    plan = importer.plan(sr)
    assert len(plan.files) == 3
    names = [f.filename.casefold() for f in plan.files]
    assert len(names) == len(set(names))

    # Refusal on empty version list
    sr_empty = SearchResult(source_id="empty-mod", title="Empty", platform="pc")
    with pytest.raises(modrinth.importer.ImportRefused):
        importer.plan(sr_empty)


def test_thunderstore_search_comma_separated_and_refusal():
    mock_http = MockHttp({
        "https://thunderstore.io/c/valheim/api/v1/package/": (200, [
            {"name": "ModA", "owner": "DevA", "description": "Mod A description", "versions": [{"download_url": "https://thunderstore.io/dl/a", "file_size": 100}]}
        ]),
        "https://thunderstore.io/c/lethal-company/api/v1/package/": (200, [
            {"name": "ModB", "owner": "DevB", "description": "Mod B description", "versions": [{"download_url": "https://thunderstore.io/dl/b", "file_size": 200}]}
        ]),
    })
    ctx = PluginContext(config={"community": "valheim,lethal-company"}, http=mock_http)
    searcher = thunderstore.search.Search(ctx)
    results = searcher.search("", platform="pc", limit=10)
    assert len(results) == 2
    assert results[0].source_id == "valheim:DevA/ModA"
    assert results[1].source_id == "lethal-company:DevB/ModB"

    # Importer refusal when package is not in the community list
    importer = thunderstore.importer.Importer(ctx)
    sr_missing = SearchResult(source_id="valheim:DevX/MissingMod", title="Missing", platform="pc")
    with pytest.raises(thunderstore.importer.ImportRefused):
        importer.plan(sr_missing)


def test_gamebanana_importer_duplicates_and_refusal():
    fake_profile = {
        "_aFiles": [
            {"_sFile": "addon.zip", "_sDownloadUrl": "https://gamebanana.com/dl/1", "_nFilesize": 500},
            {"_sFile": "addon.zip", "_sDownloadUrl": "https://files.gamebanana.com/dl/2", "_nFilesize": 600},
        ]
    }
    mock_http = MockHttp({
        "https://gamebanana.com/apiv11/Tool/123/ProfilePage": (200, fake_profile),
        "https://gamebanana.com/apiv11/Skin/456/ProfilePage": (200, {"_aFiles": []}),
    })
    ctx = PluginContext(config={}, http=mock_http)
    importer = gamebanana.importer.Importer(ctx)

    sr = SearchResult(source_id="Tool:123", title="My Tool", platform="pc")
    plan = importer.plan(sr)
    assert len(plan.files) == 2
    names = [f.filename.casefold() for f in plan.files]
    assert len(names) == len(set(names))

    # Refusal on empty files
    sr_empty = SearchResult(source_id="Skin:456", title="Empty Skin", platform="pc")
    with pytest.raises(gamebanana.importer.ImportRefused):
        importer.plan(sr_empty)

