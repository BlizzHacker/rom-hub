"""The FetchPlan gate.

`ctx.http` is not the only way a plugin can make the host reach out any more.
A FetchPlan hands the host a list of URLs and asks it to fetch them, so it is a
second channel to the same capability and it gets the same allowlist gate --
otherwise `network = [...]` in a manifest means nothing for imports.

A hostile plugin is not obliged to use the SDK's own validation, so several of
these tests return a duck-typed object from `plan()` whose `model_dump()`
emits whatever it likes. Everything the host trusts must be re-established on
the host side of the pipe.
"""

import textwrap
from pathlib import Path

import pytest

from romm_hub.broker.host import PluginCallError, PluginProcess
from romm_hub.manifest import parse_manifest
from romm_hub.types import SearchResult

MANIFEST = """
[plugin]
slug = "imp"
name = "Imp"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "imp_plugin:Search"
importer = "imp_plugin:Importer"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from romm_hub_sdk import (
        FetchFile, FetchPlan, ImportProvider, SearchProvider, SearchResult,
    )


    class Raw:
        """A plugin that skips the SDK's types entirely.

        Nothing stops a real hostile plugin doing exactly this: the runner
        only calls model_dump() on whatever plan() hands back.
        """

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            return [SearchResult(source_id="1", title="t")]


    class Importer(ImportProvider):
        def plan(self, result):
            mode = self.ctx.config.get("mode", "good")

            if mode == "mixed":
                # The first file is legitimate. If the host only checks
                # files[0], or stops at the first pass, the second one rides
                # in behind it.
                return FetchPlan(
                    files=[
                        FetchFile(
                            url="https://allowed.example/ok.zip",
                            filename="ok.zip",
                        ),
                        FetchFile(
                            url="https://evil.example/steal.zip",
                            filename="steal.zip",
                        ),
                    ],
                    platform="dos",
                )

            if mode == "raw_evil":
                # No SDK validation anywhere in this path.
                return Raw({
                    "files": [
                        {"url": "https://evil.example/g.zip",
                         "filename": "g.zip"},
                    ],
                    "platform": "dos",
                    "collection": None,
                })

            if mode == "raw_traversal":
                return Raw({
                    "files": [
                        {"url": "https://allowed.example/g.zip",
                         "filename": "../../escape.zip"},
                    ],
                    "platform": "dos",
                })

            if mode == "raw_plain_http":
                # An allowlisted host, but cleartext. netpolicy only permits
                # https, and the FetchPlan path must honour that too.
                return Raw({
                    "files": [
                        {"url": "http://allowed.example/g.zip",
                         "filename": "g.zip"},
                    ],
                    "platform": "dos",
                })

            if mode == "raw_userinfo":
                # https://allowed.example@evil.example/ -- the host is
                # evil.example; the allowlisted name is just userinfo.
                return Raw({
                    "files": [
                        {"url": "https://allowed.example@evil.example/g.zip",
                         "filename": "g.zip"},
                    ],
                    "platform": "dos",
                })

            if mode == "raw_not_a_mapping":
                return Raw(["not", "a", "plan"])

            host = "evil.example" if mode == "exfiltrate" else "allowed.example"
            return FetchPlan(
                files=[FetchFile(url=f"https://{host}/g.zip", filename="g.zip")],
                platform="dos",
            )
    '''
)


class NullFetcher:
    """The FetchPlan path must never touch this. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "imp_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, config=None, fetcher=None):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=fetcher or NullFetcher(),
        timeout=30.0,
        # Windows cannot seccomp; the host is fail-closed by default.
        allow_unsandboxed=True,
    )


def test_plan_returns_a_validated_fetchplan(plugin_dir):
    with _proc(plugin_dir) as proc:
        plan = proc.plan(SearchResult(source_id="1", title="t"))
    assert plan.platform == "dos"
    assert plan.files[0].url == "https://allowed.example/g.zip"


def test_plan_with_an_undeclared_host_is_rejected(plugin_dir):
    """A FetchPlan is a second way to make the host fetch. Gate it like ctx.http."""
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_a_mixed_plan_is_rejected_as_a_whole(plugin_dir):
    """One good file must not smuggle a bad one in behind it.

    The gate is per-plan, not per-file: a plan containing any undeclared host
    is refused entirely, so no partial import can begin on the legitimate half.
    """
    with _proc(plugin_dir, {"mode": "mixed"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_a_plan_built_without_the_sdk_is_still_gated(plugin_dir):
    """The host cannot assume the plugin used FetchPlan to build its answer."""
    with _proc(plugin_dir, {"mode": "raw_evil"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_a_traversal_filename_is_rejected_host_side(plugin_dir):
    """Filename validation is the host's, not a courtesy the plugin extends."""
    with _proc(plugin_dir, {"mode": "raw_traversal"}) as proc:
        with pytest.raises(PluginCallError, match="invalid FetchPlan"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_a_cleartext_url_is_rejected_even_for_an_allowed_host(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_plain_http"}) as proc:
        with pytest.raises(PluginCallError, match="allowed.example"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_userinfo_cannot_disguise_the_real_host(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_userinfo"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_a_non_mapping_plan_is_a_plugin_error_not_a_crash(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_not_a_mapping"}) as proc:
        with pytest.raises(PluginCallError, match="invalid FetchPlan"):
            proc.plan(SearchResult(source_id="1", title="t"))


def test_planning_never_fetches_anything_itself(plugin_dir):
    """plan() describes work; it must not perform any of it."""
    fetcher = NullFetcher()
    with _proc(plugin_dir, fetcher=fetcher) as proc:
        proc.plan(SearchResult(source_id="1", title="t"))
    assert fetcher.calls == []
