import subprocess
from pathlib import Path

import pytest

from romm_hub.cli import main

MANIFEST = """
[plugin]
slug = "demo"
name = "Demo"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "demo:Search"

[permissions]
network = ["demo.example"]
romm_api = []
"""

PLUGIN = """
from romm_hub_sdk import SearchProvider, SearchResult


class Search(SearchProvider):
    def search(self, query, platform, limit):
        return [SearchResult(source_id="1", title=f"hit: {query}")]
"""


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-plugin"
    repo.mkdir()
    (repo / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (repo / "demo.py").write_text(PLUGIN, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


def test_install_then_list(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["plugin", "install", str(source_repo)]) == 0
    assert main(["plugin", "list"]) == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "enabled" in out


def test_search_end_to_end(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["search", "oregon trail"]) == 0
    out = capsys.readouterr().out
    assert "hit: oregon trail" in out
    assert "1 of 1 source" in out


def test_search_with_no_plugins_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["search", "anything"]) == 0
    assert "no plugins" in capsys.readouterr().out.lower()


def test_disable_removes_plugin_from_search(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    main(["plugin", "disable", "demo"])
    main(["search", "oregon"])
    assert "hit:" not in capsys.readouterr().out
