"""Saying what the active backend cannot do, before it costs anything.

The failure this exists to prevent is specific and expensive: an operator
runs `rom-hub import --collection "Shooters"` against a backend that has
no collections, waits for four gigabytes, and gets a 404 from an endpoint
they have never heard of -- with the ROM half-filed and the message about
HTTP rather than about collections.

Every test below therefore asserts two things: that the refusal happened,
and that **nothing was fetched**. The second is the whole point. A
refusal after the download is not degradation, it is just a nicer
traceback.

Both routes into an unsupported collection are covered, because
`--collection` is not the only one -- a plugin's own plan can name a
collection the operator never typed (Archive.org files under
"Archive.org" by default).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from rom_hub.backends.base import (
    ARTWORK,
    COLLECTIONS,
    IMPORT,
    METADATA,
    SCAN,
    CapabilityUnsupported,
    capabilities_of,
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


# -- import ----------------------------------------------------------------


def test_a_plan_naming_a_collection_is_refused_before_any_download(
    tmp_path, queue
):
    backend = LimitedBackend({IMPORT, SCAN})
    downloader = CountingDownloader()
    result = run_import(
        FakePlugin(_plan(collection="Archive.org")),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=downloader,
    )

    assert result.state is JobState.FAILED
    assert "collections" in result.message
    assert "Archive.org" in result.message
    # The assertion that matters: no bytes, no upload, no collection call.
    assert downloader.calls == []
    assert backend.uploads == []
    assert backend.collections_called == []


def test_the_failure_reaches_the_job_record(tmp_path, queue):
    """An operator reads `rom-hub jobs`, not the console they were not
    watching."""
    backend = LimitedBackend({IMPORT})
    result = run_import(
        FakePlugin(_plan(collection="Archive.org")),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=CountingDownloader(),
    )
    job = queue.get(result.job_id)
    assert job.state is JobState.FAILED
    assert "collections" in (job.error or "")


def test_the_same_plan_without_a_collection_imports_fine(tmp_path, queue):
    """Degradation is a refusal of the unsupported part, not of the tool."""
    backend = LimitedBackend({IMPORT, SCAN})

    # `list_roms` has to answer with the uploaded rom afterwards, or the
    # pipeline's own post-condition check fails for an unrelated reason.
    uploaded: list[dict] = []
    backend.list_roms = lambda platform_id: list(uploaded)
    original_upload = backend.upload_rom

    def upload(path, platform_id):
        original_upload(path, platform_id)
        uploaded.append({"id": 999, "fs_name": Path(path).name})

    backend.upload_rom = upload

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


def test_artwork_is_refused_without_being_fetched(tmp_path):
    """The cover is a network fetch. A backend that cannot take one should
    cost no download at all."""
    backend = LimitedBackend({METADATA})
    downloader = CountingDownloader()
    patch = MetadataPatch(
        name="Doom",
        artwork_url="https://allowed.example/cover.png",
        artwork_filename="cover.png",
    )
    with pytest.raises(CapabilityUnsupported) as exc:
        run_enrich(
            FakePlugin(patch=patch),
            REF,
            backend=backend,
            work_dir=tmp_path / "artwork",
            downloader=downloader,
        )
    assert "artwork" in str(exc.value)
    assert downloader.calls == []
    assert backend.updates == []


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


def test_collection_flag_is_refused_before_a_subprocess_starts(
    tmp_path, importer_repo, monkeypatch, capsys
):
    """No plugin process, no connection, no download -- just an answer.

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
    monkeypatch.setenv("ROM_HUB_BACKEND", "gaseous")
    assert main(["plugin", "install", str(importer_repo)]) == 0
    capsys.readouterr()

    assert main(["import", "demo", "x"]) != 0
    err = capsys.readouterr().err
    assert "gaseous" in err and "romm" in err


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
