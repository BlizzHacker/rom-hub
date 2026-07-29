import io
import subprocess
import sys
from pathlib import Path

import pytest

from rom_hub.cli import configure_output_encoding, main

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
from rom_hub_sdk import SearchProvider, SearchResult


class Search(SearchProvider):
    def search(self, query, platform, limit):
        return [SearchResult(source_id="1", title=f"hit: {query}")]
"""


# Three titles a cp1252 console cannot encode, plus one it can. Taken from
# the shape of real Archive.org listings: CJK, Cyrillic, and an accented
# Latin character that people assume is "basically ASCII" and is not.
UNICODE_PLUGIN = """
from rom_hub_sdk import SearchProvider, SearchResult

TITLES = [
    "Plain ASCII Title",
    "\\u30bd\\u30cb\\u30c3\\u30af\\u30ea\\u30f3\\u30ab\\u30fc",
    "\\u041f\\u0440\\u0438\\u043a\\u043b\\u044e\\u0447\\u0435\\u043d\\u0438\\u044f",
    "Pok\\u00e9mon Caf\\u00e9 Mix",
    "Last ASCII Title",
]


class Search(SearchProvider):
    def search(self, query, platform, limit):
        return [
            SearchResult(source_id=str(i), title=t)
            for i, t in enumerate(TITLES)
        ]
"""


def _make_repo(tmp_path: Path, name: str, plugin_source: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    (repo / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (repo / "demo.py").write_text(plugin_source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path, "demo-plugin", PLUGIN)


@pytest.fixture
def unicode_source_repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path, "unicode-plugin", UNICODE_PLUGIN)


def test_install_then_list(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    assert main(["plugin", "install", str(source_repo)]) == 0
    assert main(["plugin", "list"]) == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "enabled" in out


def test_search_end_to_end(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    # The host fails closed when a plugin cannot be confined. On a host with
    # no seccomp (Windows, macOS) the opt-out is what lets this run at all;
    # on Linux it is a no-op because the filter loads and the plugin is
    # confined either way. Setting it unconditionally keeps one code path.
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    main(["plugin", "install", str(source_repo)])
    assert main(["search", "oregon trail"]) == 0
    out = capsys.readouterr().out
    assert "hit: oregon trail" in out
    assert "1 of 1 source" in out


def test_allow_unsandboxed_reads_the_environment(monkeypatch):
    from rom_hub.cli import allow_unsandboxed

    monkeypatch.delenv("ROM_HUB_ALLOW_UNSANDBOXED", raising=False)
    assert allow_unsandboxed() is False
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    assert allow_unsandboxed() is True


def test_search_reports_sandbox_refusal_clearly(
    tmp_path, source_repo, monkeypatch, capsys
):
    """A refusal must explain itself, not surface as a bare traceback."""
    from rom_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROM_HUB_ALLOW_UNSANDBOXED", raising=False)
    main(["plugin", "install", str(source_repo)])
    main(["search", "anything"])
    combined = capsys.readouterr()
    assert "ROM_HUB_ALLOW_UNSANDBOXED" in (combined.out + combined.err)


def test_install_note_states_confinement_accurately(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The install note is the only security claim most operators will read."""
    from rom_hub.sandbox import probe

    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
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
        assert "ROM_HUB_ALLOW_UNSANDBOXED" in out


def test_search_with_no_plugins_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    assert main(["search", "anything"]) == 0
    assert "no plugins" in capsys.readouterr().out.lower()


def test_disable_removes_plugin_from_search(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
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
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "hub"))
    assert main(["plugin", "install", str(repo)]) == 1
    assert "error:" in capsys.readouterr().err


def test_an_unusable_home_is_an_error_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setenv("ROM_HUB_HOME", str(blocker / "hub"))
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
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    assert main(["import", "no-such-plugin", "some_item"]) != 0
    err = capsys.readouterr().err
    assert "no-such-plugin" in err
    assert "not installed" in err


def test_import_from_a_plugin_without_the_capability_says_so(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The demo plugin declares `search` only. Naming the missing capability
    is the difference between a fixable message and a puzzle."""
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["import", "demo", "some_item"]) != 0
    err = capsys.readouterr().err
    assert "importer" in err


def test_import_from_a_disabled_plugin_is_refused(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    main(["plugin", "disable", "demo"])
    assert main(["import", "demo", "some_item"]) != 0
    assert "disabled" in capsys.readouterr().err


def _clear_romm_env(monkeypatch):
    for name in (
        "ROMM_URL",
        "ROMM_USER",
        "ROMM_PASSWORD",
        "ROM_HUB_BACKEND_URL",
        "ROM_HUB_BACKEND_USER",
        "ROM_HUB_BACKEND_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_import_without_romm_settings_names_the_variables(
    tmp_path, monkeypatch, capsys
):
    """An unconfigured Hub must not fail somewhere inside httpx.

    The settings moved into the RomM backend when the seam was extracted;
    they are that backend's, not the CLI's. The message is unchanged, and
    still names every missing variable at once.
    """
    from rom_hub.backends.base import BackendNotConfigured
    from rom_hub.backends.romm import settings_from_env

    _clear_romm_env(monkeypatch)
    with pytest.raises(BackendNotConfigured) as exc:
        settings_from_env()
    message = str(exc.value)
    assert "ROMM_URL" in message
    assert "ROMM_USER" in message
    assert "ROMM_PASSWORD" in message


def test_romm_settings_reads_the_environment(monkeypatch):
    from rom_hub.backends.romm import settings_from_env

    _clear_romm_env(monkeypatch)
    monkeypatch.setenv("ROMM_URL", "https://romm.example/")
    monkeypatch.setenv("ROMM_USER", "admin")
    monkeypatch.setenv("ROMM_PASSWORD", "hunter2")
    assert settings_from_env() == ("https://romm.example/", "admin", "hunter2")


def test_the_backend_neutral_setting_names_also_work(monkeypatch):
    """A deployment that switches backends should not have to learn a
    different product's vocabulary for "the URL"."""
    from rom_hub.backends.romm import settings_from_env

    _clear_romm_env(monkeypatch)
    monkeypatch.setenv("ROM_HUB_BACKEND_URL", "https://lib.example/")
    monkeypatch.setenv("ROM_HUB_BACKEND_USER", "admin")
    monkeypatch.setenv("ROM_HUB_BACKEND_PASSWORD", "hunter2")
    assert settings_from_env() == ("https://lib.example/", "admin", "hunter2")


def test_jobs_with_an_empty_queue_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    assert main(["jobs"]) == 0
    assert "no import jobs" in capsys.readouterr().out.lower()


def test_jobs_lists_what_the_queue_holds(tmp_path, monkeypatch, capsys):
    from rom_hub.cli import jobs_db_path
    from rom_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(home))
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
    from rom_hub.cli import jobs_db_path
    from rom_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(home))
    with JobQueue(jobs_db_path(home)) as queue:
        done = queue.enqueue("archive-org", "rubik_202308", "Rubik", "dos")
        queue.set_state(done.id, JobState.DONE)
        queue.enqueue("archive-org", "other_item", "Other", "dos")

    assert main(["jobs", "--state", "DONE"]) == 0
    out = capsys.readouterr().out
    assert "rubik_202308" in out
    assert "other_item" not in out


def test_jobs_shows_a_skipped_step_without_calling_it_an_error(
    tmp_path, monkeypatch, capsys
):
    """A DONE import that skipped an optional step the backend cannot do
    must say so here -- this listing is where an operator looks when the
    collection they expected is empty -- but it must not be marked the way
    a failure is, because the import worked."""
    from rom_hub.cli import jobs_db_path
    from rom_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(home))
    with JobQueue(jobs_db_path(home)) as queue:
        job = queue.enqueue("archive-org", "rubik_202308", "Rubik", "dos")
        queue.set_notes(
            job.id,
            "adding it to the collection 'Archive.org' was skipped: the "
            "'gaseous' backend does not support collections",
        )
        queue.set_state(job.id, JobState.DONE)

    assert main(["jobs"]) == 0
    out = capsys.readouterr().out
    assert "Archive.org" in out
    assert "does not support collections" in out
    # Marked as a note, not with the "!" every failure carries.
    note_line = [line for line in out.splitlines() if "Archive.org" in line][0]
    assert note_line.strip().startswith("~")
    assert "!" not in note_line


def test_an_unknown_job_state_is_an_error_message_not_a_traceback(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
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
    from rom_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROM_HUB_ALLOW_UNSANDBOXED", raising=False)
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
    assert "ROM_HUB_ALLOW_UNSANDBOXED" in (combined.out + combined.err)


# --- enrich --------------------------------------------------------------
#
# Same rule as `import`: none of these may reach a live RomM. Each stops at
# a check that fires before any RomM connection is attempted.


def test_enrich_from_an_unknown_plugin_exits_nonzero_with_a_clear_message(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    assert main(["enrich", "no-such-plugin", "1"]) != 0
    err = capsys.readouterr().err
    assert "no-such-plugin" in err
    assert "not installed" in err


def test_enrich_from_a_plugin_without_the_capability_says_so(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["enrich", "demo", "1"]) != 0
    assert "metadata" in capsys.readouterr().err


def test_enrich_without_romm_settings_names_the_variables(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The capability check passes, so the next thing that must stop it is
    the unconfigured RomM -- not a connection attempt."""
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
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
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    main(["plugin", "disable", "demo"])
    assert main(["enrich", "demo", "1"]) != 0
    assert "disabled" in capsys.readouterr().err


# --- stream ---------------------------------------------------------------


def test_stream_from_a_plugin_without_the_capability_says_so(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["stream", "demo", "some_item"]) != 0
    assert "stream" in capsys.readouterr().err


def test_stream_prints_the_resolved_target(tmp_path, source_repo, monkeypatch, capsys):
    """The whole command: resolve, validate, print. The Hub builds no
    streaming transport of its own -- romm-stream is a separate service."""
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    main(["plugin", "install", str(source_repo)])

    installed = tmp_path / "home" / "plugins" / "demo"
    (installed / "demo_stream.py").write_text(
        "from rom_hub_sdk import StreamProvider, StreamTarget\n"
        "\n"
        "\n"
        "class Stream(StreamProvider):\n"
        "    def resolve(self, result):\n"
        "        return StreamTarget(\n"
        '            kind="url",\n'
        '            target="https://demo.example/play/" + result.source_id,\n'
        '            title="Demo Game",\n'
        "        )\n",
        encoding="utf-8",
    )
    (installed / "manifest.toml").write_text(
        MANIFEST.replace(
            '[capabilities]\nsearch = "demo:Search"',
            '[capabilities]\nsearch = "demo:Search"\n'
            'stream = "demo_stream:Stream"',
        ),
        encoding="utf-8",
    )

    assert main(["stream", "demo", "rubik_202308"]) == 0
    out = capsys.readouterr().out
    assert "https://demo.example/play/rubik_202308" in out
    assert "url" in out
    assert "Demo Game" in out


# --- cores ----------------------------------------------------------------

CORES_PLUGIN = '''
from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan


class Cores(CoreProvider):
    def list(self):
        return [CoreArtifact(core_id="dosbox", name="DOSBox", system="dos")]

    def plan(self, core):
        return FetchPlan(
            files=[
                FetchFile(
                    url="https://demo.example/" + core.core_id + ".wasm",
                    filename=core.core_id + ".wasm",
                )
            ],
            platform=core.system or "unknown",
        )
'''


def _install_cores_plugin(tmp_path, source_repo):
    """Install the demo plugin and give it a `cores` capability."""
    main(["plugin", "install", str(source_repo)])
    installed = tmp_path / "home" / "plugins" / "demo"
    (installed / "demo_cores.py").write_text(CORES_PLUGIN, encoding="utf-8")
    (installed / "manifest.toml").write_text(
        MANIFEST.replace(
            '[capabilities]\nsearch = "demo:Search"',
            '[capabilities]\nsearch = "demo:Search"\ncores = "demo_cores:Cores"',
        ),
        encoding="utf-8",
    )
    return installed


def test_cores_from_a_plugin_without_the_capability_says_so(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["cores", "list", "demo"]) != 0
    assert "cores" in capsys.readouterr().err


def test_cores_list_prints_the_catalogue(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    _install_cores_plugin(tmp_path, source_repo)

    assert main(["cores", "list", "demo"]) == 0
    out = capsys.readouterr().out
    assert "dosbox" in out
    assert "DOSBox" in out
    assert "1 core(s)" in out


def test_cores_install_writes_into_the_configured_directory(
    tmp_path, source_repo, monkeypatch, capsys
):
    """The install path end to end, with the only socket replaced.

    `--` the downloader is the one thing stubbed, because a test may not
    reach the network; everything else is the real command.
    """
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    monkeypatch.setenv("ROM_HUB_CORES_DIR", str(tmp_path / "cores"))
    _install_cores_plugin(tmp_path, source_repo)

    import rom_hub.cores as cores_module

    real_install = cores_module.install_core

    class FakeDownloader:
        def download(self, url, dest, expected_size=None):
            from pathlib import Path

            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"wasm")
            return dest

        def close(self):
            pass

    def install(plugin, core, *, cores_dir, downloader=None):
        return real_install(
            plugin, core, cores_dir=cores_dir, downloader=FakeDownloader()
        )

    monkeypatch.setattr("rom_hub.cli.install_core", install)

    assert main(["cores", "install", "demo", "dosbox"]) == 0
    assert (tmp_path / "cores" / "demo" / "dosbox.wasm").read_bytes() == b"wasm"
    assert "dosbox" in capsys.readouterr().out


def test_cores_install_of_an_unknown_core_is_an_error_not_a_traceback(
    tmp_path, source_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    monkeypatch.setenv("ROM_HUB_CORES_DIR", str(tmp_path / "cores"))
    _install_cores_plugin(tmp_path, source_repo)

    assert main(["cores", "install", "demo", "nonesuch"]) != 0
    err = capsys.readouterr().err
    assert "nonesuch" in err
    # The message has to say what IS on offer, or it is a riddle.
    assert "dosbox" in err


def test_a_failed_job_shows_its_error(tmp_path, monkeypatch, capsys):
    from rom_hub.cli import jobs_db_path
    from rom_hub.jobs import JobQueue, JobState

    home = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(home))
    with JobQueue(jobs_db_path(home)) as queue:
        job = queue.enqueue("archive-org", "x", "X", "dos")
        queue.set_state(job.id, JobState.FAILED, error="the item is stream-only")

    main(["jobs"])
    assert "stream-only" in capsys.readouterr().out


# ------------------------------------------------- output encoding boundary
#
# A plugin chooses its own name, result titles, refusal messages and error
# strings. On a cp1252 Windows console, printing one containing anything
# outside that codepage raised UnicodeEncodeError from inside `print` and
# killed the command -- discarding results that had already been fetched.
# Fixed once at the stream, not at ~60 print sites.


def cp1252_stdout(monkeypatch):
    """Replace stdout with a real cp1252 stream, and hand back its bytes.

    Forced rather than probed so this is meaningful on Linux CI, where the
    default stdout is UTF-8 and could never reproduce the bug.
    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    return stream, raw


def test_a_title_outside_cp1252_does_not_kill_the_command(
    tmp_path, unicode_source_repo, monkeypatch
):
    """The reported crash: CJK/accented titles on a cp1252 console.

    Asserts both halves -- it does not raise, AND the results that bracket
    the unprintable ones still arrive. The old failure lost every line from
    the first bad title onwards.
    """
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    main(["plugin", "install", str(unicode_source_repo)])

    stream, raw = cp1252_stdout(monkeypatch)
    assert main(["search", "sonic", "--limit", "6"]) == 0
    stream.flush()
    out = raw.getvalue().decode("cp1252")

    # Nothing was dropped: the ASCII titles on either side of the
    # unprintable ones are both here, and so is the summary line after them.
    assert "Plain ASCII Title" in out
    assert "Last ASCII Title" in out
    assert "1 of 1 source" in out
    assert "5 results" in out

    # The unencodable titles degraded rather than vanishing or erroring.
    # Derived from the codec rather than hard-coded, so the test states
    # the property -- "this is what backslashreplace produces" -- instead
    # of a literal that needs three levels of escaping to write down.
    def degraded(text: str) -> str:
        return text.encode("cp1252", "backslashreplace").decode("cp1252")

    assert degraded("ソ") in out    # Japanese title
    assert degraded("П") in out    # Cyrillic title
    assert degraded("é") in out    # accented Latin
    # It really is an escape sequence, not the raw character.
    assert degraded("ソ").startswith("\\u")


def test_utf8_output_is_not_mangled(tmp_path, unicode_source_repo, monkeypatch):
    """Degrade only where the target genuinely cannot represent a character.

    A UTF-8 stdout can represent all of these, so it must receive them
    intact -- the fix must not "sanitise" output that was never in danger.
    """
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    main(["plugin", "install", str(unicode_source_repo)])

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    assert main(["search", "sonic", "--limit", "6"]) == 0
    stream.flush()
    out = raw.getvalue().decode("utf-8")

    assert "ソニックリンカー" in out
    assert "Приключения" in out
    assert "Pokémon Café Mix" in out
    assert "\\u" not in out


def test_configuring_the_stream_keeps_its_encoding(monkeypatch):
    """Only the error handler changes.

    Rewriting the encoding would change what a redirect or a pipe receives,
    which is the consumer's contract and not ours to alter.
    """
    stream, _ = cp1252_stdout(monkeypatch)
    configure_output_encoding()
    assert stream.encoding.lower().replace("-", "") == "cp1252"
    assert stream.errors == "backslashreplace"


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(monkeypatch):
    """pytest's capture object and StringIO have no `reconfigure`.

    Configuring output must never be the reason a command fails.
    """
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    configure_output_encoding()  # must not raise

    class Detached(io.StringIO):
        def reconfigure(self, **kwargs):
            raise ValueError("underlying buffer has been detached")

    monkeypatch.setattr(sys, "stdout", Detached())
    configure_output_encoding()  # must not raise either


def test_stderr_is_covered_too(tmp_path, monkeypatch):
    """Plugin failures print to stderr, and carry plugin-authored text."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stderr", stream)
    configure_output_encoding()
    print("error: ソニック failed", file=sys.stderr)
    stream.flush()
    degraded = "ソ".encode("cp1252", "backslashreplace").decode("cp1252")
    assert degraded in raw.getvalue().decode("cp1252")
