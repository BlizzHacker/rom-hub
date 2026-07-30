"""Drive the real import and enrich pipelines against real backends.

    python scripts/proof_matrix.py --out docs/PROOF.md \
        --romm-url http://127.0.0.1:8085 --romm-user proof --romm-password ... \
        --gaseous-url http://127.0.0.1:8086 --gaseous-user ... --gaseous-password ... \
        --retrom-url http://127.0.0.1:5102

README.md claims a plugin written against one library server works against
all three. That claim is prose, and prose does not fail when it stops being
true. This runs `rom_hub.importer.run_import` and
`rom_hub.metadata.run_enrich` -- the same functions `rom-hub import` and
`rom-hub enrich` call, unmodified -- once per backend against a live server,
and prints what each one actually did.

## The distinction this exists to preserve

A backend that has no collections and a backend whose collections are
broken must never produce the same cell, because one is a fact about the
product and the other is a bug in this project. So:

* **UNSUPPORTED** -- the backend does not declare the capability, *and*
  the host's own classification machinery was asked and agreed. For an
  essential capability that means `backends.require()` raised
  `CapabilityUnsupported` before any work started; for an optional one it
  means `backends.degrade()` returned a `SkippedStep` and the surrounding
  operation still completed. The evidence column carries the message that
  refusal or degradation actually produced, so the cell is an observation
  rather than a claim.
* **FAIL** -- the backend declares the capability and it did not work.
* **NOT-RUN** -- no server was given, or an earlier step failed so this
  one could not be attempted. Never a substitute for FAIL.
* **PASS** -- it worked, and the evidence says what was observed.

## What is real here and what is not

Real: the backend clients, the HTTP and gRPC-Web traffic, the servers, the
dedup hash, the upload, the post-upload registration, the metadata write,
the capability gating, and the job queue.

Not real: the plugin. `run_import` takes a `plugin` with `.plan()` and a
`.manifest`, and this supplies a stub, because the plugin side of the
boundary is backend-agnostic *by construction* -- a plugin returns a
description and never learns which server executed it -- and it is already
proven by 1461 unit tests plus a genuinely hostile plugin run under seccomp
in CI. Spawning a real subprocess three times would re-prove the sandbox
and say nothing new about backends.

Not real either: the download. A plugin-supplied URL must be `https`
(`netpolicy.ALLOWED_SCHEMES`), so serving the fixture over local HTTP would
require either weakening that rule -- which is the one thing this tool must
not do -- or a TLS fixture that tests nothing about backends. `run_import`
already accepts an injected `Downloader`, so one is injected that copies
the fixture from disk. Every subsequent step is untouched.

## Safety

The matrix uploads a ROM, writes metadata over it and leaves it there.
Point it at a disposable stack -- `scripts/proof-stack.compose.yml` -- and
never at a library you care about. It refuses port 8080 by default for that
reason; `--i-know-what-im-doing` lifts it.

Hostnames are redacted from the report: `docs/PROOF.md` is committed to a
public repository and where somebody's server lives is not evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from rom_hub.backends import (  # noqa: E402
    ARTWORK,
    COLLECTIONS,
    FIRMWARE,
    METADATA,
    CapabilityUnsupported,
    capabilities_of,
    degrade,
    require,
)
from rom_hub.firmware import install_firmware  # noqa: E402
from rom_hub.importer import run_import  # noqa: E402
from rom_hub.jobs import JobQueue, JobState  # noqa: E402
from rom_hub.manifest import parse_manifest  # noqa: E402
from rom_hub.metadata import run_enrich  # noqa: E402
from rom_hub.types import (  # noqa: E402
    FetchFile,
    FetchPlan,
    FirmwareArtifact,
    MetadataPatch,
    RomRef,
    SearchResult,
)

PASS = "PASS"
FAIL = "FAIL"
UNSUPPORTED = "UNSUPPORTED"
NOT_RUN = "NOT-RUN"

#: The platform every backend is asked for, and deliberately not created by
#: this script: none of the three backends will invent a platform, and
#: `LibraryBackend.platform_id()` refuses to guess one.
#:
#: NES rather than DOS because Gaseous constrains the choice. Its
#: `GET /Platforms` lists only platforms *already represented in the
#: library*, and it decides a file's platform from the file itself -- so a
#: platform exists there only once a ROM its signature database recognises
#: has been ingested. `.nes` is on the NES entry's `supportedFileExtensions`
#: in Gaseous' built-in platform map; DOS' list is empty, which is why the
#: first attempt at this matrix could not resolve a platform on Gaseous at
#: all. RomM and Retrom are indifferent, so NES it is for all three.
PLATFORM = "nes"

#: A collection name nothing else will collide with.
COLLECTION = "ROM Hub proof matrix"

#: Distinguishes this run's fixture from every other run's.
#:
#: The import step is only meaningful against a ROM the library has not
#: seen: dedup is doing its job when a second run of a fixed fixture comes
#: back SKIPPED_DUPLICATE, but that turns the *import* row into a failure
#: that is really just a stale library. Naming the fixture after the run --
#: and putting the same stamp inside it, so the content hash differs too --
#: makes the tool re-runnable against a stack that is already populated,
#: which is what a reproducible proof needs.
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

#: The fixture, named so that anyone who finds it in a library knows what
#: it is and when it got there.
#:
#: A structurally valid iNES image: the 16-byte header, one 16 KiB PRG bank
#: and one 8 KiB CHR bank. It is a real file of a real format -- which
#: matters, because RomM opens what it is given during a scan and a byte
#: string that merely starts with the right magic is not a file it can
#: read; the scan then logs it, declines to make a row for it, and the
#: import fails its own post-condition for a reason that is entirely the
#: fixture's fault.
ROM_NAME = f"rom-hub-proof-{RUN_ID}.nes"


def _fixture_bytes() -> bytes:
    header = bytearray(16)
    header[0:4] = b"NES\x1a"
    header[4] = 1  # PRG banks, 16 KiB each
    header[5] = 1  # CHR banks, 8 KiB each
    body = bytearray(16384 + 8192)
    marker = (
        f"rom-hub proof matrix fixture {RUN_ID}. Not a game. Safe to delete."
    ).encode("ascii")
    body[: len(marker)] = marker
    return bytes(header + body)


ROM_BYTES = _fixture_bytes()


def _cover_png(size: int = 64) -> bytes:
    """A solid grey PNG, written by hand so this needs no image library.

    Deliberately not 1x1. RomM resizes an uploaded cover into thumbnails,
    and a one-pixel image scales down to zero: `PUT /api/roms/{id}` answers
    500 with `ValueError: height and width must be > 0`, which reads
    exactly like a broken artwork path in the Hub and is not one.
    """
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes([0x80, 0x80, 0x80] * size) for _ in range(size))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


COVER_PNG = _cover_png()

#: The firmware fixture. Stamped like the ROM so a library that already
#: holds one from an earlier run does not turn the *upload* row into a
#: failure that is really a stale library -- the same reasoning as
#: `ROM_NAME`, and it matters more here because firmware dedup is by file
#: name rather than by hash.
FIRMWARE_NAME = f"rom-hub-proof-{RUN_ID}.bin"
FIRMWARE_BYTES = (
    f"rom-hub proof matrix firmware fixture {RUN_ID}. Not a BIOS. "
    f"Safe to delete."
).encode("ascii")

#: Manifest for the stub plugin. Real enough to be parsed by the real
#: parser -- an invalid one would be rejected exactly as a plugin's is.
STUB_MANIFEST = """
[plugin]
slug = "proof-matrix"
name = "Proof matrix"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "proof:Search"

[permissions]
network = ["proof.invalid"]
romm_api = []
"""


def _redact(url: str) -> str:
    """`http://library.example:8085` -> `http://<host>:8085`.

    docs/PROOF.md is public. The port is evidence -- it says the matrix ran
    against something other than a default install -- and the host is not.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url>"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://<host>{port}"


# -- the stub plugin -------------------------------------------------------


class StubPlugin:
    """A started plugin, as `run_import` and `run_enrich` see one.

    Both take `plugin` structurally: something with `.manifest` and the one
    method they call. Nothing else about a `PluginProcess` is reachable
    from inside them, which is the point of the broker seam -- and is why
    substituting this changes nothing downstream of `plan()`.
    """

    def __init__(self, plan: FetchPlan | None = None, patch: MetadataPatch | None = None):
        self.manifest = parse_manifest(STUB_MANIFEST)
        self._plan = plan
        self._patch = patch

    def plan(self, result: SearchResult) -> FetchPlan:
        assert self._plan is not None
        return self._plan

    def firmware_plan(self, firmware) -> FetchPlan:
        # `install_firmware` calls this the way `run_import` calls `plan`:
        # structurally, on something with a `.manifest`. Same stub, same
        # seam, and the download below is the same injected fixture.
        assert self._plan is not None
        return self._plan

    def enrich(self, rom: RomRef) -> MetadataPatch:
        assert self._patch is not None
        return self._patch


class FixtureDownloader:
    """Satisfies the `Downloader` protocol from a file already on disk.

    See "What is real here and what is not": a plugin-planned URL must be
    https, and standing up TLS to prove a backend works would be proving
    the wrong thing.
    """

    def __init__(self, source: Path):
        self.source = source
        self.calls: list[str] = []

    def download(self, url: str, dest: Path, expected_size: int | None = None) -> Path:
        self.calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, dest)
        return dest

    def close(self) -> None:
        pass


# -- results ---------------------------------------------------------------


@dataclass
class Cell:
    outcome: str
    evidence: str


@dataclass
class BackendRun:
    name: str
    label: str
    url: str = ""
    version: str = ""
    image: str = ""
    platform: str = PLATFORM
    declared: frozenset[str] = frozenset()
    cells: dict[str, Cell] = field(default_factory=dict)

    def set(self, step: str, outcome: str, evidence: str) -> None:
        self.cells[step] = Cell(outcome, evidence)

    def rest_not_run(self, steps: list[str], why: str) -> None:
        for step in steps:
            self.cells.setdefault(step, Cell(NOT_RUN, why))


#: (key, column heading). Order is the order the pipeline performs them in.
STEPS: list[tuple[str, str]] = [
    ("connect", "connect"),
    ("platform", "platform lookup"),
    ("list", "list library"),
    ("import", "import"),
    ("register", "post-upload registration"),
    ("dedup", "dedup on re-import"),
    ("collections", "collections"),
    ("metadata", "metadata write"),
    ("artwork", "cover art"),
    ("firmware", "firmware store"),
]
STEP_KEYS = [key for key, _ in STEPS]


def _short(exc: BaseException, limit: int = 220) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


# -- the run ---------------------------------------------------------------


def declared_cells(run: BackendRun, backend) -> None:
    """Record every cell that follows from `capabilities()` alone.

    Run immediately after `authenticate()`, before anything can fail, so
    that a backend which falls over at step three still reports honestly
    on the capabilities it never had. "Gaseous has no collections" is a
    fact about Gaseous; it does not become unknown because its library
    listing broke, and marking it NOT-RUN would throw away a real
    observation.

    Only *absences* are filled in here. A declared capability still has to
    be exercised before it earns a PASS.
    """
    try:
        require(backend, METADATA, "enriching a rom's metadata")
    except CapabilityUnsupported as exc:
        run.set("metadata", UNSUPPORTED, f"require() refuses up front: {exc}")

    for step, capability, what in (
        ("collections", COLLECTIONS, "the collection the plan named"),
        ("artwork", ARTWORK, "the cover art the patch proposed"),
        ("firmware", FIRMWARE, "filing the BIOS in the library"),
    ):
        skip = degrade(backend, capability, what)
        if skip is not None:
            run.set(step, UNSUPPORTED, f"not declared; degrade() -> {skip}")


def exercise(
    run: BackendRun, backend, work: Path, platform: str, verbose: bool
) -> None:
    """Walk one backend through every step, recording what happened."""
    remaining = list(STEP_KEYS)

    def done(step: str) -> None:
        if step in remaining:
            remaining.remove(step)

    # 1. connect -----------------------------------------------------------
    try:
        backend.authenticate()
        run.declared = capabilities_of(backend)
        run.set(
            "connect",
            PASS,
            f"authenticate() ok against {_redact(run.url)}; declares "
            f"{', '.join(sorted(run.declared)) or 'nothing'}",
        )
    except Exception as exc:
        run.set("connect", FAIL, _short(exc))
        run.rest_not_run(remaining[1:], "no connection")
        return
    done("connect")

    declared_cells(run, backend)

    # 2. platform ----------------------------------------------------------
    try:
        platform_id = backend.platform_id(platform)
        run.set("platform", PASS, f"{platform!r} resolved to id {platform_id}")
    except Exception as exc:
        run.set("platform", FAIL, _short(exc))
        run.rest_not_run(remaining, "no platform to work in")
        return
    done("platform")

    # 3. list --------------------------------------------------------------
    try:
        before = backend.list_roms(platform_id)
        run.set("list", PASS, f"{len(before)} rom(s) before the import")
    except Exception as exc:
        run.set("list", FAIL, _short(exc))
        run.rest_not_run(remaining, "the library could not be listed")
        return
    done("list")

    # -- the import pipeline, for real -------------------------------------
    fixture = work / ROM_NAME
    fixture.write_bytes(ROM_BYTES)
    digest = hashlib.sha1(ROM_BYTES).hexdigest()

    queue = JobQueue(work / f"{run.name}-jobs.sqlite")
    downloader = FixtureDownloader(fixture)

    # `collection` is set unconditionally and on purpose: it is exactly the
    # shape that used to break Gaseous and Retrom imports outright, because
    # the archive-org plugin names a collection by default and there is no
    # way to clear one from a plan. A backend without collections must do
    # the import anyway and report the skip.
    plan = FetchPlan(
        files=[
            FetchFile(
                url=f"https://proof.invalid/{ROM_NAME}",
                filename=ROM_NAME,
                size_bytes=len(ROM_BYTES),
            )
        ],
        platform=platform,
        collection=COLLECTION,
    )
    result = SearchResult(
        source_id="rom-hub-proof-matrix",
        title="ROM Hub proof matrix fixture",
        platform=platform,
    )

    # 4/5/7. import, registration, collections -----------------------------
    try:
        outcome = run_import(
            StubPlugin(plan=plan),
            result,
            backend=backend,
            queue=queue,
            download_dir=work / f"{run.name}-downloads",
            downloader=downloader,
        )
    except Exception as exc:
        run.set("import", FAIL, _short(exc))
        run.rest_not_run(remaining, "the import raised")
        return

    if verbose:
        print(f"    import -> {outcome.state.value}: {outcome.message}")

    imported_ok = outcome.state is JobState.DONE
    if imported_ok:
        run.set(
            "import",
            PASS,
            f"job {outcome.job_id} DONE, rom id {outcome.rom_id}; "
            f"downloader called for {len(downloader.calls)} file(s), sha1 "
            f"{digest[:12]}",
        )
    else:
        run.set(
            "import",
            FAIL,
            f"job {outcome.job_id} ended {outcome.state.value}: {outcome.message}",
        )
    done("import")

    # Registration is not a separate call the matrix makes -- `run_import`
    # calls `scan_platform()` itself, and the honest evidence that it worked
    # is that the ROM is in the library afterwards, which is exactly the
    # post-condition `run_import` checks before reporting DONE.
    if imported_ok:
        try:
            after = backend.list_roms(platform_id)
            grew = len(after) - len(before)
            run.set(
                "register",
                PASS,
                f"scan_platform() ran inside run_import; listing went "
                f"{len(before)} -> {len(after)} rom(s)"
                + ("" if grew > 0 else " (rom confirmed by id, not by count)"),
            )
        except Exception as exc:
            run.set("register", FAIL, _short(exc))
    else:
        run.set("register", NOT_RUN, "the import did not get that far")
    done("register")

    # Collections. Not declared -> `declared_cells` already recorded the
    # UNSUPPORTED, and the only thing left to add is that the import ran
    # anyway, which is the whole point of the optional classification.
    # Declared -> it had to be filed, and `run_import` raises if
    # `ensure_collection`/`add_to_collection` fail, so a DONE with no
    # collection skip in it is the evidence.
    if COLLECTIONS not in run.declared:
        existing = run.cells["collections"].evidence
        run.set(
            "collections",
            UNSUPPORTED,
            f"{existing}. The import still "
            f"{'completed' if imported_ok else 'ran'} without it",
        )
    elif not imported_ok:
        run.set("collections", NOT_RUN, "the import did not get that far")
    elif any(step.capability == COLLECTIONS for step in outcome.degraded):
        run.set(
            "collections",
            FAIL,
            f"declared but skipped anyway: {outcome.degraded}",
        )
    else:
        run.set(
            "collections",
            PASS,
            f"declared, and the import reported no collection skip while "
            f"filing under {COLLECTION!r}",
        )
    done("collections")

    # 6. dedup -------------------------------------------------------------
    if imported_ok:
        try:
            again = run_import(
                StubPlugin(plan=plan),
                result,
                backend=backend,
                queue=queue,
                download_dir=work / f"{run.name}-downloads-2",
                downloader=FixtureDownloader(fixture),
            )
            if again.state is JobState.SKIPPED_DUPLICATE:
                run.set("dedup", PASS, f"second import refused: {again.message}")
            else:
                run.set(
                    "dedup",
                    FAIL,
                    f"second import of an identical file ended "
                    f"{again.state.value}: {again.message}",
                )
        except Exception as exc:
            run.set("dedup", FAIL, _short(exc))
    else:
        run.set("dedup", NOT_RUN, "nothing was imported to re-import")
    done("dedup")

    # 7. firmware ----------------------------------------------------------
    #
    # Placed here, before the metadata block, because that block returns
    # early on a backend without METADATA and firmware has nothing to do
    # with enriching. `declared_cells` has already written the degrade()
    # message for a backend that cannot store firmware, so this only runs
    # for one that says it can.
    #
    # The proof is not "upload_firmware did not raise". It is the
    # backend's own listing afterwards: `install_firmware` uploads, and
    # then `list_firmware` is asked whether the file is there.
    if FIRMWARE in run.declared:
        try:
            firmware_dir = work / f"{run.name}-firmware"
            item = FirmwareArtifact(
                firmware_id="proof-matrix",
                name="ROM Hub proof matrix firmware",
                platform=platform,
                license="not a licence -- a fixture",
            )
            fixture_bin = work / FIRMWARE_NAME
            fixture_bin.write_bytes(FIRMWARE_BYTES)
            installed = install_firmware(
                StubPlugin(
                    plan=FetchPlan(
                        files=[
                            FetchFile(
                                url=f"https://proof.invalid/{FIRMWARE_NAME}",
                                filename=FIRMWARE_NAME,
                            )
                        ],
                        platform=platform,
                    )
                ),
                item,
                firmware_dir=firmware_dir,
                backend=backend,
                downloader=FixtureDownloader(fixture_bin),
            )
            stored = {
                str(row.get("file_name", ""))
                for row in backend.list_firmware(backend.platform_id(platform))
                if isinstance(row, dict)
            }
            if FIRMWARE_NAME in stored:
                run.set(
                    "firmware",
                    PASS,
                    f"{installed.uploaded} file(s) uploaded; the backend's own "
                    f"firmware listing for {platform!r} now carries "
                    f"{FIRMWARE_NAME}",
                )
            else:
                run.set(
                    "firmware",
                    FAIL,
                    f"upload reported {installed.uploaded} file(s) but the "
                    f"backend's firmware listing does not contain "
                    f"{FIRMWARE_NAME}; it has {sorted(stored)}",
                )
        except Exception as exc:
            run.set("firmware", FAIL, _short(exc))
    done("firmware")

    # 8/9. metadata and artwork -------------------------------------------
    #
    # METADATA is essential to `run_enrich`: a backend without it refuses
    # before doing anything, which `declared_cells` already recorded from
    # the real `require()` message. Artwork cannot be reached either, since
    # enrich never gets past the metadata gate.
    if METADATA not in run.declared:
        if "artwork" not in run.cells:
            run.set(
                "artwork",
                UNSUPPORTED,
                "declared, but unreachable: run_enrich refuses at the "
                "metadata gate, so there is no operation left to attach a "
                "cover to",
            )
        done("metadata")
        done("artwork")
        return

    if outcome.rom_id is None:
        run.rest_not_run(remaining, "no rom id to enrich")
        return

    rom = RomRef(
        rom_id=outcome.rom_id,
        name="ROM Hub proof matrix fixture",
        filename=ROM_NAME,
        platform=platform,
    )

    try:
        enriched = run_enrich(
            StubPlugin(patch=MetadataPatch(name="ROM Hub proof matrix (renamed)")),
            rom,
            backend=backend,
            work_dir=work / f"{run.name}-enrich",
        )
        if enriched.changed:
            run.set(
                "metadata",
                PASS,
                f"run_enrich wrote {', '.join(sorted(enriched.fields)) or 'no fields'} "
                f"to rom {rom.rom_id}",
            )
        else:
            run.set("metadata", FAIL, f"nothing was written: {enriched.message}")
    except Exception as exc:
        run.set("metadata", FAIL, _short(exc))
    done("metadata")

    if ARTWORK not in run.declared:
        # `declared_cells` already wrote the degrade() message.
        done("artwork")
        return

    try:
        covered = run_enrich(
            StubPlugin(
                patch=MetadataPatch(
                    artwork_base64=base64.b64encode(COVER_PNG).decode("ascii"),
                    artwork_filename="cover.png",
                )
            ),
            rom,
            backend=backend,
            work_dir=work / f"{run.name}-cover",
        )
        if covered.artwork_bytes > 0:
            run.set(
                "artwork",
                PASS,
                f"{covered.artwork_bytes} bytes of cover attached to rom {rom.rom_id}",
            )
        else:
            run.set("artwork", FAIL, f"no artwork written: {covered.message}")
    except Exception as exc:
        run.set("artwork", FAIL, _short(exc))
    done("artwork")


# -- backends --------------------------------------------------------------


def _probe_version(url: str, path: str, pick) -> str:
    """Read a server's own version over plain HTTP.

    Deliberately not routed through the backend clients: neither RomM's nor
    Gaseous' exposes one, and adding a `server_version()` to a client for
    the benefit of a reporting script would be putting a tool's convenience
    into the shipped surface.
    """
    import httpx

    try:
        resp = httpx.get(url.rstrip("/") + path, timeout=15)
        resp.raise_for_status()
        return str(pick(resp))
    except Exception:
        return ""


def _romm(args):
    from rom_hub.backends.romm.backend import RommBackend

    os.environ.update(
        ROMM_URL=args.romm_url, ROMM_USER=args.romm_user, ROMM_PASSWORD=args.romm_password
    )
    backend = RommBackend.from_env()
    return backend, lambda: _probe_version(
        args.romm_url, "/api/heartbeat", lambda r: r.json()["SYSTEM"]["VERSION"]
    )


def _gaseous(args):
    from rom_hub.backends.gaseous.backend import GaseousBackend

    os.environ.update(
        GASEOUS_URL=args.gaseous_url,
        GASEOUS_USER=args.gaseous_user,
        GASEOUS_PASSWORD=args.gaseous_password,
    )
    backend = GaseousBackend.from_env()

    def version() -> str:
        # Gaseous answers `System/Version` with a 302 to its login form
        # unless the session cookie is present, so this goes through the
        # client's own authorized request rather than a bare GET. Read-only,
        # and asked for after the run, so it cannot perturb anything.
        #
        # The body's *shape* changed between generations and the text alone
        # is not the version: 1.7.x answers a bare quoted string
        # (`"1.7.14.0"`), 2.0 answers the whole system document, whose
        # `AppVersion` is the field this column wants. Taking `.text` for
        # both put a 2 KB JSON blob in a published table -- so the object
        # form is read as an object.
        import json as _json

        resp = backend.client._authorized_request(
            "GET", f"{args.gaseous_url.rstrip('/')}/api/v1.1/System/Version"
        )
        text = resp.text.strip()
        try:
            payload = _json.loads(text)
        except ValueError:
            return text.strip('"')
        if isinstance(payload, dict):
            # Missing rather than guessed: a version this could not read is
            # left blank by the caller, which is better than a wrong pin.
            return str(payload.get("AppVersion") or "")
        return str(payload)

    return backend, version


def _retrom(args):
    from rom_hub.backends.retrom.backend import RetromBackend

    os.environ.update(RETROM_URL=args.retrom_url)
    backend = RetromBackend.from_env()
    return backend, lambda: backend.client.server_version()


BACKENDS = [
    ("romm", "RomM", _romm, "romm_url"),
    ("gaseous", "Gaseous", _gaseous, "gaseous_url"),
    ("retrom", "Retrom", _retrom, "retrom_url"),
]


def image_of(container: str) -> str:
    """`docker inspect` the container, if there is a docker to ask.

    Best effort on purpose. The matrix has to be able to run from a
    workstation against a stack on another host, where there is no local
    docker and this simply has nothing to say -- an empty cell is a better
    report than a crash, and the server-reported version column carries
    the pinning either way.
    """
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}", container],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    text = out.stdout.decode("utf-8", "replace").strip()
    # A docker that is present but answering something unexpected (a shim,
    # a wrapper, a Windows `docker` that talks to a different daemon) must
    # not put mojibake in a published table.
    if not text or not text.isprintable():
        return ""
    image, _, digest = text.partition(" ")
    return f"{image} ({digest[:19]})" if digest else image


# -- report ----------------------------------------------------------------

SYMBOL = {PASS: "PASS", FAIL: "**FAIL**", UNSUPPORTED: "UNSUPPORTED", NOT_RUN: "NOT-RUN"}


def render(runs: list[BackendRun], command: str, started: datetime) -> str:
    lines: list[str] = []
    add = lines.append

    add("# Proof matrix")
    add("")
    add(
        "Generated by `scripts/proof_matrix.py`, which runs "
        "`rom_hub.importer.run_import` and `rom_hub.metadata.run_enrich` -- "
        "the same functions `rom-hub import` and `rom-hub enrich` call -- "
        "against a live server of each supported backend. **This file is "
        "generated. Re-run the command below rather than editing it.**"
    )
    add("")
    add(f"* **Produced** {started.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    add(f"* **Command** `{command}`")
    add(
        "* **Stack** `scripts/proof-stack.compose.yml` + "
        "`scripts/proof-stack-bootstrap.sh` (disposable; torn down after)"
    )
    add("")
    add("Backends it ran against:")
    add("")
    add("| Backend | Version | Image | Endpoint | Declares |")
    add("|---|---|---|---|---|")
    for run in runs:
        add(
            f"| {run.label} | {run.version or '—'} | "
            f"`{run.image or '—'}` | `{_redact(run.url) if run.url else '—'}` | "
            f"{', '.join(sorted(run.declared)) if run.declared else '—'} |"
        )
    add("")
    add(
        "**Version** is what each server reports about itself, and is the "
        "authoritative pin. **Image** is filled in only when the matrix runs "
        "on the machine hosting the containers; run from anywhere else there "
        "is no Docker daemon to ask, and a blank is better than a guess."
    )
    add("")
    add("## What the four outcomes mean")
    add("")
    add(
        "| | |\n|---|---|\n"
        "| **PASS** | It ran and the evidence column says what was observed. |\n"
        "| **FAIL** | The backend declares the capability and it did not work. "
        "This is a bug in this project. |\n"
        "| **UNSUPPORTED** | The backend does not declare the capability *and* "
        "the host agreed: `require()` refused before doing any work, or "
        "`degrade()` returned a skip and the operation completed without it. "
        "Not a bug — and deliberately not spelled the same as FAIL, because "
        "conflating the two is how real breakage hides behind an expected gap. |\n"
        "| **NOT-RUN** | No server, or an earlier step failed so this one was "
        "never attempted. Never a stand-in for FAIL. |"
    )
    add("")
    add("## The matrix")
    add("")
    header = "| Capability | " + " | ".join(run.label for run in runs) + " |"
    add(header)
    add("|---" * (len(runs) + 1) + "|")
    for key, title in STEPS:
        cells = []
        for run in runs:
            cell = run.cells.get(key)
            cells.append(SYMBOL[cell.outcome] if cell else SYMBOL[NOT_RUN])
        add(f"| {title} | " + " | ".join(cells) + " |")
    add("")
    add("## Evidence")
    add("")
    for run in runs:
        add(f"### {run.label}")
        add("")
        if not run.cells:
            add("Not run.")
            add("")
            continue
        add("| Capability | Outcome | Evidence |")
        add("|---|---|---|")
        for key, title in STEPS:
            cell = run.cells.get(key) or Cell(NOT_RUN, "not attempted")
            evidence = cell.evidence.replace("|", "\\|").replace("\n", " ")
            add(f"| {title} | {SYMBOL[cell.outcome]} | {evidence} |")
        add("")
    add("## Reproducing this")
    add("")
    add("```sh")
    add("docker compose -p proofmatrix -f scripts/proof-stack.compose.yml up -d")
    add("bash scripts/proof-stack-bootstrap.sh")
    add(f"{command}")
    add("docker compose -p proofmatrix -f scripts/proof-stack.compose.yml down -v")
    add("```")
    add("")
    add(
        "Host names are redacted to `<host>`; ports are not, because a port "
        "other than the product default is evidence that the run did not "
        "touch a real library."
    )
    add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-backend proof matrix")
    parser.add_argument("--romm-url", default="")
    parser.add_argument("--romm-user", default="")
    parser.add_argument("--romm-password", default="")
    parser.add_argument("--gaseous-url", default="")
    parser.add_argument("--gaseous-user", default="")
    parser.add_argument("--gaseous-password", default="")
    parser.add_argument("--retrom-url", default="")
    parser.add_argument(
        "--platform",
        default=PLATFORM,
        help=f"platform name every backend is asked for (default {PLATFORM!r})",
    )
    # Per-backend override, because one of the three cannot be given the
    # same answer as the others. Gaseous decides a ROM's platform from the
    # file's signature and lists only platforms already in the library, so
    # a synthetic fixture -- which is in no signature database, and must be,
    # since the alternative is shipping a real ROM -- always lands on its
    # "Unknown" platform. Asking Gaseous for "nes" therefore fails for a
    # reason that is a fact about Gaseous, not a defect in the Hub. The
    # override is explicit and appears in the report so nobody has to
    # reverse-engineer why one column used a different platform.
    parser.add_argument("--romm-platform", default="")
    parser.add_argument("--gaseous-platform", default="")
    parser.add_argument("--retrom-platform", default="")
    parser.add_argument("--out", default="", help="write the report here")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        help="permit a port that looks like a real install",
    )
    args = parser.parse_args(argv)

    started = datetime.now(timezone.utc)
    command = "python scripts/proof_matrix.py " + " ".join(
        _redacted_argv(argv if argv is not None else sys.argv[1:])
    )

    for _, label, _, url_attr in BACKENDS:
        url = getattr(args, url_attr)
        if url and urlsplit(url).port in (8080, 5101) and not args.i_know_what_im_doing:
            print(
                f"refusing to run against {label} on port "
                f"{urlsplit(url).port}: that is a product default and this "
                f"tool uploads, renames and leaves files behind. Point it at "
                f"the disposable stack, or pass --i-know-what-im-doing.",
                file=sys.stderr,
            )
            return 2

    runs: list[BackendRun] = []
    # ignore_cleanup_errors: the job queue is SQLite, and on Windows an
    # open handle makes the teardown raise *after* every result is already
    # in hand. Losing the report to a temp-file unlink is absurd.
    with TemporaryDirectory(
        prefix="rom-hub-proof-", ignore_cleanup_errors=True
    ) as tmp:
        work = Path(tmp)
        for name, label, factory, url_attr in BACKENDS:
            run = BackendRun(name=name, label=label)
            run.url = getattr(args, url_attr)
            run.platform = getattr(args, f"{name}_platform") or args.platform
            run.image = image_of(f"proof{name}")
            if not run.url:
                run.rest_not_run(
                    STEP_KEYS, f"no --{name}-url given, so no server to run against"
                )
                runs.append(run)
                print(f"[{label}] NOT-RUN (no URL)")
                continue

            print(f"[{label}] {_redact(run.url)}")
            backend = None
            try:
                backend, version = factory(args)
                exercise(run, backend, work, run.platform, args.verbose)
                # After, not before: two of the three answer a version only
                # over an authenticated session, and `exercise` is what
                # establishes one. A version this could not read is left
                # blank rather than guessed.
                try:
                    run.version = str(version())
                except Exception:
                    run.version = ""
            except Exception as exc:
                if args.verbose:
                    traceback.print_exc()
                run.set("connect", FAIL, _short(exc))
                run.rest_not_run(STEP_KEYS, "the backend could not be built")
            finally:
                if backend is not None:
                    try:
                        backend.close()
                    except Exception:
                        pass
            for key, title in STEPS:
                cell = run.cells.get(key)
                if cell:
                    print(f"    {title:<26} {cell.outcome:<12} {cell.evidence[:110]}")
            runs.append(run)

    report = render(runs, command, started)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8", newline="\n")
        print(f"\nwrote {out}")
    else:
        print()
        print(report)

    # A FAIL is a failure of this project and must be visible to a shell.
    # UNSUPPORTED and NOT-RUN are not.
    failures = sum(
        1 for run in runs for cell in run.cells.values() if cell.outcome == FAIL
    )
    if failures:
        print(f"\n{failures} FAIL cell(s)", file=sys.stderr)
    return 1 if failures else 0


def _redacted_argv(argv: list[str]) -> list[str]:
    """The command line as a public file may repeat it.

    Two things must not survive into docs/PROOF.md: a password, and where
    somebody's server is. Both are stripped here rather than at the call
    site, because the command string is the one place in the report that
    is copied verbatim from the operator's shell.
    """
    out: list[str] = []
    redact_next = ""
    for arg in argv:
        if redact_next:
            out.append(redact_next)
            redact_next = ""
            continue
        key = arg.split("=", 1)[0]
        secret = "password" in key
        location = key.endswith("-url")
        if "=" in arg and (secret or location):
            out.append(f"{key}={'***' if secret else _redact(arg.split('=', 1)[1])}")
            continue
        out.append(arg)
        if secret:
            redact_next = "***"
        elif location:
            redact_next = "<url>"
    return out


if __name__ == "__main__":
    raise SystemExit(main())
