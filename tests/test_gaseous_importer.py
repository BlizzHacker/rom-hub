"""`run_import` end to end against a (mocked) Gaseous, through the real backend.

This is the file that actually tests the claim the seam was extracted to
make. `test_importer.py` drives the pipeline against a hand-written
`FakeRomm`; here the pipeline drives the **real** `GaseousBackend`, and
the only fake is the HTTP server underneath it -- a stateful
`httpx.MockTransport` that models Gaseous' asynchronous import the way
the real one behaves: an upload is invisible until the background queue
has processed it.

No test here may require a live Gaseous.
"""

import hashlib
import zlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from rom_hub.backends.gaseous import GaseousBackend
from rom_hub.backends.gaseous.imports import ImportWaiter
from rom_hub.jobs import JobQueue, JobState
from rom_hub.types import FetchFile, FetchPlan, SearchResult
from rom_hub.importer import run_import

API = "/api/v1.1"
RESULT = SearchResult(source_id="rubik_202308", title="Rubik")
ROM_BYTES = b"MZ\x90\x00rubik payload" * 64

PLATFORMS = [{"sourceType": "None", "id": 13, "name": "DOS", "slug": "dos"}]


class FakeGaseous:
    """A stateful stand-in for gaseous-server, at the HTTP level.

    Models the three behaviours that make Gaseous different from RomM and
    that the backend exists to absorb:

    * an upload answers a bare session GUID and puts the file in a queue;
    * the rom is not listable until `run_queue()` has been called, which
      is what `scan_platform` waits for;
    * the platform a rom lands on is the server's choice, not the
      caller's -- an unrecognised file goes to platform 0 no matter what
      `OverridePlatformId` said.
    """

    def __init__(self, identifies_platform=False):
        self.roms: list[dict] = []
        self.queue: list[dict] = []
        self.uploads: list[str] = []
        self.calls: list[httpx.Request] = []
        self._next_rom_id = 1
        self._next_map_id = 1
        self._identifies = identifies_platform

    # -- the background import queue ----------------------------------

    def run_queue(self) -> None:
        """What ImportQueueProcessor does: hash, file, and register."""
        for item in self.queue:
            if item["state"] == "Completed":
                continue
            data = item["data"]
            # Gaseous hashes the raw file (Classes/HashObject.cs), never
            # the members of an archive.
            self.roms.append(
                {
                    "id": self._next_rom_id,
                    "metadataMapId": self._next_map_id,
                    "platformId": 13 if self._identifies else 0,
                    "name": item["filename"],
                    "size": len(data),
                    "crc": format(zlib.crc32(data) & 0xFFFFFFFF, "08x"),
                    "md5": hashlib.md5(data).hexdigest(),
                    "sha1": hashlib.sha1(data).hexdigest(),
                }
            )
            self._next_rom_id += 1
            self._next_map_id += 1
            item["state"] = "Completed"

    # -- transport ----------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path

        if path == f"{API}/Account/Login":
            return httpx.Response(200, json={"success": True})

        if path == f"{API}/Platforms":
            return httpx.Response(200, json=PLATFORMS)

        if path == f"{API}/Games" and request.method == "POST":
            page = int(request.url.params.get("pageNumber", 1))
            size = int(request.url.params.get("pageSize", 200))
            games = [
                {"metadataMapId": r["metadataMapId"],
                 "platformIds": [r["platformId"]]}
                for r in self.roms
            ]
            return httpx.Response(200, json={"games": games[(page - 1) * size : page * size]})

        if path.startswith(f"{API}/Games/") and path.endswith("/roms"):
            map_id = int(path.split("/")[-2])
            platform_id = int(request.url.params.get("PlatformId", -1))
            found = [
                r
                for r in self.roms
                if r["metadataMapId"] == map_id and r["platformId"] == platform_id
            ]
            if not found:
                return httpx.Response(404, json={"status": 404})
            return httpx.Response(200, json={"gameRomItems": found, "count": len(found)})

        if path == f"{API}/Roms" and request.method == "POST":
            session = f"session-{len(self.queue) + 1}"
            filename, data = _multipart_file(request.content)
            self.queue.append(
                {"sessionId": session, "state": "Pending",
                 "filename": filename, "data": data}
            )
            self.uploads.append(filename)
            return httpx.Response(200, text=session)

        if path == f"{API}/Roms/Imports":
            return httpx.Response(
                200,
                json=[
                    {"sessionId": q["sessionId"], "state": q["state"],
                     "fileName": q["filename"]}
                    for q in self.queue
                ],
            )

        return httpx.Response(404, json={"detail": f"unhandled {path}"})

    def backend(self) -> GaseousBackend:
        """The real backend, wired to this fake server.

        The waiter is given a sleep that runs the queue, which is how a
        test models "the background task got round to it" without any
        real time passing.
        """
        client_backend = GaseousBackend(
            "https://gaseous.example",
            "romhub@example.com",
            "pw",
            transport=httpx.MockTransport(self.handler),
        )
        client_backend._waiter = ImportWaiter(
            client_backend.client,
            poll_interval=0.0,
            sleep=lambda _: self.run_queue(),
            monotonic=lambda: 0.0,
        )
        return client_backend


def _multipart_file(body: bytes) -> tuple[str, bytes]:
    """filename and payload out of a multipart body, crudely but enough."""
    marker = b'filename="'
    start = body.index(marker) + len(marker)
    filename = body[start : body.index(b'"', start)].decode()
    head = body.index(b"\r\n\r\n", start) + 4
    tail = body.rindex(b"\r\n--")
    return filename, body[head:tail]


class FakePlugin:
    def __init__(self, plan):
        self.manifest = SimpleNamespace(slug="archive-org", network=["allowed.example"])
        self._plan = plan

    def plan(self, result):
        return self._plan


class FakeDownloader:
    """Writes the bytes a real download would have produced."""

    def __init__(self, payload=ROM_BYTES):
        self.payload = payload
        self.downloads = []

    def download(self, url, dest, expected_size=None):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        self.downloads.append(url)
        return dest


def _plan(filename="rubik.img", collection=None):
    return FetchPlan(
        files=[FetchFile(url=f"https://allowed.example/{filename}", filename=filename)],
        platform="dos",
        collection=collection,
    )


@pytest.fixture
def queue(tmp_path):
    with JobQueue(tmp_path / "var" / "jobs.db") as q:
        yield q


def _run(tmp_path, backend, queue, plan=None, downloader=None, **kwargs):
    return run_import(
        FakePlugin(plan or _plan()),
        RESULT,
        backend=backend,
        queue=queue,
        download_dir=tmp_path / "downloads",
        downloader=downloader or FakeDownloader(),
        **kwargs,
    )


# -- the pipeline ----------------------------------------------------------


def test_an_import_reaches_done_through_the_real_backend(tmp_path, queue):
    server = FakeGaseous()
    res = _run(tmp_path, server.backend(), queue)

    assert res.state is JobState.DONE
    assert res.rom_id == 1
    assert server.uploads == ["rubik.img"]
    assert len(server.roms) == 1


def test_the_scan_step_is_what_makes_the_rom_visible(tmp_path, queue):
    """Without the wait, run_import would list the library before
    ImportQueueProcessor had created the row and correctly report that the
    ROM never landed. This asserts the ordering, not just the outcome."""
    server = FakeGaseous()
    backend = server.backend()

    # Before the pipeline runs, an upload alone registers nothing.
    backend.upload_rom(_write(tmp_path, "solo.img"), 13)
    assert backend.list_roms(13) == []

    server.run_queue()
    assert len(backend.list_roms(13)) == 1


def test_a_second_import_of_the_same_rom_is_skipped_as_a_duplicate(tmp_path, queue):
    server = FakeGaseous()
    backend = server.backend()

    first = _run(tmp_path, backend, queue)
    assert first.state is JobState.DONE

    second = _run(tmp_path, backend, queue)
    assert second.state is JobState.SKIPPED_DUPLICATE
    assert second.rom_id == 1
    # The decisive assertion: nothing was uploaded the second time.
    assert server.uploads == ["rubik.img"]


def test_dedup_survives_the_platform_gaseous_actually_chose(tmp_path, queue):
    """The ROM was uploaded as DOS (13) and Gaseous filed it under 0. A
    listing scoped strictly to 13 would miss it and the re-import would
    upload a second copy."""
    server = FakeGaseous(identifies_platform=False)
    backend = server.backend()

    _run(tmp_path, backend, queue)
    assert server.roms[0]["platformId"] == 0

    second = _run(tmp_path, backend, queue)
    assert second.state is JobState.SKIPPED_DUPLICATE
    assert len(server.roms) == 1


def test_dedup_also_works_when_gaseous_does_identify_the_platform(tmp_path, queue):
    server = FakeGaseous(identifies_platform=True)
    backend = server.backend()

    _run(tmp_path, backend, queue)
    assert server.roms[0]["platformId"] == 13

    second = _run(tmp_path, backend, queue)
    assert second.state is JobState.SKIPPED_DUPLICATE


def test_an_archive_dedups_by_filename_when_the_hashes_cannot_match(tmp_path, queue):
    """`rom_hub.dedup.hash_file` reproduces RomM's digest, which for an
    archive is taken over the decompressed members; Gaseous hashes the raw
    file. For a .zip the two disagree by construction, so hash dedup
    cannot match and the filename check is what catches the duplicate.

    This is the documented limitation, pinned so it stays a limitation
    rather than becoming a second copy in the user's library.
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("RUBIK.EXE", b"payload" * 100)
    payload = buffer.getvalue()

    server = FakeGaseous()
    backend = server.backend()
    plan = _plan("rubik.zip")

    first = _run(tmp_path, backend, queue, plan=plan,
                 downloader=FakeDownloader(payload))
    assert first.state is JobState.DONE

    # The hashes really do differ -- otherwise this test proves nothing.
    from rom_hub.dedup import hash_file

    stored = server.roms[0]
    computed = hash_file(tmp_path / "downloads" / str(first.job_id) / "rubik.zip")
    assert computed.sha1 != stored["sha1"]

    second = _run(tmp_path, backend, queue, plan=plan,
                  downloader=FakeDownloader(payload))
    assert second.state is JobState.SKIPPED_DUPLICATE
    assert len(server.roms) == 1


def test_a_collection_is_refused_before_anything_is_downloaded(tmp_path, queue):
    """Gaseous has no collections, and the pipeline must say so before it
    spends a download rather than after the upload."""
    server = FakeGaseous()
    downloader = FakeDownloader()
    res = _run(
        tmp_path,
        server.backend(),
        queue,
        plan=_plan(collection="Shooters"),
        downloader=downloader,
    )

    assert res.state is JobState.FAILED
    assert "collections" in res.message
    assert "gaseous" in res.message
    assert downloader.downloads == []
    assert server.uploads == []


def _write(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(ROM_BYTES)
    return path
