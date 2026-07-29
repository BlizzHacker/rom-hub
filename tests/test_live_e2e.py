"""End-to-end against the real Archive.org. Deselected unless -m live."""

import subprocess
from pathlib import Path

import pytest

from romm_hub.broker.fetcher import HttpxFetcher
from romm_hub.cli import allow_unsandboxed
from romm_hub.dispatcher import search_all
from romm_hub.registry import Registry

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"


@pytest.fixture
def installed_registry(tmp_path):
    # The plugin dir must be a git repo for install() to clone it.
    if not (PLUGIN_ROOT / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=PLUGIN_ROOT, check=True)
        subprocess.run(["git", "add", "-A"], cwd=PLUGIN_ROOT, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "wip"],
            cwd=PLUGIN_ROOT,
            check=True,
        )
    reg = Registry(tmp_path / "hub")
    reg.install(str(PLUGIN_ROOT))
    return reg


@pytest.mark.live
def test_real_search_returns_results(installed_registry):
    # Same policy switch the CLI reads. On a host without seccomp (Windows,
    # macOS) this test needs ROMM_HUB_ALLOW_UNSANDBOXED=1 in the environment,
    # exactly as a real `romm-hub search` would — see the README.
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
