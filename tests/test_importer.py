"""End-to-end import pipeline tests.

No test here may require a live RomM: `FakeRomm` stands in for the
`LibraryBackend`, `FakeDownloader` for the network, and the backend's
`upload_rom` is replaced by a recording fake. The `HttpDownloader` tests are the exception -- they
exercise the real class, but over `httpx.MockTransport`, so still no
socket is opened.
"""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from rom_hub.backends.base import COLLECTIONS, IMPORT, SCAN
from rom_hub.dedup import hash_file
from rom_hub.importer import (
    DownloadError,
    HttpDownloader,
    ImportResult,
    dest_in_job_dir,
    run_import,
)
from rom_hub.jobs import JobQueue, JobState
from rom_hub.backends.romm.client import RommError
from rom_hub.backends.romm.scan import ScanError
from rom_hub.types import FetchFile, FetchPlan, SearchResult

ROM_BYTES = b"MZ\x90\x00rom payload" * 64
RESULT = SearchResult(source_id="item-1", title="Some Game")


def _plan(url="https://allowed.example/g.zip", collection=None, files=None):
    return FetchPlan(
        files=files or [FetchFile(url=url, filename="g.zip")],
        platform="dos",
        collection=collection,
    )


class FakePlugin:
    """Stands in for a started PluginProcess.

    Only `.plan()` and `.manifest` are reached by the pipeline; the rest
    of PluginProcess is subprocess plumbing the importer never touches.
    """

    def __init__(self, plan=None, error=None, slug="fake-plugin"):
        self.manifest = SimpleNamespace(slug=slug, network=["allowed.example"])
        self._plan = plan
        self._error = error
        self.plan_calls = []

    def plan(self, result):
        self.plan_calls.append(result)
        if self._error is not None:
            raise self._error
        return self._plan


class FakeRomm:
    """A `LibraryBackend` with only the members an import reaches.

    Two members are new since the RomM client sat here directly, and both
    are the seam showing through rather than test scaffolding:
    `upload_rom` (the backend now owns the upload, so `upload_file` is no
    longer a module-level function the pipeline calls) and
    `capabilities` (the pipeline refuses a collection up front rather
    than at an HTTP call). Pass `capabilities=` to model a backend that
    cannot do something.
    """

    name = "fake"

    def __init__(
        self,
        platforms=None,
        roms=None,
        list_roms_error=None,
        capabilities=(IMPORT, COLLECTIONS, SCAN),
    ):
        self.platforms = {"dos": 7} if platforms is None else platforms
        self.roms = roms or []
        self.list_roms_error = list_roms_error
        self._capabilities = frozenset(capabilities)
        self.list_roms_calls = 0
        self.collections: dict[str, int] = {}
        self.ensure_collection_calls: list[str] = []
        self.add_to_collection_calls: list[tuple[int, list[int]]] = []
        self.pending: list[Path] = []
        self._next_rom_id = 999

    def capabilities(self):
        return self._capabilities

    def platform_id(self, slug):
        if slug not in self.platforms:
            raise RommError(f"no RomM platform matches slug {slug!r}")
        return self.platforms[slug]

    def upload_rom(self, path, platform_id):
        """The default: bytes accepted, no row created. See receive_upload.

        The `upload` fixture replaces this with a recording FakeUpload for
        the tests that care how it was called.
        """
        self.receive_upload(Path(path))

    def list_roms(self, platform_id):
        self.list_roms_calls += 1
        if self.list_roms_error is not None:
            raise self.list_roms_error
        return list(self.roms)

    def receive_upload(self, path):
        """What a real RomM actually does with an accepted upload: it
        writes the bytes into the library directory and creates **no
        database row**. The file exists on disk and `/api/roms` still does
        not list it. Nothing comes back in the /complete response either
        -- that is a bare 201 with no body.

        So an accepted upload lands in `pending`, not in `roms`. Only
        `register_uploads` -- this fake's stand-in for the library scan --
        makes it visible. Verified against RomM 4.9.2, where
        `GET /api/roms?platform_ids=1` answered `total=0` after a
        successful chunked upload.
        """
        self.pending.append(path)

    def register_uploads(self):
        """What RomM's `scan` socket event does: walk the library
        directory and create rows for files that have none yet."""
        for path in self.pending:
            hashes = hash_file(path)
            self.roms.append(
                {
                    "id": self._next_rom_id,
                    "name": path.name,
                    "fs_name": path.name,
                    "crc_hash": hashes.crc32,
                    "md5_hash": hashes.md5,
                    "sha1_hash": hashes.sha1,
                }
            )
            self._next_rom_id += 1
        self.pending = []

    def ensure_collection(self, name):
        self.ensure_collection_calls.append(name)
        return self.collections.setdefault(name, 100 + len(self.collections))

    def add_to_collection(self, collection_id, rom_ids):
        self.add_to_collection_calls.append((collection_id, list(rom_ids)))


class FakeDownloader:
    def __init__(self, content=ROM_BYTES, error=None):
        self.content = content
        self.error = error
        self.calls: list[tuple[str, Path]] = []

    def download(self, url, dest, expected_size=None):
        dest = Path(dest)
        self.calls.append((url, dest))
        if self.error is not None:
            raise self.error
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.content)
        return dest


class FakeUpload:
    """Replaces the backend's `upload_rom`. The point of the duplicate
    test is that this is never called at all, so it counts its calls.

    The return value is `{}` and nobody reads it: `LibraryBackend.
    upload_rom` returns nothing, because RomM's /complete answers 201
    with no body and there is no rom id in it to hand back.
    `lands=False` simulates the nastier case -- the server reports
    success but the ROM never appears in the library.
    """

    def __init__(self, error=None, lands=True):
        self.error = error
        self.lands = lands
        self.calls: list[tuple[Path, int]] = []

    def __call__(self, client, path, platform_id, **kwargs):
        path = Path(path)
        self.calls.append((path, platform_id))
        if self.error is not None:
            raise self.error
        if self.lands:
            client.receive_upload(path)
        return {}


class FakeScanner:
    """Stands in for `SocketIOScanner`. Satisfies the `Scanner` protocol
    without opening a socket.

    On success it calls `register_uploads()` on the FakeRomm it was built
    for, which is what makes the pipeline's post-upload confirmation
    possible -- exactly as the real scan is what creates the rows that
    `GET /api/roms` then returns.
    """

    def __init__(self, romm=None, error=None):
        self.romm = romm
        self.error = error
        self.calls: list[int] = []
        # Recorded at scan time so ordering against the other steps is
        # observable rather than assumed.
        self.uploads_at_scan: list[tuple[Path, int]] | None = None
        self.listings_at_scan: int | None = None

    def scan_platform(self, platform_id):
        self.calls.append(platform_id)
        if self.romm is not None:
            self.listings_at_scan = self.romm.list_roms_calls
        if self.error is not None:
            raise self.error
        if self.romm is not None:
            self.romm.register_uploads()
        return {"scanned_platforms": 1, "new_roms": 1}


@pytest.fixture
def upload(monkeypatch):
    """Record every upload the pipeline asks the backend to perform.

    Patched onto the class rather than onto a module function, because
    uploading is the backend's job now: `run_import` calls
    `backend.upload_rom(path, platform_id)` and there is no module-level
    `upload_file` in the pipeline to intercept.
    """
    fake = FakeUpload()
    monkeypatch.setattr(
        FakeRomm,
        "upload_rom",
        lambda self, path, platform_id: fake(self, path, platform_id),
    )
    return fake


@pytest.fixture
def queue(tmp_path):
    with JobQueue(tmp_path / "var" / "jobs.db") as q:
        yield q


def _run(tmp_path, plugin, romm, queue, downloader=None, scanner=None, **kwargs):
    return run_import(
        plugin,
        RESULT,
        backend=romm,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=downloader if downloader is not None else FakeDownloader(),
        scanner=scanner if scanner is not None else FakeScanner(romm),
        **kwargs,
    )


# -- the pipeline ---------------------------------------------------------


def test_happy_path_reaches_done_and_reports_a_rom_id(tmp_path, queue, upload):
    romm = FakeRomm()
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert isinstance(res, ImportResult)
    assert res.state is JobState.DONE
    assert res.rom_id == 999
    assert queue.get(res.job_id).state is JobState.DONE
    assert len(upload.calls) == 1
    # The integer platform id, never the slug -- x-upload-platform is an int.
    assert upload.calls[0][1] == 7


def test_the_downloaded_file_lands_under_the_job_id_directory(tmp_path, queue, upload):
    downloader = FakeDownloader()
    res = _run(tmp_path, FakePlugin(_plan()), FakeRomm(), queue, downloader)

    expected = tmp_path / "downloads" / str(res.job_id) / "g.zip"
    assert downloader.calls[0][1] == expected
    assert expected.read_bytes() == ROM_BYTES
    # The job records where the bytes are, so a retry can resume them.
    assert queue.get(res.job_id).local_path == str(expected.parent)


def test_the_rom_id_is_looked_up_by_hash_not_read_from_the_complete_response(
    tmp_path, queue, upload
):
    """RomM's /complete is a bare 201 with no body, so there is no id to
    read. The id has to be found by locating our own hash in the library."""
    romm = FakeRomm()
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert upload.calls != []
    assert res.rom_id == 999
    # One listing for dedup, one after the upload to confirm and identify.
    assert romm.list_roms_calls == 2


def test_an_upload_that_never_appears_in_the_library_lands_failed(
    tmp_path, queue, monkeypatch
):
    """A 201 says the server accepted the request. It does not say the ROM
    is in the library. Reporting DONE on the strength of a status code
    alone is exactly the bug this check exists to catch."""
    fake = FakeUpload(lands=False)
    monkeypatch.setattr(
        FakeRomm,
        "upload_rom",
        lambda self, path, platform_id: fake(self, path, platform_id),
    )
    romm = FakeRomm()

    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert fake.calls != [], "the upload must have been attempted"
    assert res.state is JobState.FAILED
    assert res.rom_id is None
    assert "g.zip" in res.message
    assert "did not appear" in res.message
    assert "did not appear" in queue.get(res.job_id).error


# -- the pre-download duplicate check -------------------------------------


def test_a_filename_already_on_the_platform_skips_without_fetching_any_bytes(
    tmp_path, queue, upload
):
    """The payoff: a re-import of something already in the library must not
    cost a download at all. Asserting on the state alone would pass even if
    the whole file had been fetched first."""
    romm = FakeRomm(roms=[{"id": 4242, "fs_name": "g.zip"}])
    downloader = FakeDownloader()

    res = _run(tmp_path, FakePlugin(_plan()), romm, queue, downloader)

    assert downloader.calls == [], "no bytes may be fetched for a known file"
    assert upload.calls == []
    assert res.state is JobState.SKIPPED_DUPLICATE
    assert res.rom_id == 4242


def test_the_precheck_skips_only_the_files_that_are_already_present(
    tmp_path, queue, upload
):
    """A partially-present plan still has to fetch the missing half."""
    plan = _plan(
        files=[
            FetchFile(url="https://allowed.example/a.zip", filename="a.zip"),
            FetchFile(url="https://allowed.example/b.zip", filename="b.zip"),
        ]
    )
    romm = FakeRomm(roms=[{"id": 4242, "fs_name": "a.zip"}])
    downloader = FakeDownloader()

    res = _run(tmp_path, FakePlugin(plan), romm, queue, downloader)

    assert [c[1].name for c in downloader.calls] == ["b.zip"]
    assert [c[0].name for c in upload.calls] == ["b.zip"]
    assert res.state is JobState.DONE
    assert "already present" in res.message


def test_the_precheck_listing_is_reused_for_hash_dedup_not_fetched_twice(
    tmp_path, queue, upload
):
    """One listing serves both checks. A real library is thousands of roms
    and this runs on every import."""
    romm = FakeRomm()
    _run(tmp_path, FakePlugin(_plan()), romm, queue)

    # One before the download, one after the scan to confirm. Not three.
    assert romm.list_roms_calls == 2


def test_an_upload_is_confirmed_by_filename_when_the_hash_cannot_match(
    tmp_path, queue, upload
):
    """RomM's digest for a .7z or .rar cannot be reproduced here, so the
    confirmation would otherwise report a ROM missing that landed fine.
    The name is decisive at this point: the Hub chose it, and RomM writes
    it to `roms/<slug>/<that name>`."""

    class HashlessRomm(FakeRomm):
        def register_uploads(self):
            # Registers the row, but with hashes the Hub could never have
            # computed -- exactly what happens for an unreadable archive.
            for path in self.pending:
                self.roms.append(
                    {"id": self._next_rom_id, "fs_name": path.name,
                     "sha1_hash": "0" * 40}
                )
                self._next_rom_id += 1
            self.pending = []

    romm = HashlessRomm()
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue, scanner=FakeScanner(romm))

    assert res.state is JobState.DONE
    assert res.rom_id == 999


def test_a_different_filename_is_not_treated_as_a_duplicate(
    tmp_path, queue, upload
):
    romm = FakeRomm(roms=[{"id": 4242, "fs_name": "something-else.zip"}])
    downloader = FakeDownloader()

    res = _run(tmp_path, FakePlugin(_plan()), romm, queue, downloader)

    assert downloader.calls != []
    assert res.state is JobState.DONE


# -- the library scan (RomM's upload creates no row on its own) -----------


def test_an_upload_is_registered_with_a_scan_scoped_to_that_platform(
    tmp_path, queue, upload
):
    """Without this the ROM is on disk and absent from the library."""
    romm = FakeRomm()
    scanner = FakeScanner(romm)
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue, scanner=scanner)

    assert res.state is JobState.DONE
    # The platform id, not the slug, and only the one just uploaded to.
    assert scanner.calls == [7]


def test_the_scan_runs_after_the_upload_and_before_the_confirming_listing(
    tmp_path, queue, upload
):
    """Ordering is the whole point. Scanning before the upload registers
    nothing; confirming before the scan reports a ROM missing that is
    merely unregistered."""
    romm = FakeRomm()
    scanner = FakeScanner(romm)
    _run(tmp_path, FakePlugin(_plan()), romm, queue, scanner=scanner)

    # One listing had happened (the dedup one) when the scan ran; the
    # confirming listing had not.
    assert scanner.listings_at_scan == 1
    assert romm.list_roms_calls == 2


def test_nothing_is_scanned_when_every_file_was_already_a_duplicate(
    tmp_path, queue, upload
):
    """No upload means nothing to register, and a scan of a real library
    is expensive enough that firing one for nothing is a bug."""
    scratch = tmp_path / "scratch.bin"
    scratch.write_bytes(ROM_BYTES)
    romm = FakeRomm(
        roms=[{"id": 4242, "sha1_hash": hash_file(scratch).sha1, "name": "Some Game"}]
    )
    scanner = FakeScanner(romm)

    res = _run(tmp_path, FakePlugin(_plan()), romm, queue, scanner=scanner)

    assert res.state is JobState.SKIPPED_DUPLICATE
    assert scanner.calls == []


@pytest.mark.parametrize(
    "error",
    [
        ScanError("timed out after 600s waiting for RomM"),
        ScanError("could not open a socket.io connection to RomM"),
        RuntimeError("something entirely unexpected"),
    ],
    ids=["timeout", "connect-failure", "unexpected"],
)
def test_a_scan_that_fails_lands_failed_and_says_the_upload_itself_worked(
    tmp_path, queue, upload, error
):
    """The operator has to be able to tell these apart: the bytes are in
    RomM's library directory, and only the registration did not happen.
    Reporting a plain upload failure would send them re-uploading a file
    that is already there."""
    romm = FakeRomm()
    res = _run(
        tmp_path,
        FakePlugin(_plan()),
        romm,
        queue,
        scanner=FakeScanner(romm, error=error),
    )

    assert res.state is JobState.FAILED
    assert res.rom_id is None
    message = queue.get(res.job_id).error
    assert "uploaded" in message
    assert "registration" in message or "register" in message
    assert "g.zip" in message


def test_a_failed_scan_never_reports_done_even_though_the_bytes_arrived(
    tmp_path, queue, upload
):
    """The regression guard for the whole blocker: an accepted upload is
    not an imported ROM."""
    romm = FakeRomm()
    res = _run(
        tmp_path,
        FakePlugin(_plan()),
        romm,
        queue,
        scanner=FakeScanner(romm, error=ScanError("nope")),
    )

    assert upload.calls != [], "the upload must actually have been attempted"
    assert res.state is not JobState.DONE
    assert romm.roms == [], "nothing was ever registered in the library"


def test_the_collection_step_is_skipped_when_the_scan_failed(
    tmp_path, queue, upload
):
    """A failure must stop the pipeline, not merely be recorded."""
    romm = FakeRomm()
    res = _run(
        tmp_path,
        FakePlugin(_plan(collection="Shareware")),
        romm,
        queue,
        scanner=FakeScanner(romm, error=ScanError("nope")),
    )

    assert res.state is JobState.FAILED
    assert romm.ensure_collection_calls == []
    assert romm.add_to_collection_calls == []


def test_a_collection_is_populated_with_the_looked_up_id(tmp_path, queue, upload):
    """The collection step depends on the id, so it must run after the
    lookup -- not off a value scraped from the upload response."""
    romm = FakeRomm()
    res = _run(tmp_path, FakePlugin(_plan(collection="Shareware")), romm, queue)

    assert res.state is JobState.DONE
    assert romm.add_to_collection_calls == [(100, [999])]


def test_a_multi_file_plan_costs_one_extra_listing_not_one_per_file(
    tmp_path, queue, upload
):
    """A library can hold thousands of roms; the lookup must not be
    per-file."""
    plan = _plan(
        files=[
            FetchFile(url="https://allowed.example/a.zip", filename="a.zip"),
            FetchFile(url="https://allowed.example/b.zip", filename="b.zip"),
            FetchFile(url="https://allowed.example/c.zip", filename="c.zip"),
        ]
    )
    downloader = FakeDownloader()

    def distinct(url, dest, expected_size=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(ROM_BYTES + dest.name.encode())
        downloader.calls.append((url, dest))
        return dest

    downloader.download = distinct
    romm = FakeRomm()
    res = _run(tmp_path, FakePlugin(plan), romm, queue, downloader)

    assert res.state is JobState.DONE
    assert romm.list_roms_calls == 2
    assert romm.ensure_collection_calls == []
    assert len(upload.calls) == 3
    # One upload per file, each to its own path -- never the same path
    # twice, which is what two entries sharing a filename would produce.
    assert len({c[0] for c in upload.calls}) == len(plan.files)


def test_a_duplicate_is_skipped_and_the_upload_fake_is_never_called(
    tmp_path, queue, upload
):
    """The requirement is not "ends in SKIPPED_DUPLICATE", it is "no bytes
    were uploaded". Assert on the upload fake, not just on the state."""
    scratch = tmp_path / "scratch.bin"
    scratch.write_bytes(ROM_BYTES)
    hashes = hash_file(scratch)

    romm = FakeRomm(
        roms=[{"id": 4242, "sha1_hash": hashes.sha1.upper(), "name": "Some Game"}]
    )
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert upload.calls == []
    assert res.state is JobState.SKIPPED_DUPLICATE
    assert res.rom_id == 4242
    assert queue.get(res.job_id).state is JobState.SKIPPED_DUPLICATE


def test_an_unresolvable_platform_fails_with_a_message_naming_the_slug(
    tmp_path, queue, upload
):
    romm = FakeRomm(platforms={"nes": 1})
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert res.state is JobState.FAILED
    assert "dos" in res.message
    assert "dos" in queue.get(res.job_id).error
    assert upload.calls == []


def test_a_download_failure_lands_failed_and_the_job_is_retryable(
    tmp_path, queue, upload
):
    plugin = FakePlugin(_plan())
    romm = FakeRomm()
    broken = FakeDownloader(error=DownloadError("connection reset by peer"))

    first = _run(tmp_path, plugin, romm, queue, broken)
    assert first.state is JobState.FAILED
    assert "connection reset by peer" in first.message
    assert "connection reset by peer" in queue.get(first.job_id).error
    assert upload.calls == []

    # Retrying means re-running the SAME job -- that is what makes the
    # partially downloaded bytes under download_dir/<job_id>/ reusable.
    second = _run(tmp_path, plugin, romm, queue, job_id=first.job_id)
    assert second.job_id == first.job_id
    assert second.state is JobState.DONE
    assert len(upload.calls) == 1


def test_a_successful_retry_clears_the_stale_error_text(tmp_path, queue, upload):
    """jobs.set_state() treats error=None as "leave unchanged", so without
    an explicit clear a job would show DONE next to the error text from
    the attempt before it -- a record that lies about what happened."""
    plugin = FakePlugin(_plan())
    romm = FakeRomm()

    failed = _run(
        tmp_path, plugin, romm, queue, FakeDownloader(error=DownloadError("boom"))
    )
    assert "boom" in queue.get(failed.job_id).error

    done = _run(tmp_path, plugin, romm, queue, job_id=failed.job_id)
    job = queue.get(done.job_id)
    assert job.state is JobState.DONE
    assert not job.error, f"stale error survived a successful retry: {job.error!r}"


def test_collection_calls_happen_when_the_plan_names_one(tmp_path, queue, upload):
    romm = FakeRomm()
    res = _run(tmp_path, FakePlugin(_plan(collection="Shareware")), romm, queue)

    assert res.state is JobState.DONE
    assert romm.ensure_collection_calls == ["Shareware"]
    assert romm.add_to_collection_calls == [(100, [999])]


def test_no_collection_calls_when_the_plan_names_none(tmp_path, queue, upload):
    romm = FakeRomm()
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert res.state is JobState.DONE
    assert romm.ensure_collection_calls == []
    assert romm.add_to_collection_calls == []


def test_a_plugin_whose_plan_raises_lands_failed_not_an_exception(
    tmp_path, queue, upload
):
    plugin = FakePlugin(error=RuntimeError("plugin exploded"))
    res = _run(tmp_path, plugin, FakeRomm(), queue)

    assert res.state is JobState.FAILED
    assert "plugin exploded" in res.message
    assert "plugin exploded" in queue.get(res.job_id).error
    assert upload.calls == []


def test_an_upload_failure_lands_failed_with_the_reason(tmp_path, queue, monkeypatch):
    fake = FakeUpload(error=RommError("chunk 2 rejected (400)"))
    monkeypatch.setattr(
        FakeRomm,
        "upload_rom",
        lambda self, path, platform_id: fake(self, path, platform_id),
    )

    res = _run(tmp_path, FakePlugin(_plan()), FakeRomm(), queue)
    assert res.state is JobState.FAILED
    assert "chunk 2 rejected" in res.message


def test_a_collection_failure_after_a_successful_upload_is_reported(
    tmp_path, queue, upload
):
    romm = FakeRomm()

    def boom(name):
        raise RommError("collections endpoint returned 500")

    romm.ensure_collection = boom
    res = _run(tmp_path, FakePlugin(_plan(collection="Shareware")), romm, queue)

    assert res.state is JobState.FAILED
    assert "Shareware" in res.message
    assert "collections endpoint returned 500" in res.message


def test_listing_existing_roms_failing_does_not_upload_blind(tmp_path, queue, upload):
    """Dedup that could not run is not dedup that passed. Uploading anyway
    would put a duplicate in the library on every transient 500."""
    romm = FakeRomm(list_roms_error=RommError("GET /api/roms failed (503)"))
    res = _run(tmp_path, FakePlugin(_plan()), romm, queue)

    assert res.state is JobState.FAILED
    assert upload.calls == []


def test_the_plans_platform_is_recorded_on_the_job(tmp_path, queue, upload):
    res = _run(tmp_path, FakePlugin(_plan()), FakeRomm(), queue)
    assert queue.get(res.job_id).platform == "dos"


def test_every_file_in_a_multi_file_plan_is_downloaded_and_uploaded(
    tmp_path, queue, upload
):
    plan = _plan(
        files=[
            FetchFile(url="https://allowed.example/a.zip", filename="a.zip"),
            FetchFile(url="https://allowed.example/b.zip", filename="b.zip"),
        ]
    )
    downloader = FakeDownloader()
    res = _run(tmp_path, FakePlugin(plan), FakeRomm(), queue, downloader)

    assert res.state is JobState.DONE
    assert [c[1].name for c in downloader.calls] == ["a.zip", "b.zip"]
    assert [c[0].name for c in upload.calls] == ["a.zip", "b.zip"]


def test_run_import_closes_the_downloader_it_built_itself(tmp_path, queue, upload):
    """The CLI path passes no downloader, so run_import constructs one --
    and an httpx.Client it opened is one it has to close. Harmless for a
    one-shot CLI that exits; a leak for anything calling this in a loop."""
    built = []

    class TrackingDownloader(HttpDownloader):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.closed = False
            built.append(self)

        def download(self, url, dest, expected_size=None):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(ROM_BYTES)
            return dest

        def close(self):
            self.closed = True
            super().close()

    romm = FakeRomm()
    monkey = pytest.MonkeyPatch()
    monkey.setattr("rom_hub.importer.HttpDownloader", TrackingDownloader)
    try:
        res = run_import(
            FakePlugin(_plan()),
            RESULT,
            backend=romm,
            queue=queue,
            download_dir=tmp_path / "downloads",
            # Supplied explicitly: this test is about the downloader run_import
            # builds for itself, and the default scanner would open a real
            # socket to a RomM that does not exist here.
            scanner=FakeScanner(romm),
        )
    finally:
        monkey.undo()

    assert res.state is JobState.DONE
    assert len(built) == 1
    assert built[0].closed is True
    assert built[0]._client.is_closed is True


def test_run_import_does_not_close_a_downloader_it_was_handed(
    tmp_path, queue, upload
):
    """Only close what this function owns: a caller-supplied downloader may
    be reused across imports."""

    class ClosableFake(FakeDownloader):
        closed = False

        def close(self):
            self.closed = True

    downloader = ClosableFake()
    _run(tmp_path, FakePlugin(_plan()), FakeRomm(), queue, downloader)
    assert downloader.closed is False


def test_the_downloader_is_closed_even_when_the_import_fails(
    tmp_path, queue, upload
):
    built = []

    class TrackingDownloader(HttpDownloader):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.closed = False
            built.append(self)

        def download(self, url, dest, expected_size=None):
            raise DownloadError("the network went away")

        def close(self):
            self.closed = True
            super().close()

    monkey = pytest.MonkeyPatch()
    monkey.setattr("rom_hub.importer.HttpDownloader", TrackingDownloader)
    try:
        res = run_import(
            FakePlugin(_plan()),
            RESULT,
            backend=FakeRomm(),
            queue=queue,
            download_dir=tmp_path / "downloads",
        )
    finally:
        monkey.undo()

    assert res.state is JobState.FAILED
    assert built[0].closed is True


# -- the job-directory containment guard -----------------------------------
#
# FetchFile's validator is the first layer. These tests pin the second one:
# the host must refuse to write outside the job directory even when a name
# reached it *unvalidated*, so that a future gap in the validator cannot
# become a filesystem write. Both tests deliberately bypass pydantic with
# model_construct -- that is the point.


@pytest.mark.parametrize(
    "evil", ["C:evil.zip", "..", "../evil.zip", "sub/evil.zip", "\\\\srv\\s\\e.zip"]
)
def test_a_name_that_slipped_past_the_validator_is_still_not_written(
    tmp_path, queue, upload, evil
):
    """The containment layer, mutation-checked: delete it and this fails."""
    plan = FetchPlan.model_construct(
        files=[
            FetchFile.model_construct(
                url="https://allowed.example/g.zip", filename=evil, size_bytes=None
            )
        ],
        platform="dos",
        collection=None,
    )
    downloader = FakeDownloader()
    res = _run(tmp_path, FakePlugin(plan), FakeRomm(), queue, downloader)

    assert res.state is JobState.FAILED
    # Refused *before* the network call, not cleaned up after a write.
    assert downloader.calls == []
    assert upload.calls == []
    assert "outside" in queue.get(res.job_id).error


def test_the_containment_check_allows_an_ordinary_name(tmp_path):
    job_dir = tmp_path / "downloads" / "7"
    assert dest_in_job_dir(job_dir, "g.zip") == job_dir / "g.zip"


@pytest.mark.parametrize("evil", ["C:evil.zip", "..", "../e.zip", "/etc/passwd"])
def test_the_containment_check_refuses_an_escaping_name(tmp_path, evil):
    job_dir = tmp_path / "downloads" / "7"
    with pytest.raises(Exception, match="outside"):
        dest_in_job_dir(job_dir, evil)


# -- the downloader's allowlist guard --------------------------------------


def _redirecting_transport(seen, target):
    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "allowed.example":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=b"payload from the redirect target")

    return httpx.MockTransport(handler)


def test_a_redirect_to_a_disallowed_host_is_refused_and_never_requested(tmp_path):
    """check_url validates a URL *string*, not where that string leads. An
    allowed host that 302s to an undeclared one would otherwise walk the
    host straight out of the allowlist -- so every hop is re-checked
    before it is followed, and httpx is told never to follow one itself."""
    seen: list[str] = []
    dest = tmp_path / "g.zip"

    with HttpDownloader(
        allowlist=["allowed.example"],
        transport=_redirecting_transport(seen, "https://evil.example/g.zip"),
    ) as downloader:
        with pytest.raises(DownloadError, match="evil.example"):
            downloader.download("https://allowed.example/g.zip", dest)

    assert seen == ["https://allowed.example/g.zip"]
    assert not any("evil.example" in url for url in seen)
    assert not dest.exists()


def test_a_redirect_that_stays_inside_the_allowlist_is_followed(tmp_path):
    """Archive.org really does 302 from archive.org to iaNNNN.us.archive.org,
    so refusing every redirect outright would break real imports. The gate
    is the allowlist, not the redirect."""
    seen: list[str] = []
    dest = tmp_path / "g.zip"

    with HttpDownloader(
        allowlist=["allowed.example", "*.allowed.example"],
        transport=_redirecting_transport(seen, "https://cdn.allowed.example/g.zip"),
    ) as downloader:
        downloader.download("https://allowed.example/g.zip", dest)

    assert seen == [
        "https://allowed.example/g.zip",
        "https://cdn.allowed.example/g.zip",
    ]
    assert dest.read_bytes() == b"payload from the redirect target"


def test_a_plugin_supplied_redirect_out_of_the_allowlist_fails_the_import(
    tmp_path, queue, upload
):
    """The same guard, reached the way a hostile plugin would reach it:
    through run_import, with a plan whose URL is on an allowed host."""
    seen: list[str] = []
    with HttpDownloader(
        allowlist=["allowed.example"],
        transport=_redirecting_transport(seen, "https://evil.example/g.zip"),
    ) as downloader:
        res = _run(tmp_path, FakePlugin(_plan()), FakeRomm(), queue, downloader)

    assert res.state is JobState.FAILED
    assert "evil.example" in res.message
    assert not any("evil.example" in url for url in seen)
    assert upload.calls == []


def test_a_redirect_loop_is_bounded(tmp_path):
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://allowed.example/x"})

    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as downloader:
        with pytest.raises(DownloadError, match="redirect"):
            downloader.download("https://allowed.example/g.zip", tmp_path / "g.zip")

    assert len(seen) <= 6


def test_a_plain_http_url_is_refused(tmp_path):
    """ALLOWED_SCHEMES is https-only; the downloader must not be the one
    component that quietly accepts cleartext."""
    with HttpDownloader(allowlist=["allowed.example"]) as downloader:
        with pytest.raises(DownloadError):
            downloader.download("http://allowed.example/g.zip", tmp_path / "g.zip")


# -- resumable downloads ----------------------------------------------------


def test_a_partial_file_is_resumed_with_a_range_request(tmp_path):
    body = bytes(range(256)) * 4
    dest = tmp_path / "g.bin"
    dest.write_bytes(body[:400])
    seen = {}

    def handler(request):
        seen["range"] = request.headers.get("range")
        return httpx.Response(
            206,
            content=body[400:],
            headers={"content-range": f"bytes 400-{len(body) - 1}/{len(body)}"},
        )

    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as downloader:
        downloader.download("https://allowed.example/g.bin", dest)

    assert seen["range"] == "bytes=400-"
    assert dest.read_bytes() == body


def test_a_server_ignoring_the_range_restarts_instead_of_appending(tmp_path):
    """A 200 answer to a Range request means the whole body is coming.
    Appending it to the partial would silently produce a corrupt file that
    still hashes and still uploads."""
    body = b"complete body, all of it"
    dest = tmp_path / "g.bin"
    dest.write_bytes(b"stale partial")

    def handler(request):
        return httpx.Response(200, content=body)

    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as downloader:
        downloader.download("https://allowed.example/g.bin", dest)

    assert dest.read_bytes() == body


def test_an_already_complete_file_is_not_downloaded_again(tmp_path):
    body = b"already here"
    dest = tmp_path / "g.bin"
    dest.write_bytes(body)
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=b"should not have been fetched")

    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as downloader:
        downloader.download(
            "https://allowed.example/g.bin", dest, expected_size=len(body)
        )

    assert seen == []
    assert dest.read_bytes() == body


def test_a_non_2xx_download_raises_download_error_naming_the_status(tmp_path):
    def handler(request):
        return httpx.Response(404, text="no such item")

    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as downloader:
        with pytest.raises(DownloadError, match="404"):
            downloader.download("https://allowed.example/g.bin", tmp_path / "g.bin")


# -- importing something that cannot be played ----------------------------
#
# The pipeline warns and imports. It never refuses: cataloguing a platform
# with no emulator core is a legitimate use of a library, and several
# plugins in the directory exist to do only that. What it must not do is
# stay quiet, because a ROM that imports cleanly and then does nothing when
# clicked explains itself nowhere else.


def _dead_plan():
    """A plan filed under Dreamcast, which RomM has no EmulatorJS core for."""
    return FetchPlan(
        files=[FetchFile(url="https://allowed.example/g.zip", filename="g.zip")],
        platform="dc",
    )


def test_a_platform_with_no_core_warns_and_still_imports(tmp_path, queue, upload):
    romm = FakeRomm(platforms={"dc": 7})
    res = _run(tmp_path, FakePlugin(_dead_plan()), romm, queue)

    assert res.state is JobState.DONE, "the warning must not become a refusal"
    assert res.rom_id == 999
    assert len(res.warnings) == 1
    assert "'dc'" in res.warnings[0]
    assert "no EmulatorJS core" in res.warnings[0]
    assert len(upload.calls) == 1


def test_a_playable_platform_warns_about_nothing(tmp_path, queue, upload):
    res = _run(tmp_path, FakePlugin(_plan()), FakeRomm(), queue)
    assert res.warnings == ()


def test_the_warning_reaches_the_job_row_not_only_the_return_value(
    tmp_path, queue, upload
):
    """`rom-hub jobs` is where an operator looks weeks later, by which
    point the console line is long gone."""
    romm = FakeRomm(platforms={"dc": 7})
    res = _run(tmp_path, FakePlugin(_dead_plan()), romm, queue)
    assert "'dc'" in (queue.get(res.job_id).notes or "")


def test_the_warning_is_on_the_row_even_when_the_job_later_fails(
    tmp_path, queue, upload
):
    """The platform was unplayable regardless of what the download did."""
    romm = FakeRomm(platforms={"dc": 7})
    res = _run(
        tmp_path,
        FakePlugin(_dead_plan()),
        romm,
        queue,
        downloader=FakeDownloader(error=DownloadError("network is down")),
    )
    assert res.state is JobState.FAILED
    assert "'dc'" in (queue.get(res.job_id).notes or "")


def test_allow_unplayable_silences_the_warning_and_changes_nothing_else(
    tmp_path, queue, upload
):
    romm = FakeRomm(platforms={"dc": 7})
    res = _run(
        tmp_path, FakePlugin(_dead_plan()), romm, queue, warn_unplayable=False
    )
    assert res.warnings == ()
    assert res.state is JobState.DONE
    assert len(upload.calls) == 1


def test_the_warning_is_raised_before_a_single_byte_is_fetched(tmp_path, queue, upload):
    """The point of warning early is a 700 MB Dreamcast rip nobody wanted.

    Proved by failing the download: the note is on the row even though no
    byte ever arrived, which can only be true if it was written first.
    """
    romm = FakeRomm(platforms={"dc": 7})
    downloader = FakeDownloader(error=DownloadError("refused"))
    res = _run(tmp_path, FakePlugin(_dead_plan()), romm, queue, downloader)
    assert queue.get(res.job_id).notes
    assert downloader.calls, "the download was still attempted, just after the notice"


def test_a_netplay_only_platform_warns_about_netplay_rather_than_absence(
    tmp_path, queue, upload
):
    """`3ds` has a core, but only in RomM's nightly build and only when
    netplay is on. Reporting it as "no core" would send an operator
    looking for something that is there."""
    romm = FakeRomm(platforms={"3ds": 7})
    plan = FetchPlan(
        files=[FetchFile(url="https://allowed.example/g.zip", filename="g.zip")],
        platform="3ds",
    )
    res = _run(tmp_path, FakePlugin(plan), romm, queue)
    assert len(res.warnings) == 1
    assert "netplay" in res.warnings[0]
    assert res.state is JobState.DONE


def test_a_degraded_collection_and_an_unplayable_platform_both_survive(
    tmp_path, queue, upload
):
    """`set_notes` overwrites the column and there are now two writers.

    Before the notes were accumulated, whichever fired second erased the
    other and the job row told half the story.
    """
    romm = FakeRomm(platforms={"dc": 7}, capabilities=(IMPORT, SCAN))
    plan = FetchPlan(
        files=[FetchFile(url="https://allowed.example/g.zip", filename="g.zip")],
        platform="dc",
        collection="Archive.org",
    )
    res = _run(tmp_path, FakePlugin(plan), romm, queue)

    notes = queue.get(res.job_id).notes or ""
    assert "collection" in notes
    assert "'dc'" in notes
    # And the two stay in their own channels: a skipped step is a shortfall
    # in the run, an unplayable platform is a fact about the result.
    assert len(res.degraded) == 1
    assert len(res.warnings) == 1


def test_an_unplayable_platform_is_reported_even_when_the_import_fails(
    tmp_path, queue, upload
):
    """The commonest way to meet this warning is a platform the library has
    no shelf for at all, which fails at `platform_id` two steps later. If
    the notice only survived a success, the operator would be told the slug
    is unknown and never told it is also unplayable."""
    romm = FakeRomm(platforms={})
    res = _run(tmp_path, FakePlugin(_dead_plan()), romm, queue)

    assert res.state is JobState.FAILED
    assert len(res.warnings) == 1
    assert "'dc'" in res.warnings[0]
