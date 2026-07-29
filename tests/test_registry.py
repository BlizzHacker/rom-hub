import subprocess
from pathlib import Path

import pytest

from romm_hub.registry import Registry, RegistryError

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

[config]
depth = { type = "int", default = 3 }
"""


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "src-repo"
    repo.mkdir()
    (repo / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (repo / "demo.py").write_text("class Search:\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    return repo


def test_install_from_local_repo(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    plugin = reg.install(str(source_repo))
    assert plugin.slug == "demo"
    assert plugin.manifest.name == "Demo"
    assert (plugin.path / "demo.py").exists()
    assert plugin.enabled is True


def test_installed_lists_plugins(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    reg.install(str(source_repo))
    assert [p.slug for p in reg.installed()] == ["demo"]


def test_config_defaults_come_from_manifest(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    plugin = reg.install(str(source_repo))
    assert plugin.config == {"depth": 3}


def test_set_config_persists_across_instances(tmp_path, source_repo):
    root = tmp_path / "hub"
    Registry(root).install(str(source_repo))
    Registry(root).set_config("demo", {"depth": 9})
    assert Registry(root).get("demo").config == {"depth": 9}


def test_disable_persists(tmp_path, source_repo):
    root = tmp_path / "hub"
    Registry(root).install(str(source_repo))
    Registry(root).set_enabled("demo", False)
    assert Registry(root).get("demo").enabled is False


def test_install_rejects_bad_manifest(tmp_path, source_repo):
    (source_repo / "manifest.toml").write_text(
        MANIFEST.replace('rpp_version = "1"', 'rpp_version = "9"'), encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=source_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bad"],
        cwd=source_repo,
        check=True,
    )
    with pytest.raises(RegistryError, match="rpp_version"):
        Registry(tmp_path / "hub").install(str(source_repo))


def test_get_unknown_slug_raises(tmp_path):
    with pytest.raises(RegistryError, match="not installed"):
        Registry(tmp_path / "hub").get("nope")
