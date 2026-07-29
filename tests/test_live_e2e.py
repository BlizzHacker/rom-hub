"""End-to-end against the real Archive.org. Deselected unless -m live."""

import shutil
import subprocess
from pathlib import Path

import pytest

from rom_hub.broker.fetcher import HttpxFetcher
from rom_hub.broker.host import PluginProcess
from rom_hub.cli import allow_unsandboxed
from rom_hub.dispatcher import search_all
from rom_hub.netpolicy import check_url
from rom_hub.registry import Registry
from rom_hub.types import SearchResult

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"


@pytest.fixture
def installed_registry(tmp_path):
    # install() clones its source, so the source must be a git repo -- but a
    # test must not `git init`/`add -A`/`commit` inside the developer's own
    # working tree, which would sweep up whatever uncommitted edits happen to
    # be sitting in plugins-dev at the time. Copy first, init the copy.
    source = tmp_path / "archive-org"
    shutil.copytree(
        PLUGIN_ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__")
    )
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "wip"],
        cwd=source,
        check=True,
    )
    reg = Registry(tmp_path / "hub")
    reg.install(str(source))
    return reg


@pytest.mark.live
def test_real_search_returns_results(installed_registry):
    # Same policy switch the CLI reads. On a host without seccomp (Windows,
    # macOS) this test needs ROM_HUB_ALLOW_UNSANDBOXED=1 in the environment,
    # exactly as a real `rom-hub search` would — see the README.
    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            installed_registry.installed(),
            fetcher=fetcher,
            query="oregon trail",
            limit=5,
            allow_unsandboxed=allow_unsandboxed(),
        )
    finally:
        fetcher.close()

    assert outcome.complete, [s.error for s in outcome.statuses if not s.ok]
    assert outcome.results, "expected at least one result from Archive.org"
    assert all(r.plugin == "archive-org" for r in outcome.results)
    assert all(r.title for r in outcome.results)
    assert all(
        r.extra["stream_only"] in {"true", "false"} for r in outcome.results
    )


def _planned(installed_registry, source_id: str):
    """Ask the real plugin, in a real subprocess, over the real network."""
    plugin = installed_registry.get("archive-org")
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
        ) as proc:
            return proc.plan(SearchResult(source_id=source_id, title=source_id))
    finally:
        fetcher.close()


@pytest.mark.live
def test_real_plan_for_a_downloadable_item(installed_registry):
    """The dry run for the manual live import.

    Everything the real import does except opening RomM: the broker starts
    the plugin, the plugin reads live Archive.org metadata over ctx.http,
    and the host validates the returned plan against the allowlist.
    """
    plan = _planned(installed_registry, "rubik_202308")
    assert plan.platform == "dos"
    assert [f.filename for f in plan.files] == ["rubik.zip"]
    assert plan.files[0].size_bytes == 15420
    for entry in plan.files:
        check_url(entry.url, installed_registry.get("archive-org").manifest.network)


@pytest.mark.live
def test_real_stream_only_item_is_refused(installed_registry):
    """Archive.org's own flag, read live -- not a fixture asserting itself."""
    from rom_hub.broker.host import PluginCallError

    with pytest.raises(PluginCallError) as exc:
        _planned(installed_registry, "msdos_Oregon_Trail_The_1990")
    assert "stream-only" in str(exc.value).lower()
