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

**An optional step the backend cannot do is not a failure.** The
capability check at step 1a asks two questions, not one: can this backend
be imported to at all (if not, refuse before the download), and can it do
the extras this plan asks for (if not, do the import and say what was
skipped). Collections are the extra that exists today: two of the three
shipped backends have no collection concept at all, while the archive-org
plugin names a collection by default with no way to clear one from the
CLI -- so refusing meant those backends could not import from archive-org
at all. Which capability is essential and which is optional is decided in
`rom_hub.backends.base`, next to the capability names and next to the
reasoning, because that is where the next backend author will look.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

import httpx

from rom_hub.backends.base import (
    COLLECTIONS,
    IMPORT,
    CapabilityUnsupported,
    LibraryBackend,
    Scanner,
    SkippedStep,
    degrade,
    require,
)
from rom_hub.dedup import FileHashes, find_by_filename, find_duplicate, hash_file
from rom_hub.jobs import Job, JobQueue, JobState
from rom_hub.netpolicy import PolicyViolation, check_url
from rom_hub.paths import UnsafeDestination, dest_in_job_dir
from rom_hub.playability import import_warning
from rom_hub.types import FetchPlan, SearchResult

USER_AGENT = "rom-hub/0.1 (+https://github.com/rommapp/romm)"

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
        max_bytes: int | None = None,
    ):
        self.allowlist = list(allowlist)
        self.max_redirects = max_redirects
        # `None` for a ROM or a core: those are multi-GB by nature and the
        # operator asked for the specific file. A *data asset* is a
        # different transaction -- the plugin named the URL, the size came
        # from a manifest, and nobody typed either -- so `assets.py`
        # constructs its downloader with a bound. Enforced on the declared
        # length and again while streaming, because the header is a hint.
        self.max_bytes = max_bytes
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

    def _check_budget(self, url: str, size: int) -> None:
        if self.max_bytes is not None and size > self.max_bytes:
            raise DownloadError(
                f"downloading {url!r} would take {size} bytes, over the "
                f"{self.max_bytes}-byte limit for this download"
            )

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

                # Believe Content-Length when it is offered: refusing before
                # a body byte is pulled is strictly cheaper. `written`
                # starts at `offset` because a resumed download's budget is
                # the whole file, not the remainder of it.
                written = offset if mode == "ab" else 0
                declared = resp.headers.get("content-length", "")
                if declared.isdigit():
                    self._check_budget(current, written + int(declared))

                with dest.open(mode) as fh:
                    for chunk in resp.iter_bytes(STREAM_CHUNK_BYTES):
                        written += len(chunk)
                        # Checked before the write, so a server that lied
                        # about its length cannot land more on disk than
                        # the budget allows.
                        self._check_budget(current, written)
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
    #: Optional steps the backend could not perform, skipped rather than
    #: fatal. Already spelled out in `message` -- this is the same thing
    #: structured, for a caller that wants to branch on it rather than
    #: read it. Empty on an import that did everything the plan asked.
    degraded: tuple[SkippedStep, ...] = ()
    #: Things that are true about the *result* rather than about the run.
    #: A `SkippedStep` says part of the job did not happen; one of these
    #: says the whole job happened and the operator still needs to know
    #: something -- today, that the platform it landed on has no emulator
    #: core, so the ROM is in the library and will not start. Kept apart
    #: from `degraded` because merging them would make "the import was
    #: reduced" and "the import was complete and is unplayable" read as
    #: the same event, and only one of them is a shortfall.
    warnings: tuple[str, ...] = ()


class _ImportFailure(Exception):
    """Internal: a step failed with a message already fit for an operator."""


# `dest_in_job_dir` lives in rom_hub.paths now -- `metadata` and `cores`
# hand the host plugin-chosen filenames too, and they must be checked by
# the same code, not by a second copy of it. Re-exported here because it
# is imported from this module by name in several places.
__all__ = ["dest_in_job_dir", "run_import", "HttpDownloader", "ImportResult"]


def run_import(
    plugin,
    result: SearchResult,
    *,
    backend: LibraryBackend,
    queue: JobQueue,
    download_dir: Path,
    downloader: Downloader | None = None,
    scanner: Scanner | None = None,
    job_id: int | None = None,
    warn_unplayable: bool = True,
) -> ImportResult:
    """Import one search result into the library, recording progress in `queue`.

    `plugin` is a started `PluginProcess` (anything with `.plan()` and a
    `.manifest`). `backend` is a `LibraryBackend` -- RomM, or whatever
    else `ROM_HUB_BACKEND` selected; nothing in this function knows which.
    Pass `job_id` to re-run an existing job rather than enqueueing a new
    one: that is what a retry is, and reusing the id is what lets the
    partially downloaded bytes under `download_dir/<job_id>/` be resumed
    instead of re-fetched.

    `warn_unplayable` defaults on, and defaults are the whole point of it:
    an operator who has not thought about emulator cores is exactly the
    one who should be told the ROM they are importing will not start. Set
    it False to import a platform you already know is catalogue-only
    without being told again. It has never gated the import itself.

    Never raises for an import that failed -- the failure is the return
    value and the job record. Only a caller error (an unknown `job_id`)
    raises.
    """
    download_dir = Path(download_dir)
    manifest = getattr(plugin, "manifest", None)
    slug = _slug_of(plugin)

    # Close only what this call opened. A caller-supplied downloader may be
    # reused across imports and is not ours to shut. The CLI supplies none,
    # so it always takes the branch that builds -- and therefore owns -- one.
    owns_downloader = downloader is None
    if owns_downloader:
        downloader = HttpDownloader(allowlist=list(getattr(manifest, "network", [])))

    # The backend registers its own uploads. This opens nothing here --
    # RomM's scanner connects only when scan_platform() is called, which
    # is after a successful upload and therefore never on an import that
    # dedups or fails early. A backend that indexes on receipt implements
    # scan_platform as a no-op; the pipeline does not branch on which.
    if scanner is None:
        scanner = backend

    try:
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
                backend=backend,
                queue=queue,
                job=job,
                download_dir=download_dir,
                downloader=downloader,
                scanner=scanner,
                warn_unplayable=warn_unplayable,
            )
        except _ImportFailure as exc:
            return _fail(queue, job.id, str(exc))
        except Exception as exc:  # noqa: BLE001
            # A step nobody anticipated still failed an import, and the
            # operator needs it in the job record rather than on a console
            # they are not watching. The type name is kept because an
            # unexpected failure's class is usually the most informative
            # thing about it.
            return _fail(
                queue, job.id, f"unexpected {type(exc).__name__} during import: {exc}"
            )
    finally:
        if owns_downloader:
            downloader.close()


def _slug_of(plugin) -> str:
    return getattr(getattr(plugin, "manifest", None), "slug", "") or "unknown"


def _backend_name(backend: LibraryBackend) -> str:
    """What to call the library server in an operator-facing message.

    Falls back to "the library" rather than to a product name: a backend
    without a `name` is a broken backend, and guessing "RomM" for it is
    how these messages came to say the wrong thing in the first place.
    """
    name = getattr(backend, "name", "") or ""
    return name if isinstance(name, str) and name else "the library"


def _fail(queue: JobQueue, job_id: int, message: str) -> ImportResult:
    queue.set_state(job_id, JobState.FAILED, error=message)
    return ImportResult(
        job_id=job_id, state=JobState.FAILED, rom_id=None, message=message
    )


def _import(
    plugin,
    result: SearchResult,
    *,
    backend: LibraryBackend,
    queue: JobQueue,
    job: Job,
    download_dir: Path,
    downloader: Downloader,
    scanner: Scanner,
    warn_unplayable: bool = True,
) -> ImportResult:
    slug = _slug_of(plugin)
    # What to call the library server in anything an operator will read.
    # These messages used to say "RomM" outright, which was true when RomM
    # was the only backend and became a lie the moment it was not: an
    # operator running Gaseous was told a duplicate was "already in RomM",
    # and -- worse -- that a failed registration could be fixed by
    # triggering a scan in a product they do not run.
    library_name = _backend_name(backend)

    # 1. Ask the plugin what to fetch. PluginProcess.plan() has already
    #    validated the shape and gated every URL against the allowlist.
    try:
        plan: FetchPlan = plugin.plan(result)
    except Exception as exc:
        raise _ImportFailure(
            f"plugin {slug!r} could not plan an import for "
            f"{result.source_id!r}: {exc}"
        ) from exc

    # 1a. Can this backend do what the plan asks for -- and what part of it
    #     can it not do?
    #
    #     Asked here, before a single byte is fetched, because the
    #     alternative is discovering it at step 5 or 6: the ROM
    #     downloaded, hashed, uploaded, and then a 404 from an endpoint
    #     the backend never had, leaving a half-filed import and a message
    #     about HTTP.
    #
    #     Two different answers, and the split is `backends.base`'s
    #     essential/optional classification, not a judgement made here:
    #
    #       * IMPORT missing -> refuse. There is no reduced import that
    #         still happens without somewhere to upload to and something
    #         to dedup against.
    #       * COLLECTIONS missing -> proceed, and record the skip. A
    #         collection groups a ROM that is already in the library; the
    #         ROM is what was asked for. This used to refuse, and it is
    #         why `rom-hub import archive-org rubik_202308` downloaded
    #         nothing at all against the backends that have none -- the
    #         archive-org plugin names a collection by default and there
    #         is no CLI flag that clears one.
    #
    #     The pipeline cannot tell a plugin's default collection from one
    #     the operator typed; by the time a FetchPlan exists they look
    #     identical. `_cmd_import` can, and refuses an explicit
    #     `--collection` before this is ever reached.
    #
    #     Converted to an _ImportFailure rather than allowed to propagate:
    #     an unsupported capability is a refusal with a sentence already
    #     written for an operator, and the job record should carry that
    #     sentence, not "unexpected CapabilityUnsupported during import".
    try:
        require(backend, IMPORT, "importing a ROM")
    except CapabilityUnsupported as exc:
        raise _ImportFailure(str(exc)) from exc

    skipped: list[SkippedStep] = []
    warnings: list[str] = []

    # `set_notes` overwrites the column, and there are now two independent
    # reasons to write to it -- a degraded collection and an unplayable
    # platform. Accumulating and rewriting the join is what keeps the
    # second from erasing the first; the alternative was a job row that
    # said only whichever thing happened to be noticed last.
    notes: list[str] = []

    def note(line: str) -> None:
        notes.append(line)
        queue.set_notes(job.id, " | ".join(notes))

    collection_skip: SkippedStep | None = None
    if plan.collection:
        collection_skip = degrade(
            backend, COLLECTIONS, f"adding it to the collection {plan.collection!r}"
        )
        if collection_skip is not None:
            skipped.append(collection_skip)
            # On the row now, not at the end: the note is true from this
            # point regardless of how the job finishes, and a job that
            # later fails on something else should still show it.
            note(str(collection_skip))

    # 1b. Will the thing about to be imported actually play?
    #
    #     Asked here, before a byte is fetched, because the answer is worth
    #     having *early* -- an operator who did not know a Dreamcast rip
    #     cannot be played in RomM's web player would rather find out now
    #     than after a 700 MB download and a click that does nothing.
    #
    #     It does not refuse, and that is the design rather than a
    #     softening of it. A library is not only a player: cataloguing an
    #     Apple II disk, a ScummVM package or an interactive-fiction story
    #     file is a legitimate thing to want, and several plugins in this
    #     directory exist to do exactly that. Refusing would substitute the
    #     host's judgement for the operator's on a question the host cannot
    #     answer. Staying quiet, though, is the failure mode this whole
    #     project is built against -- so it warns, once, naming the
    #     platform, and the ROM lands.
    #
    #     `warn_unplayable=False` is how the CLI's --allow-unplayable
    #     reaches here. It suppresses the sentence, never the import; there
    #     has never been an import for it to enable.
    if warn_unplayable:
        unplayable = import_warning(plan.platform)
        if unplayable:
            warnings.append(unplayable)
            # On the job row now rather than at the end. The note is true
            # from this point regardless of how the job finishes, and a
            # job that later fails on something else should still carry it
            # -- the platform was wrong either way.
            note(unplayable)

    # 2. Slug -> integer platform id. Never guess: a wrong id files the ROM
    #    under the wrong system, which is worse than a visible failure.
    try:
        platform_id = backend.platform_id(plan.platform)
    except Exception as exc:
        raise _ImportFailure(
            f"could not resolve platform {plan.platform!r} in the "
            f"{getattr(backend, 'name', 'active')!r} library: {exc}"
        ) from exc
    queue.set_platform(job.id, plan.platform)

    # 3. List the library once, up front. This happens *before* the
    #    download because it feeds two checks, and the first of them can
    #    make the download unnecessary.
    #
    #    A dedup that could not run is not a dedup that passed -- uploading
    #    anyway would put a duplicate in the library on every transient 5xx
    #    from /api/roms.
    try:
        existing_roms = backend.list_roms(platform_id)
    except Exception as exc:
        raise _ImportFailure(
            f"could not list existing roms for platform {plan.platform!r}, so "
            f"the import was stopped rather than risk a duplicate: {exc}"
        ) from exc

    duplicates: list[tuple[str, dict]] = []

    # 3a. The cheap check: filename on this platform. RomM assembles every
    #     upload to `roms/<platform_fs_slug>/<filename>`, so a name already
    #     present on the same platform is the same ROM -- and this costs no
    #     bytes at all. Re-importing something already in the library is the
    #     common case, and it should not mean downloading it again first.
    wanted = []
    for entry in plan.files:
        match = find_by_filename(entry.filename, existing_roms)
        if match is None:
            wanted.append(entry)
        else:
            duplicates.append((entry.filename, match))

    # 3b. Download whatever survived. Recorded before the first byte, so a
    #     failed attempt still tells a retry where its partial bytes are.
    job_dir = download_dir / str(job.id)
    queue.set_state(job.id, JobState.DOWNLOADING, local_path=str(job_dir))
    paths: list[Path] = []
    for entry in wanted:
        try:
            dest = dest_in_job_dir(job_dir, entry.filename)
        except UnsafeDestination as exc:
            # Same job outcome as before this check moved: FAILED, with the
            # containment message verbatim in the job's error column.
            raise _ImportFailure(str(exc)) from exc
        try:
            downloader.download(entry.url, dest, expected_size=entry.size_bytes)
        except Exception as exc:
            raise _ImportFailure(
                f"download of {entry.filename!r} from {entry.url!r} failed: {exc}"
            ) from exc
        paths.append(dest)

    # 4. The exact check: content hash, against the same listing. Catches
    #    the same ROM stored under a different name, which the filename
    #    check cannot see.
    to_upload: list[Path] = []
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
            duplicates.append((path.name, match))

    if not to_upload:
        names = ", ".join(name for name, _ in duplicates)
        existing_id = _rom_id_of(duplicates[0][1]) if duplicates else None
        message = (
            f"already in {library_name} ({names}); nothing was uploaded"
            + (f" -- matches rom id {existing_id}" if existing_id is not None else "")
            + _skipped_suffix(skipped)
        )
        queue.set_state(job.id, JobState.SKIPPED_DUPLICATE)
        return ImportResult(
            job_id=job.id,
            state=JobState.SKIPPED_DUPLICATE,
            rom_id=existing_id,
            message=message,
            degraded=tuple(skipped),
            warnings=tuple(warnings),
        )

    # 5. Upload. The return value is deliberately discarded: RomM's
    #    /complete answers a bare 201 with no body, so there is nothing in
    #    it to read -- not a rom id, not anything.
    queue.set_state(job.id, JobState.UPLOADING)
    for path in to_upload:
        try:
            backend.upload_rom(path, platform_id)
        except Exception as exc:
            raise _ImportFailure(
                f"upload of {path.name!r} to {library_name} failed: {exc}"
            ) from exc

    # 5a. Register the uploaded bytes with the library.
    #
    #     This step is not optional and it is not a refresh. RomM's
    #     /complete writes the file into the library directory and creates
    #     **no database row at all** -- `GET /api/roms` does not list it,
    #     and no REST endpoint exists that would. Its own web UI emits a
    #     socket.io `scan` after every upload; so does this. See
    #     rom_hub.backends.romm.scan for the upstream reading.
    #
    #     A failure here is reported as precisely what it is: the bytes
    #     reached RomM and only the registration did not. An operator told
    #     merely "upload failed" would go and upload it again, on top of a
    #     file that is already sitting in the library directory.
    names = ", ".join(path.name for path in to_upload)
    try:
        scanner.scan_platform(platform_id)
    except Exception as exc:
        raise _ImportFailure(
            f"{names} uploaded to {library_name} successfully, but registering it "
            f"in the library failed, so the ROM is not importable yet: {exc}. "
            f"The file already reached {library_name} for platform "
            f"{plan.platform!r} -- do not re-upload it; re-run the "
            f"registration from {library_name} itself."
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
        library = backend.list_roms(platform_id)
    except Exception as exc:
        raise _ImportFailure(
            f"the upload of {len(to_upload)} file(s) reported success, but "
            f"confirming it in the library failed: {exc}"
        ) from exc

    rom_ids: list[int] = []
    missing: list[str] = []
    for path in to_upload:
        # Hash first: it is the strongest evidence that *this* file landed.
        # Filename is accepted as a fallback because it is also decisive
        # here -- the Hub chose the name via x-upload-filename and RomM
        # assembles to `roms/<platform_fs_slug>/<that name>` -- and because
        # the Hub cannot reproduce RomM's digest for the archive formats it
        # has no reader for (.7z, .rar). Without this, those would upload
        # and scan correctly and then be reported as missing.
        match = find_duplicate(hashes_by_path[path], library) or find_by_filename(
            path.name, library
        )
        if match is None:
            missing.append(path.name)
            continue
        rom_id = _rom_id_of(match)
        if rom_id is not None:
            rom_ids.append(rom_id)

    if missing:
        raise _ImportFailure(
            f"{library_name} reported the upload of {', '.join(missing)} "
            f"succeeded, but the file did not appear in the library for "
            f"platform {plan.platform!r} afterwards -- the ROM did not land, "
            f"so the import is not done. Check {library_name}'s own logs for "
            f"the upload."
        )

    # 6. Collection, only if the plan asked for one *and* the backend can
    #    do it. After 5b, because the rom ids it needs only exist once
    #    that lookup has run. When it cannot, `skipped` already holds the
    #    note written at step 1a and the import finishes without it.
    if plan.collection and collection_skip is None:
        if not rom_ids:
            raise _ImportFailure(
                f"uploaded {len(to_upload)} file(s), but {library_name}'s "
                f"upload response carried no rom id, so they could not be "
                f"added to collection {plan.collection!r}; add them by hand"
            )
        try:
            collection_id = backend.ensure_collection(plan.collection)
            backend.add_to_collection(collection_id, rom_ids)
        except Exception as exc:
            raise _ImportFailure(
                f"the upload succeeded, but adding it to collection "
                f"{plan.collection!r} failed: {exc}"
            ) from exc

    # 7. Done -- and, if anything optional was skipped on the way, done
    #    with that said out loud. The operator reads this line and the job
    #    record; a skip that appears in neither is a skip that silently
    #    changed what they got.
    present = f", {len(duplicates)} already present" if duplicates else ""
    message = (
        f"imported {len(to_upload)} file(s) as rom id(s) {rom_ids}{present}"
        + _skipped_suffix(skipped)
    )
    queue.set_state(job.id, JobState.DONE)
    return ImportResult(
        job_id=job.id,
        state=JobState.DONE,
        rom_id=rom_ids[0] if rom_ids else None,
        message=message,
        degraded=tuple(skipped),
        warnings=tuple(warnings),
    )


def _skipped_suffix(skipped: list[SkippedStep]) -> str:
    """The degradation notes, appended to whatever the outcome was."""
    if not skipped:
        return ""
    return ". " + "; ".join(str(step) for step in skipped)


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
