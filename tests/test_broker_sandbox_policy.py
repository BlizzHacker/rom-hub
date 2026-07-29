import textwrap
from pathlib import Path

import pytest

from rom_hub.broker.host import PluginProcess, SandboxRefused
from rom_hub.manifest import parse_manifest

MANIFEST = """
[plugin]
slug = "sbx"
name = "Sbx"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "sbx_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    """
    from rom_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            return [SearchResult(source_id="1", title="ok")]
    """
)


class NullFetcher:
    def get(self, url, params):
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "sbx_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, allow_unsandboxed):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config={},
        fetcher=NullFetcher(),
        timeout=30.0,
        allow_unsandboxed=allow_unsandboxed,
    )


def test_opt_out_allows_an_unsandboxed_plugin_to_run(plugin_dir):
    with _proc(plugin_dir, allow_unsandboxed=True) as proc:
        assert proc.search("q", None, 5)[0].title == "ok"
        assert isinstance(proc.sandboxed, bool)
        assert proc.sandbox_reason


def test_default_is_fail_closed(plugin_dir):
    """Without the opt-out, an unsandboxable platform must refuse to run."""
    proc = _proc(plugin_dir, allow_unsandboxed=False)
    from rom_hub.sandbox import probe

    if probe()[0]:
        # Sandbox works here: start() must succeed and report sandboxed.
        with proc:
            assert proc.sandboxed is True
    else:
        with pytest.raises(SandboxRefused, match="unsandboxed"):
            proc.start()
        proc.close()


def test_refusal_message_names_the_opt_out(plugin_dir):
    from rom_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    proc = _proc(plugin_dir, allow_unsandboxed=False)
    with pytest.raises(SandboxRefused, match="ROM_HUB_ALLOW_UNSANDBOXED"):
        proc.start()
    proc.close()
