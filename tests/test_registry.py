import json
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


# --- I5: git argument injection --------------------------------------------


def _git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_install_rejects_option_like_source_without_executing_it(tmp_path):
    """A source starting with '-' must never reach git as an option.

    `--upload-pack=<cmd>` is run through a shell by git clone, so a source
    copy-pasted from a forum is remote code execution. The marker file is the
    payload: if it exists, the injected command ran.
    """
    marker = tmp_path / "PWNED.txt"
    evil = f"--upload-pack=touch {marker.as_posix()};false"

    with pytest.raises(RegistryError, match="source"):
        Registry(tmp_path / "hub").install(evil)

    assert not marker.exists(), "injected --upload-pack command was executed"


def test_install_rejects_option_like_ref_without_executing_it(tmp_path, source_repo):
    marker = tmp_path / "PWNED-REF.txt"
    evil_ref = f"--upload-pack=touch {marker.as_posix()};false"

    with pytest.raises(RegistryError, match="ref"):
        Registry(tmp_path / "hub").install(str(source_repo), evil_ref)

    assert not marker.exists(), "injected --upload-pack command was executed"


def test_install_rejects_ext_transport(tmp_path):
    """`git clone 'ext::sh -c ...'` is arbitrary code execution by design."""
    marker = tmp_path / "PWNED-EXT.txt"
    evil = f"ext::sh -c touch% {marker.as_posix()}"

    with pytest.raises(RegistryError, match="source"):
        Registry(tmp_path / "hub").install(evil)

    assert not marker.exists()


def test_install_accepts_a_literal_path_that_merely_contains_a_dash(tmp_path, source_repo):
    """The guard must reject options, not ordinary paths with dashes in them."""
    dashed = tmp_path / "my-plugin-repo"
    subprocess.run(
        ["git", "clone", "--quiet", "--", str(source_repo), str(dashed)], check=True
    )
    plugin = Registry(tmp_path / "hub").install(str(dashed))
    assert plugin.slug == "demo"


# --- I8: pinning ------------------------------------------------------------


def test_install_records_the_resolved_commit(tmp_path, source_repo):
    root = tmp_path / "hub"
    Registry(root).install(str(source_repo))
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["demo"]["commit"] == _git_head(source_repo)


def test_install_by_commit_sha_pins_to_that_commit(tmp_path, source_repo):
    """A bare SHA must be usable as a ref, and must win over the branch tip."""
    first = _git_head(source_repo)
    (source_repo / "demo.py").write_text(
        "class Search:\n    MOVED = True\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=source_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "second"],
        cwd=source_repo,
        check=True,
    )
    assert _git_head(source_repo) != first

    root = tmp_path / "hub"
    plugin = Registry(root).install(str(source_repo), first)

    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["demo"]["ref"] == first
    assert state["demo"]["commit"] == first
    assert plugin.commit == first
    assert "MOVED" not in (plugin.path / "demo.py").read_text(encoding="utf-8")


def test_install_by_tag_records_the_commit_the_tag_pointed_at(tmp_path, source_repo):
    """Tags are mutable; the resolved commit is what makes the pin verifiable."""
    tagged = _git_head(source_repo)
    subprocess.run(["git", "tag", "v1.0"], cwd=source_repo, check=True)

    root = tmp_path / "hub"
    Registry(root).install(str(source_repo), "v1.0")

    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state["demo"]["ref"] == "v1.0"
    assert state["demo"]["commit"] == tagged


def test_a_failed_reinstall_leaves_the_previous_install_intact(
    tmp_path, source_repo, monkeypatch
):
    """install() was rmtree-then-copytree, so a failure between them left the
    plugin listed in state.json with no files, and get() then reported a
    confusing "cannot read manifest"."""
    import shutil as _shutil

    reg = Registry(tmp_path / "hub")
    first = reg.install(str(source_repo))
    assert (first.path / "demo.py").exists()

    real_copytree = _shutil.copytree
    monkeypatch.setattr(
        _shutil,
        "copytree",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        reg.install(str(source_repo))
    monkeypatch.setattr(_shutil, "copytree", real_copytree)

    # Still installed, still complete, still readable.
    again = reg.get("demo")
    assert (again.path / "demo.py").exists()
    assert again.manifest.name == "Demo"


def test_a_reinstall_does_not_leave_staging_directories_behind(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    reg.install(str(source_repo))
    reg.install(str(source_repo))
    leftovers = [p.name for p in reg.plugins_dir.iterdir() if p.name != "demo"]
    assert leftovers == [], leftovers
