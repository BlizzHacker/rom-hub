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
    # The host fails closed when a plugin cannot be confined. On a host with
    # no seccomp (Windows, macOS) the opt-out is what lets this run at all;
    # on Linux it is a no-op because the filter loads and the plugin is
    # confined either way. Setting it unconditionally keeps one code path.
    monkeypatch.setenv("ROMM_HUB_ALLOW_UNSANDBOXED", "1")
    main(["plugin", "install", str(source_repo)])
    assert main(["search", "oregon trail"]) == 0
    out = capsys.readouterr().out
    assert "hit: oregon trail" in out
    assert "1 of 1 source" in out


def test_allow_unsandboxed_reads_the_environment(monkeypatch):
    from romm_hub.cli import allow_unsandboxed

    monkeypatch.delenv("ROMM_HUB_ALLOW_UNSANDBOXED", raising=False)
    assert allow_unsandboxed() is False
    monkeypatch.setenv("ROMM_HUB_ALLOW_UNSANDBOXED", "1")
    assert allow_unsandboxed() is True


def test_search_reports_sandbox_refusal_clearly(
    tmp_path, source_repo, monkeypatch, capsys
):
    """A refusal must explain itself, not surface as a bare traceback."""
    from romm_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROMM_HUB_ALLOW_UNSANDBOXED", raising=False)
    main(["plugin", "install", str(source_repo)])
    main(["search", "anything"])
    combined = capsys.readouterr()
    assert "ROMM_HUB_ALLOW_UNSANDBOXED" in (combined.out + combined.err)


def test_install_note_states_confinement_accurately(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The install note is the only security claim most operators will read."""
    from romm_hub.sandbox import probe

    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    # The note is hard-wrapped for the terminal; assert on the prose, not on
    # where the line breaks happen to fall.
    out = " ".join(capsys.readouterr().out.split())
    # The retracted Phase 1 claim must not come back.
    assert "does not sandbox" not in out
    # What is genuinely not confined has to be stated either way.
    assert "read any file this process can" in out
    if probe()[0]:
        assert "seccomp" in out
    else:
        assert "ROMM_HUB_ALLOW_UNSANDBOXED" in out


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


def test_a_bad_manifest_on_install_is_an_error_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    """main() caught only RegistryError, so ManifestError and an OSError from
    Registry.__init__'s mkdir produced a bare traceback."""
    repo = tmp_path / "broken"
    repo.mkdir()
    (repo / "manifest.toml").write_text('[plugin]\nslug = "x"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "hub"))
    assert main(["plugin", "install", str(repo)]) == 1
    assert "error:" in capsys.readouterr().err


def test_an_unusable_home_is_an_error_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setenv("ROMM_HUB_HOME", str(blocker / "hub"))
    assert main(["plugin", "list"]) == 1
    assert "error:" in capsys.readouterr().err
