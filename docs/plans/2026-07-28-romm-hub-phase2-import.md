# RomM Hub Phase 2 — Import Implementation Plan

**Goal:** The Hub can take a search result from a plugin and put the actual ROM into RomM's library — download, dedup, chunked upload, collection grouping — with a job queue that survives a restart.

**Architecture:** A new `importer` capability lets a plugin return a *`FetchPlan`* describing what to fetch; the **host** validates every URL in that plan against the plugin's own allowlist, downloads it, hashes it, dedups against RomM, and uploads it via RomM's chunked upload API. The plugin never sees the RomM token and never performs a privileged step.

**Tech Stack:** Python 3.12, httpx, pydantic v2, sqlite3 (stdlib), pytest.

## Verified API contract

Read from RomM 4.9.2's live `openapi.json` on the deployment target. Use these exactly.

**Auth** — `POST /api/token`, body `application/x-www-form-urlencoded` (OAuth2 password grant).

**Chunked upload** — three calls:

| Step | Call | Required headers |
|---|---|---|
| 1 | `POST /api/roms/upload/start` → 201 | `x-upload-platform` (**integer platform id**), `x-upload-filename` (str), `x-upload-total-size` (int), `x-upload-total-chunks` (int) |
| 2 | `PUT /api/roms/upload/{upload_id}` → 200, once per chunk | `x-chunk-index` (int); chunk bytes are the body |
| 3 | `POST /api/roms/upload/{upload_id}/complete` → 201 | — |

Abort with `POST /api/roms/upload/{upload_id}/cancel`.

**`x-upload-platform` is an integer id, not a slug.** The adapter must resolve slug → id via `GET /api/platforms` and cache it.

**Chunk count is the client's choice** — pick a chunk size, then `total_chunks = ceil(size / chunk_size)`. The two must agree or the server will wait forever.

**Dedup** — `SimpleRomSchema` exposes `crc_hash`, `md5_hash`, `sha1_hash`. `GET /api/roms` has **no hash filter** (its params are `search_term`, `platform_ids`, `collection_id`, …), so dedup is: list roms for the target platform, compare hashes client-side.

## Global Constraints

- **The plugin never holds the RomM token and never uploads.** It returns a `FetchPlan`; the host does every privileged step. This is the whole design.
- **Every URL in a returned `FetchPlan` must pass `netpolicy.check_url` against that plugin's declared allowlist**, exactly like `ctx.http` does. A plugin must not be able to make the host fetch from a host it never declared. This needs a test.
- **Never block `clone`/`fork`** in the seccomp denylist, and do not modify `src/romm_hub/sandbox.py`'s denylist.
- `sandbox.install()` is irreversible and process-wide — **any test that triggers it runs in a child process**, never in the pytest process.
- Do not weaken `netpolicy`, the fail-closed sandbox policy, or the `ROMM_HUB_ALLOW_UNSANDBOXED` opt-out.
- Downloads land under `var/downloads/` (gitignored, off `C:`). Never write into the repo.
- All RomM network calls in tests are mocked. **No test may require a live RomM.** The one live check is manual, in Task 8.
- Python 3.12+, `src/` layout, TDD, `python -m pytest` from repo root.
- Verify on **both** Windows and Linux. Baselines to beat: Windows 94 passed / 5 skipped, Linux 95 passed / 4 skipped. `tests/test_hostile_plugin.py` must keep passing on Linux.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/romm_hub/types.py` | add `FetchFile`, `FetchPlan` (modify) |
| `src/romm_hub/romm/client.py` | RomM HTTP client: auth, platforms, roms, upload (new) |
| `src/romm_hub/romm/upload.py` | chunked upload orchestration (new) |
| `src/romm_hub/dedup.py` | hashing + match against existing library (new) |
| `src/romm_hub/jobs.py` | SQLite-backed job queue (new) |
| `src/romm_hub/importer.py` | the import pipeline tying it together (new) |
| `src/romm_hub_sdk/capabilities.py` | add `ImportProvider` ABC (modify) |
| `src/romm_hub_sdk/runner.py` | serve the `plan` method (modify) |
| `src/romm_hub/broker/host.py` | `PluginProcess.plan()` + FetchPlan URL validation (modify) |
| `src/romm_hub/cli.py` | `romm-hub import` (modify) |
| `plugins-dev/archive-org/archive_org/importer.py` | Archive.org importer (new) |

---

### Task 1: FetchPlan types and the `importer` capability contract

**Files:**
- Modify: `src/romm_hub/types.py`, `src/romm_hub_sdk/capabilities.py`, `src/romm_hub_sdk/__init__.py`, `src/romm_hub/manifest.py`
- Test: `tests/test_fetchplan_types.py`

**Interfaces produced:**
- `FetchFile(url: str, filename: str, size_bytes: int | None)` — `filename` must be a bare name, no separators, no `..`
- `FetchPlan(files: list[FetchFile], platform: str, collection: str | None)` — at least one file
- `ImportProvider(Capability)` with `plan(self, result: SearchResult) -> FetchPlan`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetchplan_types.py`:

```python
import pytest
from pydantic import ValidationError

from romm_hub.types import FetchFile, FetchPlan


def test_minimal_plan():
    p = FetchPlan(
        files=[FetchFile(url="https://archive.org/download/x/g.zip", filename="g.zip")],
        platform="dos",
    )
    assert p.files[0].filename == "g.zip"
    assert p.collection is None


def test_plan_requires_at_least_one_file():
    with pytest.raises(ValidationError):
        FetchPlan(files=[], platform="dos")


@pytest.mark.parametrize(
    "evil",
    ["../escape.zip", "a/b.zip", "a\\b.zip", "/abs.zip", "..", "", "."],
)
def test_filename_must_be_a_bare_name(evil):
    """A plugin must not be able to steer the host's writes with a filename."""
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename=evil)


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename="g.zip", size_bytes=-1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetchplan_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'FetchFile'`

- [ ] **Step 3: Implement**

Append to `src/romm_hub/types.py`:

```python
import posixpath
from pydantic import field_validator


class FetchFile(BaseModel):
    url: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("filename")
    @classmethod
    def _bare_name_only(cls, v: str) -> str:
        # The host writes this to disk. A plugin must not be able to point
        # that write anywhere but the job's own download directory.
        if v in {".", ".."}:
            raise ValueError("filename must not be a path segment")
        if "/" in v or "\\" in v or v != posixpath.basename(v):
            raise ValueError("filename must be a bare name, not a path")
        return v


class FetchPlan(BaseModel):
    files: list[FetchFile] = Field(min_length=1)
    platform: str = Field(min_length=1)
    collection: str | None = None
```

Add to `src/romm_hub_sdk/capabilities.py`:

```python
from romm_hub.types import FetchPlan, SearchResult


class ImportProvider(Capability):
    @abstractmethod
    def plan(self, result: SearchResult) -> FetchPlan:
        """Describe what to fetch for this result. The HOST performs the fetch."""
```

Export `ImportProvider`, `FetchPlan`, `FetchFile` from `src/romm_hub_sdk/__init__.py`.

`importer` is already in `manifest.KNOWN_CAPABILITIES` — confirm, and add a manifest test asserting an `importer` entrypoint parses.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_fetchplan_types.py -q`, then `python -m pytest -q`. Both green.

- [ ] **Step 5: Commit** — `git commit -m "feat(import): FetchPlan types and the ImportProvider capability"`

---

### Task 2: Host serves `plan`, and validates every URL it returns

**Files:**
- Modify: `src/romm_hub_sdk/runner.py`, `src/romm_hub/broker/host.py`
- Test: `tests/test_broker_plan.py`

**Interfaces produced:** `PluginProcess.plan(result: SearchResult) -> FetchPlan`, raising `PluginCallError` when the plan is invalid or contains a URL outside the plugin's allowlist.

This is the security-critical task of Phase 2. `ctx.http` is not the only way a plugin can make the host fetch something any more — `FetchPlan` is a second one, and it must be gated identically.

- [ ] **Step 1: Write the failing test**

Create `tests/test_broker_plan.py`:

```python
import textwrap
from pathlib import Path

import pytest

from romm_hub.broker.host import PluginCallError, PluginProcess
from romm_hub.manifest import parse_manifest
from romm_hub.types import SearchResult

MANIFEST = """
[plugin]
slug = "imp"
name = "Imp"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "imp_plugin:Search"
importer = "imp_plugin:Importer"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from romm_hub_sdk import (
        FetchFile, FetchPlan, ImportProvider, SearchProvider, SearchResult,
    )


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            return [SearchResult(source_id="1", title="t")]


    class Importer(ImportProvider):
        def plan(self, result):
            mode = self.ctx.config.get("mode", "good")
            host = "evil.example" if mode == "exfiltrate" else "allowed.example"
            return FetchPlan(
                files=[FetchFile(url=f"https://{host}/g.zip", filename="g.zip")],
                platform="dos",
            )
    '''
)


class NullFetcher:
    def get(self, url, params):
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "imp_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, config=None):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=NullFetcher(),
        timeout=30.0,
        allow_unsandboxed=True,
    )


def test_plan_returns_a_validated_fetchplan(plugin_dir):
    with _proc(plugin_dir) as proc:
        plan = proc.plan(SearchResult(source_id="1", title="t"))
    assert plan.platform == "dos"
    assert plan.files[0].url == "https://allowed.example/g.zip"


def test_plan_with_an_undeclared_host_is_rejected(plugin_dir):
    """A FetchPlan is a second way to make the host fetch. Gate it like ctx.http."""
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.plan(SearchResult(source_id="1", title="t"))
```

- [ ] **Step 2: Run test to verify it fails** — `AttributeError: 'PluginProcess' object has no attribute 'plan'`

- [ ] **Step 3: Implement**

In `runner.py`, add a `plan` branch beside `search`:

```python
            elif method == "plan":
                if ctx is None:
                    raise RuntimeError("init must be called before plan")
                if "importer" not in instances:
                    instances["importer"] = _load(entrypoints["importer"], ctx)
                plan = instances["importer"].plan(SearchResult(**params["result"]))
                result = plan.model_dump()
```

(import `SearchResult` in `runner.py`.)

In `broker/host.py`:

```python
    def plan(self, result: SearchResult) -> FetchPlan:
        raw = self._call("plan", {"result": result.model_dump()})
        try:
            plan = FetchPlan(**raw)
        except (ValidationError, TypeError) as exc:
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid FetchPlan: {exc}"
            ) from exc
        # A FetchPlan is a second path to making the host fetch something.
        # It gets the same allowlist gate as ctx.http, for the same reason.
        for f in plan.files:
            try:
                check_url(f.url, self.manifest.network)
            except PolicyViolation as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} FetchPlan rejected: {exc}"
                ) from exc
        return plan
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_broker_plan.py -v`, then full suite. Green.

- [ ] **Step 5: Commit** — `git commit -m "feat(import): plan capability with allowlist-gated FetchPlan URLs"`

---

### Task 3: RomM client — auth and platform resolution

**Files:** Create `src/romm_hub/romm/__init__.py`, `src/romm_hub/romm/client.py`. Test: `tests/test_romm_client.py`

**Interfaces produced:** `RommError(Exception)`; `RommClient(base_url, username, password, timeout=30.0)` with `.authenticate()`, `.platform_id(slug) -> int` (cached, raises `RommError` naming the slug if absent), `.list_platforms() -> list[dict]`, `.list_roms(platform_id) -> list[dict]`, `.ensure_collection(name) -> int`, `.add_to_collection(collection_id, rom_ids)`.

Auth is `POST /api/token` with **form-encoded** body (OAuth2 password grant), not JSON. Tests mock every HTTP call — use `httpx.MockTransport`.

- [ ] **Step 1: Write the failing test** covering, at minimum:
  - `authenticate()` posts form-encoded (assert `content-type` is `application/x-www-form-urlencoded`) and stores the bearer token
  - subsequent calls send `Authorization: Bearer <token>`
  - `platform_id("dos")` resolves via `GET /api/platforms` and returns the **integer** id
  - `platform_id` is cached — a second call issues no second HTTP request
  - `platform_id("nope")` raises `RommError` whose message contains `nope`
  - a 401 raises `RommError` mentioning authentication, not a bare `HTTPStatusError`

- [ ] **Step 2: Run test to verify it fails** — `ModuleNotFoundError: No module named 'romm_hub.romm'`

- [ ] **Step 3: Implement.** Accept an injectable `transport` parameter so tests can pass `httpx.MockTransport`. Every non-2xx response must become a `RommError` with the status and the response body excerpt — never leak a raw `httpx` exception to callers.

- [ ] **Step 4: Run tests** — targeted, then full suite. Green.

- [ ] **Step 5: Commit** — `git commit -m "feat(romm): client with auth and cached platform resolution"`

---

### Task 4: Chunked upload

**Files:** Create `src/romm_hub/romm/upload.py`. Test: `tests/test_romm_upload.py`

**Interfaces produced:** `DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024`; `upload_file(client, path: Path, platform_id: int, chunk_size: int = DEFAULT_CHUNK_SIZE, progress=None) -> dict` returning the complete response; raises `RommError` after calling `/cancel` on any failure.

- [ ] **Step 1: Write the failing test** covering:
  - a 20 MB file at a 8 MB chunk size sends `x-upload-total-chunks: 3`, and exactly 3 `PUT`s with `x-chunk-index` 0, 1, 2
  - `x-upload-total-size` equals the real file size and `x-upload-filename` equals the file's name
  - `x-upload-platform` is sent as the integer id
  - the concatenated chunk bodies reconstruct the original file **byte for byte**
  - a file smaller than one chunk sends `total_chunks: 1` and one `PUT`
  - an **empty file** is rejected before any request is made (`total_chunks: 0` would hang the server)
  - a failure mid-upload calls `POST /api/roms/upload/{id}/cancel` and raises `RommError`

- [ ] **Step 2: Run test to verify it fails** — `ModuleNotFoundError`

- [ ] **Step 3: Implement.** `total_chunks = math.ceil(size / chunk_size)`; stream the file rather than reading it whole. Wrap the whole sequence in `try/except` that cancels then re-raises as `RommError`.

- [ ] **Step 4: Run tests.** Green.

- [ ] **Step 5: Commit** — `git commit -m "feat(romm): chunked upload with cancel-on-failure"`

---

### Task 5: Dedup by hash

**Files:** Create `src/romm_hub/dedup.py`. Test: `tests/test_dedup.py`

**Interfaces produced:** `hash_file(path) -> FileHashes` (dataclass with `crc32: str`, `md5: str`, `sha1: str`, all lowercase hex); `find_duplicate(hashes, existing_roms: list[dict]) -> dict | None` matching on any of `sha1_hash`, `md5_hash`, `crc_hash` in that priority order.

- [ ] **Step 1: Write the failing test** covering:
  - `hash_file` on known content returns the known hex digests (use a fixed byte string and precomputed values)
  - hashes are lowercase hex
  - a single pass over the file computes all three (read the file once; assert by hashing a large temp file and checking it works, not by mocking)
  - `find_duplicate` matches on `sha1_hash`
  - matching is **case-insensitive** — RomM may store uppercase hex
  - a rom with `null` hash fields never matches
  - no match returns `None`

- [ ] **Step 2: Run test to verify it fails** — `ModuleNotFoundError`

- [ ] **Step 3: Implement.** One streaming pass updating all three digests together.

- [ ] **Step 4: Run tests.** Green.

- [ ] **Step 5: Commit** — `git commit -m "feat(import): hash-based dedup against the existing library"`

---

### Task 6: Persisted job queue

**Files:** Create `src/romm_hub/jobs.py`. Test: `tests/test_jobs.py`

**Interfaces produced:** `JobState` enum (`PENDING`, `DOWNLOADING`, `UPLOADING`, `DONE`, `FAILED`, `SKIPPED_DUPLICATE`); `Job` dataclass (`id: int`, `plugin: str`, `source_id: str`, `title: str`, `platform: str`, `state: JobState`, `error: str | None`, `local_path: str | None`); `JobQueue(db_path: Path)` with `.enqueue(...) -> Job`, `.claim_next() -> Job | None`, `.set_state(job_id, state, error=None, local_path=None)`, `.get(job_id)`, `.list(state=None)`, `.reset_stale()`.

- [ ] **Step 1: Write the failing test** covering:
  - enqueue then `claim_next` returns it and marks it non-`PENDING`
  - `claim_next` on an empty queue returns `None`
  - **state survives a new `JobQueue` instance on the same file** — this is the requirement the whole task exists for
  - `reset_stale()` moves `DOWNLOADING`/`UPLOADING` jobs back to `PENDING` (a restart mid-import must not strand them)
  - `set_state(..., FAILED, error="x")` persists the error text
  - the schema is created on first use against a path that does not exist yet

- [ ] **Step 2: Run test to verify it fails** — `ModuleNotFoundError`

- [ ] **Step 3: Implement** with stdlib `sqlite3`. Create the table if absent. Use a transaction for claim so two callers cannot claim the same job.

- [ ] **Step 4: Run tests.** Green.

- [ ] **Step 5: Commit** — `git commit -m "feat(import): SQLite job queue that survives a restart"`

---

### Task 7: The import pipeline

**Files:** Create `src/romm_hub/importer.py`. Test: `tests/test_importer.py`

**Interfaces produced:** `ImportResult` dataclass (`job_id`, `state`, `rom_id: int | None`, `message: str`); `run_import(plugin, result: SearchResult, *, romm: RommClient, queue: JobQueue, download_dir: Path, fetcher, allow_unsandboxed=False) -> ImportResult`.

Pipeline order — each step's failure must land the job in `FAILED` with a useful message, never a bare traceback:

1. `plugin.plan(result)` (URLs already allowlist-gated by Task 2)
2. resolve `plan.platform` → integer id via `romm.platform_id()`
3. download each file to `download_dir/<job_id>/<filename>` with **resumable range requests**
4. `hash_file` each, `find_duplicate` against `romm.list_roms(platform_id)` — on a hit, set `SKIPPED_DUPLICATE` and **do not upload**
5. `upload_file`
6. if `plan.collection`, `ensure_collection` + `add_to_collection`
7. `DONE`

- [ ] **Step 1: Write the failing test** covering, with fakes for `RommClient` and the downloader:
  - happy path reaches `DONE` and reports a rom id
  - a duplicate reaches `SKIPPED_DUPLICATE` **and the upload fake is never called** (assert on the fake)
  - an unresolvable platform fails with a message naming the slug
  - a download failure lands `FAILED` with the reason, and the job is retryable afterwards
  - `plan.collection` set → collection calls happen; unset → they do not
  - a plugin whose `plan()` raises lands `FAILED`, not an exception out of `run_import`

- [ ] **Step 2: Run test to verify it fails** — `ModuleNotFoundError`

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run tests.** Green.

- [ ] **Step 5: Commit** — `git commit -m "feat(import): end-to-end import pipeline with dedup and collections"`

---

### Task 8: Archive.org importer + CLI + live verification

**Files:**
- Create: `plugins-dev/archive-org/archive_org/importer.py`
- Modify: `plugins-dev/archive-org/manifest.toml` (declare `importer`), `src/romm_hub/cli.py`, `README.md`, `docs/DESIGN.md`
- Test: `tests/test_archive_org_importer.py`, extend `tests/test_cli.py`

**Interfaces produced:** `archive_org.importer:Importer`; CLI `romm-hub import <plugin> <source_id> [--platform] [--collection]` and `romm-hub jobs [--state]`.

The Archive.org importer must use the routing the design pass established:

- `GET https://archive.org/metadata/{identifier}` returns `metadata.emulator`, `metadata.emulator_ext`, `metadata.collection`, and `files[]`.
- **Refuse any item whose `collection` contains `stream_only`** — those are not downloadable, and attempting it is the mistake the whole `stream_only` design decision exists to prevent. Raise with a message saying the item is stream-only.
- Select the payload file by matching `emulator_ext` against the `files[]` entries; if several match, take the largest; if none match, raise naming the extension it looked for.
- Map `metadata.emulator` → RomM platform slug via a table in the plugin repo. An unmapped emulator must raise a "needs mapping" error naming the emulator — **never** guess a platform, because silently misfiling a ROM is worse than a visible failure.

- [ ] **Step 1: Write the failing test** using a **fixture captured from the real API** (the shape is in `tests/test_archive_org.py`), covering: payload selection by `emulator_ext`; largest-wins on ties; `stream_only` refusal; unmapped-emulator error; missing-extension error. Plus a CLI test that `import` on an unknown plugin exits non-zero with a clear message.

- [ ] **Step 2: Run test to verify it fails** — `ModuleNotFoundError: No module named 'archive_org.importer'`

- [ ] **Step 3: Implement**, including the CLI commands and doc updates. Document the new `import`/`jobs` commands and the RomM connection settings (`ROMM_URL`, `ROMM_USER`, `ROMM_PASSWORD`) in the README.

- [ ] **Step 4: Verify on both platforms and against live RomM**

Run `python -m pytest -q` on Windows, then the Linux transfer + run:

```
cd <repo> && git archive HEAD -o /tmp/hub.tar && scp /tmp/hub.tar root@your-server.example:/tmp/hub.tar && ssh root@your-server.example "pct push <ctid> /tmp/hub.tar /tmp/hub.tar && pct exec <ctid> -- bash -c 'rm -rf /tmp/hub && mkdir -p /tmp/hub && tar xf /tmp/hub.tar -C /tmp/hub'"
```

```
ssh root@your-server.example "pct exec <ctid> -- bash -c 'docker run --rm --network=romm_default -v /tmp/hub:/hub -w /hub python:3.12-slim sh -c \"apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq git >/dev/null 2>&1; git config --global user.email t@t; git config --global user.name t; git config --global init.defaultBranch main; pip install -q -e .[dev] >/dev/null 2>&1; python -m pytest -q 2>&1 | tail -5\"'"
```

`tests/test_hostile_plugin.py` must still pass on Linux.

**Then one real end-to-end import**, run manually and recorded in the report: pick a small, freely-downloadable, non-`stream_only` Archive.org item, import it into the live RomM on the deployment target, and confirm it appears in the library. **Ask the operator before writing to the live library** — this is the first write this project has ever made to real data. Report the item chosen and the resulting rom id.

- [ ] **Step 5: Commit** — `git commit -m "feat(import): Archive.org importer, CLI import/jobs, docs"`

---

## Phase 2 Done Criteria

- [ ] A plugin's `FetchPlan` URLs are allowlist-gated, proven by a test using an undeclared host.
- [ ] A `filename` containing a path separator or `..` is rejected, proven by test.
- [ ] Chunked upload reconstructs a file byte-for-byte, proven by test.
- [ ] A duplicate is detected by hash and **not** uploaded, proven by test.
- [ ] Job state survives a `JobQueue` restart, proven by test.
- [ ] `stream_only` items are refused with a clear message, proven by test.
- [ ] An unmapped emulator fails visibly rather than guessing a platform.
- [ ] Windows and Linux suites both green; `test_hostile_plugin.py` still passes on Linux.
- [ ] One real ROM imported into the live RomM, with the operator's go-ahead.

## Explicitly Not in Phase 2

Web UI (Phase 3), `metadata`/`stream`/`cores` capabilities (Phase 4), federation and netplay (C/D), and filesystem confinement for plugins (still needs a mount namespace Docker denies).
