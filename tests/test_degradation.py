"""Saying what the active backend cannot do, before it costs anything --
and doing the rest of the job anyway when what it cannot do is optional.

Two failures are being prevented here, and they pull in opposite
directions:

1. An operator runs `rom-hub import --collection "Shooters"` against a
   backend with no collections, waits for four gigabytes, and gets a 404
   from an endpoint they have never heard of -- ROM half-filed, message
   about HTTP. Every refusal test therefore asserts both that the refusal
   happened *and* that nothing was fetched. A refusal after the download
   is not degradation, it is a nicer traceback.

2. An operator runs `rom-hub import archive-org rubik_202308` against
   Gaseous or Retrom and gets **nothing at all**, because the plugin
   names a collection by default and neither backend has collections.
   That was the state of this file's policy until the essential/optional
   split: the ROM they asked for was refused over the label they did not.

So: `IMPORT` and `METADATA` refuse (nothing was fetched), `COLLECTIONS`
and `ARTWORK` are skipped with the skip reported in the result *and* on
the job row, and an explicitly typed `--collection` still refuses --
because a default nobody chose and a name somebody typed are not the same
request.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from rom_hub.backends.base import (
    ALL_CAPABILITIES,
    ARTWORK,
    COLLECTIONS,
    ESSENTIAL_CAPABILITIES,
    IMPORT,
    METADATA,
    OPTIONAL_CAPABILITIES,
    SCAN,
    UNGATED_CAPABILITIES,
    CapabilityUnsupported,
    capabilities_of,
    degrade,
    require,
)
from rom_hub.cli import main
from rom_hub.importer import run_import
from rom_hub.jobs import JobQueue, JobState
from rom_hub.metadata import EnrichError, run_enrich
from rom_hub.types import FetchFile, FetchPlan, MetadataPatch, RomRef, SearchResult

RESULT = SearchResult(source_id="item-1", title="Some Game")
REF = RomRef(rom_id=42, name="doom", filename="doom.zip", platform="dos")


class LimitedBackend:
    """A backend that can do exactly what it was told it can do."""

    def __init__(self, capabilities, name="limited"):
        self.name = name
        self._capabilities = frozenset(capabilities)
        self.uploads: list[tuple] = []
        self.collections_called: list[str] = []
        self.updates: list[tuple] = []

    def capabilities(self):
        return self._capabilities

    def platform_id(self, platform):
        return 7

    def list_roms(self, platform_id):
        return []

    def upload_rom(self, path, platform_id):
        self.uploads.append((Path(path), platform_id))

    def get_rom(self, rom_id):
        return {"id": rom_id, "name": "doom", "fs_name": "doom.zip"}

    def update_rom(self, rom_id, fields, artwork=None):
        self.updates.append((rom_id, dict(fields), artwork))
        return {"id": rom_id}

    def ensure_collection(self, name):
        self.collections_called.append(name)
        return 1

    def add_to_collection(self, collection_id, rom_ids):
        pass

    def scan_platform(self, platform_id):
        return {}

    def close(self):
        pass


class CountingDownloader:
    def __init__(self):
        self.calls: list[str] = []

    def download(self, url, dest, expected_size=None):
        self.calls.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"payload" * 64)
        return dest

    def close(self):
        pass


class FakePlugin:
    def __init__(self, plan=None, patch=None, slug="fake-plugin"):
        self.manifest = SimpleNamespace(slug=slug, network=["allowed.example"])
        self._plan = plan
        self._patch = patch

    def plan(self, result):
        return self._plan

    def enrich(self, rom):
        return self._patch


def _plan(collection=None):
    return FetchPlan(
        files=[FetchFile(url="https://allowed.example/g.zip", filename="g.zip")],
        platform="dos",
        collection=collection,
    )


@pytest.fixture
def queue(tmp_path):
    with JobQueue(tmp_path / "var" / "jobs.db") as q:
        yield q


# -- require() -------------------------------------------------------------


def test_require_names_the_backend_and_what_it_can_do():
    """"collections are not supported" invites "but I have collections"
    from someone looking at a different server than the Hub is."""
    backend = LimitedBackend({IMPORT}, name="example")
    with pytest.raises(CapabilityUnsupported) as exc:
        require(backend, COLLECTIONS, "--collection 'Shooters'")
    message = str(exc.value)
    assert "example" in message
    assert "collections" in message
    assert "--collection 'Shooters'" in message
    assert "import" in message  # what it *can* do


def test_require_passes_silently_when_supported():
    require(LimitedBackend({COLLECTIONS}), COLLECTIONS, "anything")


def test_require_can_carry_the_way_out():
    backend = LimitedBackend({IMPORT}, name="example")
    with pytest.raises(CapabilityUnsupported) as exc:
        require(backend, COLLECTIONS, "--collection 'x'", hint="Re-run without it.")
    assert "Re-run without it." in str(exc.value)


# -- the classification ----------------------------------------------------


def test_every_capability_is_classified():
    """A capability added later must be deliberately placed, not left to
    fall through whichever branch happens to be the default."""
    classified = (
        ESSENTIAL_CAPABILITIES | OPTIONAL_CAPABILITIES | UNGATED_CAPABILITIES
    )
    assert classified == ALL_CAPABILITIES
    assert not (ESSENTIAL_CAPABILITIES & OPTIONAL_CAPABILITIES)
    assert not (ESSENTIAL_CAPABILITIES & UNGATED_CAPABILITIES)
    assert not (OPTIONAL_CAPABILITIES & UNGATED_CAPABILITIES)


@pytest.mark.parametrize("capability", sorted(ESSENTIAL_CAPABILITIES))
def test_an_essential_capability_cannot_be_degraded(capability):
    """The guard rail on the whole policy: the way this gets weakened is
    a later caller quietly turning "cannot do the job" into a footnote."""
    with pytest.raises(ValueError, match="essential"):
        degrade(LimitedBackend(set()), capability, "something")


def test_degrade_returns_none_when_the_backend_can_do_it():
    assert degrade(LimitedBackend({COLLECTIONS}), COLLECTIONS, "x") is None


def test_a_skip_says_what_and_which_backend():
    step = degrade(LimitedBackend({IMPORT}, name="gaseous"), COLLECTIONS, "grouping it")
    assert step is not None
    assert "grouping it" in str(step)
    assert "gaseous" in str(step)
    assert "collections" in str(step)


# -- import ----------------------------------------------------------------


def _upload_is_visible_afterwards(backend):
    """Make `list_roms` answer with what was uploaded.

    The pipeline confirms its own upload by re-listing, so a backend stub
    that never reports the file fails every import for a reason that has
    nothing to do with capabilities.
    """
    uploaded: list[dict] = []
    backend.list_roms = lambda platform_id: list(uploaded)
    original_upload = backend.upload_rom

    def upload(path, platform_id):
        original_upload(path, platform_id)
        uploaded.append({"id": 999, "fs_name": Path(path).name})

    backend.upload_rom = upload
    return uploaded


def test_a_plan_naming_a_collection_still_imports_on_a_backend_without_them(
    tmp_path, queue
):
    """The bug this branch exists for.

    `rom-hub import archive-org rubik_202308` against Gaseous or Retrom
    downloaded nothing at all, because archive-org names a collection by
    default and there is no CLI flag that clears one. A collection is a
    grouping nicety; the ROM is the job.
    """
    backend = LimitedBackend({IMPORT, SCAN}, name="gaseous")
    _upload_is_visible_afterwards(backend)
    downloader = CountingDownloader()

    result = run_import(
        FakePlugin(_plan(collection="Archive.org")),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=downloader,
    )

    assert result.state is JobState.DONE, result.message
    assert len(backend.uploads) == 1
    assert downloader.calls  # the ROM was actually fetched
    # ... and the collection was not faked, only skipped.
    assert backend.collections_called == []


def test_the_skipped_collection_is_reported_in_the_outcome(tmp_path, queue):
    """"It worked" and "it worked, minus the grouping you asked for" are
    different outcomes and must read differently."""
    backend = LimitedBackend({IMPORT, SCAN}, name="gaseous")
    _upload_is_visible_afterwards(backend)

    result = run_import(
        FakePlugin(_plan(collection="Archive.org")),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=CountingDownloader(),
    )

    assert "collections" in result.message
    assert "Archive.org" in result.message
    assert "gaseous" in result.message
    assert [step.capability for step in result.degraded] == [COLLECTIONS]


def test_the_skip_reaches_the_job_record_without_looking_like_a_failure(
    tmp_path, queue
):
    """An operator reads `rom-hub jobs`, not the console they were not
    watching -- but a DONE job whose note landed in the error column reads
    as broken."""
    backend = LimitedBackend({IMPORT, SCAN}, name="retrom")
    _upload_is_visible_afterwards(backend)

    result = run_import(
        FakePlugin(_plan(collection="Archive.org")),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=CountingDownloader(),
    )

    job = queue.get(result.job_id)
    assert job.state is JobState.DONE
    assert "collections" in (job.notes or "")
    assert "Archive.org" in (job.notes or "")
    assert not job.error


def test_a_supported_collection_is_still_created(tmp_path, queue):
    """Degrading the unsupported case must not have quietly disabled the
    supported one."""
    backend = LimitedBackend({IMPORT, SCAN, COLLECTIONS})
    _upload_is_visible_afterwards(backend)

    result = run_import(
        FakePlugin(_plan(collection="Archive.org")),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=CountingDownloader(),
    )

    assert result.state is JobState.DONE, result.message
    assert backend.collections_called == ["Archive.org"]
    assert result.degraded == ()
    assert queue.get(result.job_id).notes is None


def test_an_essential_capability_still_refuses_before_any_download(
    tmp_path, queue
):
    """The half of this that must not be weakened. A backend that cannot
    be uploaded to has nowhere to put the ROM -- there is no reduced
    import left to do, so nothing is fetched."""
    backend = LimitedBackend({COLLECTIONS}, name="listener")
    downloader = CountingDownloader()

    result = run_import(
        FakePlugin(_plan()),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=downloader,
    )

    assert result.state is JobState.FAILED
    assert "import" in result.message
    assert "listener" in result.message
    # The assertion that matters: no bytes, no upload.
    assert downloader.calls == []
    assert backend.uploads == []
    job = queue.get(result.job_id)
    assert job.state is JobState.FAILED
    # A refusal is an error, not a note -- the opposite filing from a skip.
    assert "import" in (job.error or "")


def test_the_same_plan_without_a_collection_imports_fine(tmp_path, queue):
    """Degradation is a refusal of the unsupported part, not of the tool."""
    backend = LimitedBackend({IMPORT, SCAN})
    _upload_is_visible_afterwards(backend)

    result = run_import(
        FakePlugin(_plan()),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=CountingDownloader(),
    )
    assert result.state is JobState.DONE, result.message
    assert len(backend.uploads) == 1
    assert result.degraded == ()


# -- enrich ----------------------------------------------------------------


def test_enrich_against_a_backend_that_cannot_write_metadata(tmp_path):
    backend = LimitedBackend({IMPORT})
    with pytest.raises(CapabilityUnsupported) as exc:
        run_enrich(
            FakePlugin(patch=MetadataPatch(name="Doom")),
            REF,
            backend=backend,
            work_dir=tmp_path / "artwork",
        )
    assert "metadata" in str(exc.value)
    assert backend.updates == []


def test_artwork_is_dropped_without_being_fetched_and_the_fields_land(tmp_path):
    """The cover is a network fetch, so a backend that cannot take one
    costs no download -- but the name it *can* take is still written.
    Losing four fields because the fifth is an image is a worse answer
    than writing the four."""
    backend = LimitedBackend({METADATA}, name="fieldsonly")
    downloader = CountingDownloader()
    patch = MetadataPatch(
        name="Doom",
        artwork_url="https://allowed.example/cover.png",
        artwork_filename="cover.png",
    )
    result = run_enrich(
        FakePlugin(patch=patch),
        REF,
        backend=backend,
        work_dir=tmp_path / "artwork",
        downloader=downloader,
    )

    assert result.changed
    assert backend.updates == [(42, {"name": "Doom"}, None)]
    assert downloader.calls == []
    assert "artwork" in result.message
    assert [step.capability for step in result.degraded] == [ARTWORK]


def test_an_artwork_only_patch_reports_that_nothing_was_written(tmp_path):
    """Dropping the only thing the patch proposed leaves no operation.
    Reporting `changed` for it would be reporting a write that did not
    happen."""
    backend = LimitedBackend({METADATA}, name="fieldsonly")
    patch = MetadataPatch(
        artwork_url="https://allowed.example/cover.png",
        artwork_filename="cover.png",
    )
    result = run_enrich(
        FakePlugin(patch=patch),
        REF,
        backend=backend,
        work_dir=tmp_path / "artwork",
        downloader=CountingDownloader(),
    )

    assert not result.changed
    assert backend.updates == []
    assert "artwork" in result.message
    assert "nothing was written" in result.message


def test_artwork_still_reaches_a_backend_that_takes_it(tmp_path):
    """The supported path, unchanged by the degradation of the other."""
    backend = LimitedBackend({METADATA, ARTWORK})
    patch = MetadataPatch(
        name="Doom", artwork_base64="aGVsbG8=", artwork_filename="cover.png"
    )
    result = run_enrich(
        FakePlugin(patch=patch), REF, backend=backend, work_dir=tmp_path / "artwork"
    )
    assert result.changed
    assert result.degraded == ()
    assert backend.updates[0][2] is not None


def test_a_field_only_patch_still_applies_without_artwork_support(tmp_path):
    backend = LimitedBackend({METADATA})
    result = run_enrich(
        FakePlugin(patch=MetadataPatch(name="Doom")),
        REF,
        backend=backend,
        work_dir=tmp_path / "artwork",
    )
    assert result.changed
    assert backend.updates == [(42, {"name": "Doom"}, None)]


def test_an_enrich_error_is_still_an_enrich_error(tmp_path):
    """Sanity: capability refusals did not swallow the ordinary failures."""
    backend = LimitedBackend({METADATA, ARTWORK})

    class Boom(FakePlugin):
        def enrich(self, rom):
            raise RuntimeError("upstream is down")

    with pytest.raises(EnrichError, match="upstream is down"):
        run_enrich(Boom(), REF, backend=backend, work_dir=tmp_path / "a")


# -- the CLI ---------------------------------------------------------------


IMPORTER_MANIFEST = """
[plugin]
slug = "demo"
name = "Demo"
version = "0.1.0"
rpp_version = "1"

[capabilities]
importer = "demo:Importer"

[permissions]
network = ["demo.example"]
romm_api = []
"""

IMPORTER_PLUGIN = """
from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider


class Importer(ImportProvider):
    def plan(self, result):
        return FetchPlan(
            files=[FetchFile(url="https://demo.example/g.zip", filename="g.zip")],
            platform="dos",
        )
"""


@pytest.fixture
def importer_repo(tmp_path):
    repo = tmp_path / "importer-plugin"
    repo.mkdir()
    (repo / "manifest.toml").write_text(IMPORTER_MANIFEST, encoding="utf-8")
    (repo / "demo.py").write_text(IMPORTER_PLUGIN, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


def test_an_explicitly_typed_collection_is_refused_with_a_way_forward(
    tmp_path, importer_repo, monkeypatch, capsys
):
    """The asymmetry, tested.

    A plugin's default collection is dropped and noted. A name the
    operator typed is not: silently importing somewhere other than where
    they said is how a library ends up unsorted with nothing to explain
    it. So this refuses -- and, because a refusal an operator cannot act
    on is a dead end, it says what to run instead.

    No plugin process, no connection, no download -- just an answer.
    Refused early enough that this passes on a host with no sandbox and
    no ROM_HUB_ALLOW_UNSANDBOXED, which is itself the evidence that no
    PluginProcess was ever started.
    """
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROM_HUB_ALLOW_UNSANDBOXED", raising=False)
    monkeypatch.setattr(
        "rom_hub.cli.open_backend", lambda *a, **k: LimitedBackend({IMPORT})
    )
    assert main(["plugin", "install", str(importer_repo)]) == 0
    capsys.readouterr()

    assert main(["import", "demo", "x", "--collection", "Shooters"]) != 0
    err = capsys.readouterr().err
    assert "collections" in err
    assert "Shooters" in err
    assert "limited" in err
    # The way out, not just the diagnosis.
    assert "--collection" in err
    assert "ungrouped" in err


def test_the_same_import_without_the_flag_is_not_refused(
    tmp_path, importer_repo, monkeypatch, capsys
):
    """The counterpart: the flag is what was refused, not the import.

    The demo plugin's plan names no collection, so this gets as far as
    starting a PluginProcess -- which is exactly the point, and is why it
    needs the unsandboxed opt-out the refusal test deliberately does not.
    """
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    backend = LimitedBackend({IMPORT})
    monkeypatch.setattr("rom_hub.cli.open_backend", lambda *a, **k: backend)
    assert main(["plugin", "install", str(importer_repo)]) == 0
    capsys.readouterr()

    # It will fail later (the fake demo.example host is unreachable), but
    # the failure must be a download, not a capability refusal.
    main(["import", "demo", "x"])
    err = capsys.readouterr().err
    assert "collections" not in err


def test_an_unconfigured_backend_is_reported_not_raised(
    tmp_path, importer_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    for name in (
        "ROMM_URL",
        "ROMM_USER",
        "ROMM_PASSWORD",
        "ROM_HUB_BACKEND_URL",
        "ROM_HUB_BACKEND_USER",
        "ROM_HUB_BACKEND_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    assert main(["plugin", "install", str(importer_repo)]) == 0
    capsys.readouterr()

    assert main(["import", "demo", "x"]) != 0
    assert "ROMM_URL" in capsys.readouterr().err


def test_an_unknown_backend_names_the_ones_that_exist(
    tmp_path, importer_repo, monkeypatch, capsys
):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    # Not `gaseous`: that used to be the stand-in for "a backend that does
    # not exist", and once it did exist this test started asserting on an
    # unconfigured-backend message instead of an unknown-backend one.
    monkeypatch.setenv("ROM_HUB_BACKEND", "not-a-real-backend")
    assert main(["plugin", "install", str(importer_repo)]) == 0
    capsys.readouterr()

    assert main(["import", "demo", "x"]) != 0
    err = capsys.readouterr().err
    assert "not-a-real-backend" in err
    assert "romm" in err and "gaseous" in err


# -- backend info ----------------------------------------------------------


def test_backend_info_shows_the_active_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROM_HUB_BACKEND", raising=False)
    assert main(["backend", "info"]) == 0
    out = capsys.readouterr().out
    assert "romm" in out
    assert "ROMM_URL" in out
    for capability in (IMPORT, COLLECTIONS, METADATA, ARTWORK, SCAN):
        assert capability in out


def test_backend_info_needs_no_connection(tmp_path, monkeypatch, capsys):
    """The operator most likely to run this is the one who cannot connect."""
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    for name in ("ROMM_URL", "ROMM_USER", "ROMM_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert main(["backend", "info"]) == 0
    out = capsys.readouterr().out
    assert "not set" in out


def test_backend_info_lists_what_a_backend_cannot_do(
    tmp_path, monkeypatch, capsys
):
    """Absence from a list reads as an oversight; "cannot" reads as an
    answer."""

    class Partial:
        name = "partial"
        SETTING_NAMES = (("PARTIAL_URL", "ROM_HUB_BACKEND_URL"),)
        CAPABILITIES = frozenset({IMPORT})

    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("rom_hub.backends.backend_class", lambda name: Partial)
    assert main(["backend", "info", "--backend", "partial"]) == 0
    out = capsys.readouterr().out
    assert "cannot:" in out
    assert "collections" in out.split("cannot:")[1]
    assert "artwork" in out.split("cannot:")[1]


def test_backend_info_refuses_an_unknown_backend(tmp_path, monkeypatch, capsys):
    # Deliberately a name no backend will ever have. This used to say
    # "retrom", which stopped being unknown the moment Retrom shipped --
    # a placeholder that names a real target dates the instant it lands.
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    assert main(["backend", "info", "--backend", "no-such-backend"]) != 0
    assert "no-such-backend" in capsys.readouterr().err


def test_capabilities_of_reads_the_declaration():
    assert capabilities_of(LimitedBackend({IMPORT, SCAN})) == frozenset(
        {IMPORT, SCAN}
    )
