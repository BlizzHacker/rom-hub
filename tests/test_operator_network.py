"""Operator grants are per plugin and enforced at each host fetch."""

import textwrap

import httpx
import pytest

from rom_hub.broker.fetcher import HttpxFetcher
from rom_hub.broker.host import PluginProcess
from rom_hub.importer import DownloadError, HttpDownloader
from rom_hub.manifest import ManifestError, parse_manifest
from rom_hub.netpolicy import network_policy, url_allowed

GRANT = "ROM_HUB_NETWORK_TEST_SHOP"
SHOP = "http://127.0.0.1:8080"
MANIFEST = """
[plugin]
slug = "test-shop"
name = "Shop fixture"
version = "1"
rpp_version = "1"

[capabilities]
search = "shop:Search"
importer = "shop:Import"

[permissions]
network = ["static.example"]
"""


@pytest.fixture
def manifest():
    """A plugin that accepts operator grants."""
    return parse_manifest(MANIFEST + "network_from_env = true\n")


@pytest.mark.parametrize(("url", "allowed"), [
    ("https://static.example/static.json", True),
    ("https://shop.example/list", True),
    ("https://demo.retail.example/list", True),
    ("https://retail.example/list", False),
    ("http://shop.example/list", False),
    ("http://192.168.1.12:8080/list", True),
    ("http://192.168.1.12:8081/list", False),
    ("http://192.168.1.12/list", False),
    ("http://[fd12::35]:8080/list", True),
    ("http://127.0.0.1:8080/list", True),
    ("http://public.example:8080/list", False),
    ("http://169.254.169.254:80/list", False),
    ("http://10.0.0.1/list", False),
    ("http://user@127.0.0.1:8080/list", False),
])
def test_grant_matches_only_the_authorized_scheme_host_and_port(manifest, url, allowed):
    """Host patterns keep HTTPS semantics; a local HTTP grant pins the port."""
    grants = (
        "shop.example,*.retail.example,http://192.168.1.12:8080,http://[fd12::35]:8080,"
        "http://127.0.0.1:8080,http://public.example:8080,http://169.254.169.254:80,"
        "http://10.0.0.1,10.0.0.0/8"
    )
    policy = network_policy(manifest, {GRANT: grants})
    assert url_allowed(url, policy) is allowed


def test_manifest_must_opt_in_to_its_own_environment_variable(manifest):
    """The operator's environment cannot grant access across plugin slugs."""
    url = SHOP + "/list"
    assert not url_allowed(url, network_policy(parse_manifest(MANIFEST), {GRANT: SHOP}))
    assert not url_allowed(url, network_policy(manifest, {"ROM_HUB_NETWORK_OTHER": SHOP}))
    with pytest.raises(ManifestError, match="network_from_env must be a boolean"):
        parse_manifest(MANIFEST + 'network_from_env = "true"\n')


def test_plugin_can_search_and_download_from_an_operator_shop(tmp_path, monkeypatch, manifest):
    """Exercise the broker, plugin subprocess, and download through the same grant."""
    monkeypatch.setenv(GRANT, SHOP)
    (tmp_path / "shop.py").write_text(textwrap.dedent("""
        import os
        from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchProvider, SearchResult

        class Search(SearchProvider):
            def search(self, query, platform, limit):
                assert "ROM_HUB_NETWORK_TEST_SHOP" not in os.environ
                result = self.ctx.http.get(self.ctx.config["url"] + "/list")
                return [SearchResult(source_id="fixture", title=result.text)]

        class Import(ImportProvider):
            def plan(self, result):
                return FetchPlan(platform="nes", files=[
                    FetchFile(url=self.ctx.config["url"] + "/file", filename="fixture.nes")
                ])
    """), encoding="utf-8")

    def serve(request):
        """Provide a small catalogue and a fake payload without network access."""
        return httpx.Response(200, text={"list": "Game", "file": "fixture"}[request.url.path[1:]])

    transport = httpx.MockTransport(serve)
    fetcher = HttpxFetcher(transport=transport)
    try:
        with PluginProcess(tmp_path, manifest, {"url": SHOP}, fetcher,
                           allow_unsandboxed=True) as process:
            result = process.search("Game", "nes", 1)[0]
            file = process.plan(result).files[0]
            path = tmp_path / file.filename
            with HttpDownloader(process.network_policy, transport=transport) as downloader:
                downloader.download(file.url, path)
    finally:
        fetcher.close()
    assert result.title == "Game"
    assert path.read_bytes() == b"fixture"


def test_redirects_are_rechecked_and_credentials_stay_on_their_origin(manifest, tmp_path):
    """A second approved origin may be fetched, without the first's credentials."""
    policy = network_policy(manifest, {GRANT: SHOP + ",http://127.0.0.1:8081"})
    requests = []

    def serve(request):
        """Redirect to a second grant, then to a port the operator did not grant."""
        requests.append((request.url.port, request.headers.get("authorization")))
        if request.url.port == 8080:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8081/file"})
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:8082/file"})

    with HttpDownloader(policy, transport=httpx.MockTransport(serve)) as downloader:
        with pytest.raises(DownloadError, match="outside.*allowlist"):
            downloader.download(SHOP + "/file", tmp_path / "file",
                                headers={"Authorization": "Bearer fixture"})
    assert requests == [(8080, "Bearer fixture"), (8081, None)]
