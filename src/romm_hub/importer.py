"""The import pipeline: everything Phase 2 built, chained into one run.

    plugin.plan() -> platform id -> download -> hash/dedup -> upload
    -> confirm by hash -> collection -> DONE

The plugin's only move is describing what it wants fetched. The host
resolves the platform, opens the sockets, hashes the bytes, and holds the
RomM token. That split is the whole design, and this module is where it
is actually spent.

Two things here are load-bearing beyond "it works":

**The allowlist survives redirects.** `netpolicy.check_url` validates a
URL *string*. It says nothing about where that string ultimately leads,
so an allowed host answering `302 Location: https://evil.example/...`
would walk the host straight out of the allowlist that `PluginProcess.
plan()` just enforced. `HttpDownloader` therefore tells httpx to follow
nothing (`follow_redirects=False`, the same defence `broker/fetcher.py`
uses for `ctx.http`) and re-runs `check_url` on each hop itself before
issuing the next request. Redirects are still *followed* -- Archive.org
genuinely 302s from `archive.org` to `iaNNNN.us.archive.org`, and
refusing outright would break real imports -- but only to hosts the
plugin declared.

**An upload is not confirmed by its status code.** RomM's
`/api/roms/upload/{id}/complete` answers a bare `201` with no body -- it
carries no rom id, and accepting the request is not the same as the ROM
appearing in the library. So after uploading, the pipeline re-lists the
platform and locates each file by the digest it already computed for
dedup. That yields the rom ids the collection step needs *and* proves the
ROM landed; a file that is absent from the library after a "successful"
upload fails the job rather than reporting DONE on faith.

**Nothing escapes as a traceback.** Every failure lands the job in
`FAILED` with a sentence an operator can act on, because the job record
is where they will look, not the console the exception would have
printed to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol
from urllib.parse import urljoin

import httpx

from romm_hub.dedup import FileHashes, find_duplicate, hash_file
from romm_hub.jobs import Job, JobQueue, JobState
from romm_hub.netpolicy import PolicyViolation, check_url
from romm_hub.romm.client import RommClient
from romm_hub.romm.upload import upload_file
from romm_hub.types import FetchPlan, SearchResult

USER_AGENT = "romm-hub/0.1 (+https://github.com/rommapp/romm)"

# Redirect chains inside one allowlist are legitimate; unbounded ones are a
# loop or a tarpit, and either way the download is not going to happen.
MAX_REDIRECTS = 5

# Downloads are multi-GB by nature. Stream them; never read one whole.
STREAM_CHUNK_BYTES = 1024 * 1024

DOWNLOAD_TIMEOUT = 300.0

# `jobs.set_state()` treats `error=None` as "leave the column alone", which
# is correct for the mid-pipeline transitions that have nothing to say about
# errors -- but it means a job that failed, was retried, and succeeded would
# display DONE next to the previous attempt's error text forever. run_import
# therefore writes an explicit empty string exactly once, when an attempt
# begins: from that point the column describes *this* attempt, and an attempt
# that fails for a new reason still overwrites it with the new one.
_ERROR_CLEARED = ""


class DownloadError(Exception):
    """A file could not be fetched: transport, status, or policy."""


class Downloader(Protocol):
    def download(
        self, url: str, dest: Path, expected_size: int | None = None
    ) -> Path: ...


class HttpDownloader:
    """Streams a plugin-planned URL to disk, resumably, without ever
    leaving the plugin's declared allowlist. See the module docstring for
    why the redirect handling is manual."""

    def __init__(
        self,
        allowlist: list[str],
        timeout: float = DOWNLOAD_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        max_redirects: int = MAX_REDIRECTS,
    ):
        self.allowlist = list(allowlist)
        self.max_redirects = max_redirects
        self._client = httpx.Client(
            timeout=timeout,
            # httpx must never follow a redirect on its own: by the time it
            # did, the request to the undeclared host would already be out.
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpDownloader":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def download(
        self, url: str, dest: Path, expected_size: int | None = None
    ) -> Path:
        """Fetch `url` into `dest`, resuming a partial file if one is there.

        `expected_size` (from `FetchFile.size_bytes`, when the plugin
        offered one) lets an already-complete file be recognised without
        a request at all -- the cheapest possible resume.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        offset = dest.stat().st_size if dest.exists() else 0

        if expected_size is not None and offset:
            if offset == expected_size:
                return dest
            if offset > expected_size:
                # Longer than the thing we are fetching: not a prefix of it.
                dest.unlink()
                offset = 0

        try:
            self._stream_to(url, dest, offset)
        except PolicyViolation as exc:
            raise DownloadError(
                f"refusing to download from a host outside the plugin's "
                f"allowlist: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise DownloadError(f"downloading {url!r} failed: {exc}") from exc
        return dest

    def _stream_to(self, url: str, dest: Path, offset: int) -> None:
        current = url
        hops = 0
        restarted = False

        while True:
            # The enforcement point, re-entered for every hop. Nothing below
            # this line runs for a URL the plugin never declared.
            check_url(current, self.allowlist)

            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with self._client.stream("GET", current, headers=headers) as resp:
                if resp.is_redirect:
                    hops += 1
                    if hops > self.max_redirects:
                        raise DownloadError(
                            f"downloading {url!r} gave up after "
                            f"{self.max_redirects} redirects (last: {current!r})"
                        )
                    current = self._redirect_target(resp, current)
                    continue

                if resp.status_code == 416 and offset and not restarted:
                    # The partial on disk is at or past the end of what the
                    # server has. Treat it as junk and fetch the lot.
                    restarted = True
                    offset = 0
                    continue

                if resp.status_code == 206:
                    mode = "ab"
                elif resp.status_code == 200:
                    # The server ignored our Range and is sending the whole
                    # body. Appending it to the partial would produce a
                    # corrupt file that still hashes and still uploads.
                    mode = "wb"
                else:
                    raise DownloadError(
                        f"downloading {current!r} failed with HTTP "
                        f"{resp.status_code}"
                    )

                with dest.open(mode) as fh:
                    for chunk in resp.iter_bytes(STREAM_CHUNK_BYTES):
                        fh.write(chunk)
            return

    @staticmethod
    def _redirect_target(resp: httpx.Response, current: str) -> str:
        # next_request is httpx's own resolution of a relative Location
        # against the request URL; it is populated even when the client is
        # told not to follow. urljoin is the fallback, not the primary.
        if resp.next_request is not None:
            return str(resp.next_request.url)
        return urljoin(current, resp.headers.get("location", ""))


@dataclass
class ImportResult:
    job_id: int
    state: JobState
    rom_id: int | None
    message: str


class _ImportFailure(Exception):
    """Internal: a step failed with a message already fit for an operator."""


def dest_in_job_dir(job_dir: Path, filename: str) -> Path:
    """Join `filename` onto `job_dir`, refusing anything that lands outside.

    `FetchFile`'s validator is supposed to make this unreachable, and it is
    the layer that gives a plugin author a legible error. This is the layer
    that has to hold when that one has a gap -- a validator bug must not be
    able to become a filesystem write. It is deliberately kept even though
    nothing should reach it: the previous validator looked complete too,
    and `job_dir / "C:evil.zip"` still wrote outside the job directory.

    Both path flavours are consulted so the answer does not depend on the
    host OS: `"C:evil.zip"` is one bare name to POSIX and a drive-relative
    path to Windows, and it must be refused either way.
    """
    job_dir = Path(job_dir)
    if PureWindowsPath(filename).parts != (filename,) or PurePosixPath(
        filename
    ).parts != (filename,):
        raise _ImportFailure(
            f"refusing to write {filename!r}: it is not a bare name, so it "
            f"could land outside the job directory {str(job_dir)!r}"
        )

    dest = job_dir / filename
    try:
        # resolve() also collapses "..", and follows any symlink already
        # standing in the job directory.
        root = job_dir.resolve()
        resolved = dest.resolve()
    except (OSError, ValueError) as exc:
        # ValueError is what an embedded NUL byte raises here.
        raise _ImportFailure(
            f"refusing to write {filename!r} outside the job directory "
            f"{str(job_dir)!r} -- the name could not be resolved at all "
            f"({type(exc).__name__}: {exc})"
        ) from exc

    if resolved.parent != root:
        raise _ImportFailure(
            f"refusing to write {filename!r} outside the job directory "
            f"{str(job_dir)!r} -- it would land at {str(resolved)!r}"
        )
    return dest


def run_import(
    plugin,
    result: SearchResult,
    *,
    romm: RommClient,
    queue: JobQueue,
    download_dir: Path,
    downloader: Downloader | None = None,
    job_id: int | None = None,
) -> ImportResult:
    """Import one search result into RomM, recording progress in `queue`.

    `plugin` is a started `PluginProcess` (anything with `.plan()` and a
    `.manifest`). Pass `job_id` to re-run an existing job rather than
    enqueueing a new one: that is what a retry is, and reusing the id is
    what lets the partially downloaded bytes under
    `download_dir/<job_id>/` be resumed instead of re-fetched.

    Never raises for an import that failed -- the failure is the return
    value and the job record. Only a caller error (an unknown `job_id`)
    raises.
    """
    download_dir = Path(download_dir)
    manifest = getattr(plugin, "manifest", None)
    slug = _slug_of(plugin)

    if downloader is None:
        downloader = HttpDownloader(allowlist=list(getattr(manifest, "network", [])))

    if job_id is None:
        job = queue.enqueue(
            plugin=slug,
            source_id=result.source_id,
            title=result.title,
            platform=result.platform or "",
        )
    else:
        job = queue.get(job_id)
        if job is None:
            raise ValueError(f"no such job: {job_id}")

    # This attempt owns the error column from here on. See _ERROR_CLEARED.
    queue.set_state(job.id, JobState.DOWNLOADING, error=_ERROR_CLEARED)

    try:
        return _import(
            plugin,
            result,
            romm=romm,
            queue=queue,
            job=job,
            download_dir=download_dir,
            downloader=downloader,
        )
    except _ImportFailure as exc:
        return _fail(queue, job.id, str(exc))
    except Exception as exc:  # noqa: BLE001
        # A step nobody anticipated still failed an import, and the operator
        # needs it in the job record rather than on a console they are not
        # watching. The type name is kept because an unexpected failure's
        # class is usually the most informative thing about it.
        return _fail(
            queue, job.id, f"unexpected {type(exc).__name__} during import: {exc}"
        )


def _slug_of(plugin) -> str:
    return getattr(getattr(plugin, "manifest", None), "slug", "") or "unknown"


def _fail(queue: JobQueue, job_id: int, message: str) -> ImportResult:
    queue.set_state(job_id, JobState.FAILED, error=message)
    return ImportResult(
        job_id=job_id, state=JobState.FAILED, rom_id=None, message=message
    )


def _import(
    plugin,
    result: SearchResult,
    *,
    romm: RommClient,
    queue: JobQueue,
    job: Job,
    download_dir: Path,
    downloader: Downloader,
) -> ImportResult:
    slug = _slug_of(plugin)

    # 1. Ask the plugin what to fetch. PluginProcess.plan() has already
    #    validated the shape and gated every URL against the allowlist.
    try:
        plan: FetchPlan = plugin.plan(result)
    except Exception as exc:
        raise _ImportFailure(
            f"plugin {slug!r} could not plan an import for "
            f"{result.source_id!r}: {exc}"
        ) from exc

    # 2. Slug -> integer platform id. Never guess: a wrong id files the ROM
    #    under the wrong system, which is worse than a visible failure.
    try:
        platform_id = romm.platform_id(plan.platform)
    except Exception as exc:
        raise _ImportFailure(
            f"could not resolve platform {plan.platform!r} in RomM: {exc}"
        ) from exc
    queue.set_platform(job.id, plan.platform)

    # 3. Download. Recorded before the first byte, so a failed attempt still
    #    tells a retry where its partial bytes are.
    job_dir = download_dir / str(job.id)
    queue.set_state(job.id, JobState.DOWNLOADING, local_path=str(job_dir))
    paths: list[Path] = []
    for entry in plan.files:
        dest = dest_in_job_dir(job_dir, entry.filename)
        try:
            downloader.download(entry.url, dest, expected_size=entry.size_bytes)
        except Exception as exc:
            raise _ImportFailure(
                f"download of {entry.filename!r} from {entry.url!r} failed: {exc}"
            ) from exc
        paths.append(dest)

    # 4. Dedup. A dedup that could not run is not a dedup that passed --
    #    uploading anyway would put a duplicate in the library on every
    #    transient 5xx from /api/roms.
    try:
        existing_roms = romm.list_roms(platform_id)
    except Exception as exc:
        raise _ImportFailure(
            f"could not list existing roms for platform {plan.platform!r}, so "
            f"the import was stopped rather than risk a duplicate: {exc}"
        ) from exc

    to_upload: list[Path] = []
    duplicates: list[tuple[Path, dict]] = []
    # Kept: these same digests identify the ROMs again after the upload,
    # so the file is never hashed twice.
    hashes_by_path: dict[Path, FileHashes] = {}
    for path in paths:
        try:
            hashes = hash_file(path)
        except OSError as exc:
            raise _ImportFailure(f"could not hash {path.name!r}: {exc}") from exc
        hashes_by_path[path] = hashes
        match = find_duplicate(hashes, existing_roms)
        if match is None:
            to_upload.append(path)
        else:
            duplicates.append((path, match))

    if not to_upload:
        names = ", ".join(path.name for path, _ in duplicates)
        existing_id = _rom_id_of(duplicates[0][1]) if duplicates else None
        message = (
            f"already in RomM ({names}); nothing was uploaded"
            + (f" -- matches rom id {existing_id}" if existing_id is not None else "")
        )
        queue.set_state(job.id, JobState.SKIPPED_DUPLICATE)
        return ImportResult(
            job_id=job.id,
            state=JobState.SKIPPED_DUPLICATE,
            rom_id=existing_id,
            message=message,
        )

    # 5. Upload. The return value is deliberately discarded: RomM's
    #    /complete answers a bare 201 with no body, so there is nothing in
    #    it to read -- not a rom id, not anything.
    queue.set_state(job.id, JobState.UPLOADING)
    for path in to_upload:
        try:
            upload_file(romm, path, platform_id)
        except Exception as exc:
            raise _ImportFailure(
                f"upload of {path.name!r} to RomM failed: {exc}"
            ) from exc

    # 5b. Find what was just uploaded, by the digests already computed in
    #     step 4. This does two jobs at once:
    #
    #       * It is the only way to learn the new rom ids, since /complete
    #         carries none. Step 6 needs them.
    #       * It is a real post-condition. A 201 says the server accepted
    #         the request; finding our own hash in the library says the ROM
    #         actually landed. Reporting DONE on the strength of a status
    #         code alone would call a silently-dropped upload a success.
    #
    #     One listing for the whole plan, matched against every file --
    #     never one call per file, because real libraries hold thousands of
    #     roms and this runs on every import.
    try:
        library = romm.list_roms(platform_id)
    except Exception as exc:
        raise _ImportFailure(
            f"the upload of {len(to_upload)} file(s) reported success, but "
            f"confirming it in the library failed: {exc}"
        ) from exc

    rom_ids: list[int] = []
    missing: list[str] = []
    for path in to_upload:
        match = find_duplicate(hashes_by_path[path], library)
        if match is None:
            missing.append(path.name)
            continue
        rom_id = _rom_id_of(match)
        if rom_id is not None:
            rom_ids.append(rom_id)

    if missing:
        raise _ImportFailure(
            f"RomM reported the upload of {', '.join(missing)} succeeded, but "
            f"the file did not appear in the library for platform "
            f"{plan.platform!r} afterwards -- the ROM did not land, so the "
            f"import is not done. Check RomM's own logs for the upload."
        )

    # 6. Collection, only if the plan asked for one. After 5b, because the
    #    rom ids it needs only exist once that lookup has run.
    if plan.collection:
        if not rom_ids:
            raise _ImportFailure(
                f"uploaded {len(to_upload)} file(s), but RomM's upload response "
                f"carried no rom id, so they could not be added to collection "
                f"{plan.collection!r}; add them by hand"
            )
        try:
            collection_id = romm.ensure_collection(plan.collection)
            romm.add_to_collection(collection_id, rom_ids)
        except Exception as exc:
            raise _ImportFailure(
                f"the upload succeeded, but adding it to collection "
                f"{plan.collection!r} failed: {exc}"
            ) from exc

    # 7. Done.
    skipped = f", {len(duplicates)} already present" if duplicates else ""
    message = f"imported {len(to_upload)} file(s) as rom id(s) {rom_ids}{skipped}"
    queue.set_state(job.id, JobState.DONE)
    return ImportResult(
        job_id=job.id,
        state=JobState.DONE,
        rom_id=rom_ids[0] if rom_ids else None,
        message=message,
    )


def _rom_id_of(rom: dict) -> int | None:
    """The integer id of a rom from a `GET /api/roms` listing.

    Never from an upload response: `/complete` answers 201 with no body,
    so there is no id there to read.
    """
    if not isinstance(rom, dict):
        return None
    value = rom.get("id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
