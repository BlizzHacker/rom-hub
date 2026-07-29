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


# --- import / jobs -------------------------------------------------------
#
# None of these may reach a live RomM. Each stops at a check that fires
# before any RomM connection is attempted, which is also the order an
# operator's mistakes actually arrive in.


def test_import_from_an_unknown_plugin_exits_nonzero_with_a_clear_message(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["import", "no-such-plugin", "some_item"]) != 0
    err = capsys.readouterr().err
    assert "no-such-plugin" in err
    assert "not installed" in err


def test_import_from_a_plugin_without_the_capability_says_so(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The demo plugin declares `search` only. Naming the missing capability
    is the difference between a fixable message and a puzzle."""
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["import", "demo", "some_item"]) != 0
    err = capsys.readouterr().err
    assert "importer" in err


def test_import_from_a_disabled_plugin_is_refused(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    main(["plugin", "disable", "demo"])
    assert main(["import", "demo", "some_item"]) != 0
    assert "disabled" in capsys.readouterr().err


def test_import_without_romm_settings_names_the_variables(
    tmp_path, monkeypatch, capsys
):
    """An unconfigured Hub must not fail somewhere inside httpx."""
    from romm_hub.cli import romm_settings

    monkeypatch.delenv("ROMM_URL", raising=False)
    monkeypatch.delenv("ROMM_USER", raising=False)
    monkeypatch.delenv("ROMM_PASSWORD", raising=False)
    with pytest.raises(RuntimeError) as exc:
        romm_settings()
    message = str(exc.value)
    assert "ROMM_URL" in message
    assert "ROMM_USER" in message
    assert "ROMM_PASSWORD" in message


def test_romm_settings_reads_the_environment(monkeypatch):
    from romm_hub.cli import romm_settings

    monkeypatch.setenv("ROMM_URL", "https://romm.example/")
    monkeypatch.setenv("ROMM_USER", "admin")
    monkeypatch.setenv("ROMM_PASSWORD", "hunter2")
    assert romm_settings() == ("https://romm.example/", "admin", "hunter2")


def test_jobs_with_an_empty_queue_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["jobs"]) == 0
    assert "no import jobs" in capsys.readouterr().out.lower()


def test_jobs_lists_what_the_queue_holds(tmp_path, monkeypatch, capsys):
    from romm_hub.cli import jobs_db_path
    from romm_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROMM_HUB_HOME", str(home))
    with JobQueue(jobs_db_path(home)) as queue:
        done = queue.enqueue("archive-org", "rubik_202308", "Rubik", "dos")
        queue.set_state(done.id, JobState.DONE)
        queue.enqueue("archive-org", "other_item", "Other", "dos")

    assert main(["jobs"]) == 0
    out = capsys.readouterr().out
    assert "rubik_202308" in out
    assert "other_item" in out
    assert "DONE" in out


def test_jobs_can_be_filtered_by_state(tmp_path, monkeypatch, capsys):
    from romm_hub.cli import jobs_db_path
    from romm_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROMM_HUB_HOME", str(home))
    with JobQueue(jobs_db_path(home)) as queue:
        done = queue.enqueue("archive-org", "rubik_202308", "Rubik", "dos")
        queue.set_state(done.id, JobState.DONE)
        queue.enqueue("archive-org", "other_item", "Other", "dos")

    assert main(["jobs", "--state", "DONE"]) == 0
    out = capsys.readouterr().out
    assert "rubik_202308" in out
    assert "other_item" not in out


def test_an_unknown_job_state_is_an_error_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["jobs", "--state", "NONSENSE"]) != 0
    err = capsys.readouterr().err
    assert "NONSENSE" in err
    # The message has to say what the legal values are, or it is a riddle.
    assert "DONE" in err


def test_import_reports_a_sandbox_refusal_clearly(
    tmp_path, source_repo, monkeypatch, capsys
):
    """`search` isolates each plugin in the dispatcher; `import` talks to one
    PluginProcess directly, so SandboxRefused reaches main() unwrapped."""
    from romm_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROMM_HUB_ALLOW_UNSANDBOXED", raising=False)
    # Point at a RomM that is not there: the refusal must come first, so the
    # connection is never attempted.
    monkeypatch.setenv("ROMM_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ROMM_USER", "x")
    monkeypatch.setenv("ROMM_PASSWORD", "y")
    main(["plugin", "install", str(source_repo)])
    # The demo plugin has no importer, so borrow a manifest that claims one.
    home = tmp_path / "home" / "plugins" / "demo"
    (home / "manifest.toml").write_text(
        MANIFEST.replace(
            '[capabilities]\nsearch = "demo:Search"',
            '[capabilities]\nsearch = "demo:Search"\nimporter = "demo:Search"',
        ),
        encoding="utf-8",
    )
    assert main(["import", "demo", "anything"]) != 0
    combined = capsys.readouterr()
    assert "ROMM_HUB_ALLOW_UNSANDBOXED" in (combined.out + combined.err)


# --- enrich --------------------------------------------------------------
#
# Same rule as `import`: none of these may reach a live RomM. Each stops at
# a check that fires before any RomM connection is attempted.


def test_enrich_from_an_unknown_plugin_exits_nonzero_with_a_clear_message(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["enrich", "no-such-plugin", "1"]) != 0
    err = capsys.readouterr().err
    assert "no-such-plugin" in err
    assert "not installed" in err


def test_enrich_from_a_plugin_without_the_capability_says_so(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["enrich", "demo", "1"]) != 0
    assert "metadata" in capsys.readouterr().err


def test_enrich_without_romm_settings_names_the_variables(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The capability check passes, so the next thing that must stop it is
    the unconfigured RomM -- not a connection attempt."""
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    for name in ("ROMM_URL", "ROMM_USER", "ROMM_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    main(["plugin", "install", str(source_repo)])
    installed = tmp_path / "home" / "plugins" / "demo" / "manifest.toml"
    installed.write_text(
        MANIFEST.replace(
            '[capabilities]\nsearch = "demo:Search"',
            '[capabilities]\nsearch = "demo:Search"\nmetadata = "demo:Search"',
        ),
        encoding="utf-8",
    )
    assert main(["enrich", "demo", "1"]) != 0
    assert "ROMM_URL" in capsys.readouterr().err


def test_enrich_from_a_disabled_plugin_is_refused(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    main(["plugin", "disable", "demo"])
    assert main(["enrich", "demo", "1"]) != 0
    assert "disabled" in capsys.readouterr().err


def test_a_failed_job_shows_its_error(tmp_path, monkeypatch, capsys):
    from romm_hub.cli import jobs_db_path
    from romm_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROMM_HUB_HOME", str(home))
    with JobQueue(jobs_db_path(home)) as queue:
        job = queue.enqueue("archive-org", "x", "X", "dos")
        queue.set_state(job.id, JobState.FAILED, error="the item is stream-only")

    main(["jobs"])
    assert "stream-only" in capsys.readouterr().out
