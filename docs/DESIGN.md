# ROM Hub — Design

**Status:** Phases 1–3 built; **RPP v1 fully implemented**
**Date:** 2026-07-29
**Scope of this document:** sub-projects **A** and **B** only (see [Scope](#scope)).

---

## Problem

[RomM](https://github.com/rommapp/romm) is a self-hosted ROM library manager. Its
metadata providers (IGDB, MobyGames, ScreenScraper, RAWG, LaunchBox, Hasheous,
Flashpoint, SteamGridDB, TGDB) are **hardcoded** — `backend/handler/metadata/`
contains one module per provider, each wired in by name. Adding a source means
editing core.

We want the opposite arrangement: core ships an **engine**, the community ships
**plugins**. A user pastes a URL, gets a new source, and core never changes.

This design covers a **sidecar** that adds that capability to an unmodified RomM,
plus the first plugin (Archive.org) as the proof that the contract is real.

### Why a sidecar and not a fork

The deployed RomM is a `ghcr.io/rommapp/romm:latest` container that
pulls updates. A fork means either abandoning updates or carrying a permanent
merge burden. The sidecar keeps RomM byte-identical to upstream, which also
keeps the door open to contributing the *protocol* upstream later without
having to disentangle it from a pile of local changes.

**API feasibility is confirmed.** RomM exposes 118 API paths, including
everything the Hub needs:

| Need | Endpoint |
|---|---|
| Push a ROM into the library | `POST /api/roms/upload/start` → `PUT /api/roms/upload/{id}` → `POST /api/roms/upload/{id}/complete` |
| Create missing platforms | `POST /api/platforms` |
| Group imports | `POST /api/collections`, `POST /api/collections/{id}/roms` |
| Auth | `POST /api/token`, `/api/client-tokens` |
| Dedup against existing library | `GET /api/roms`, `GET /api/search/roms` |

Zero core changes required.

---

## Scope

The original request bundles four separable products. Only **A** and **B** are
designed here; **C** and **D** are deliberately deferred but the design does not
foreclose them.

| | Sub-project | Status |
|---|---|---|
| **A** | Plugin engine + host (the extension system itself) | **designed here** |
| **B** | Archive.org plugin (search, import, metadata, stream, cores) | **designed here** |
| **C** | Cross-server federation / friend libraries | deferred |
| **D** | Multiplayer + netplay | deferred |

**Deferred is not discarded.** C and D were given a design pass specifically to
find out what they demand of the RPP contract before v1 is frozen — see
[DESIGN-federation-netplay.md](DESIGN-federation-netplay.md). The outcome:
**one addition and two reservations**, both folded in below.

Relevant existing surfaces, as actually verified:

- `/api/client-tokens/{id}/pair` + `/api/client-tokens/pair/{code}/status` — a
  device-pairing code flow.
- `/api/netplay/list` — `Get Rooms`. RomM 4.9.2 already has a netplay room
  concept.
- `/api/sync/*` — **NOT server-to-server.** Verified: this is save-state sync
  between a *client device* and the server ("the client sends its current save
  state, and the server returns operations to bring both sides in sync"). It is
  handheld/device sync and does **not** give federation a head start.

C remains a distributed-systems problem (identity, trust, partial availability,
NAT traversal) larger than A and B combined, and is not built in this pass.

---

## Two named things

Keeping these separate is deliberate — it is what makes a future upstream
contribution a clean proposal rather than "please merge my application."

- **ROM Provider Protocol (RPP)** — the versioned *contract* between a host
  and a plugin. Portable; potentially upstreamable.
- **ROM Hub** — *our* implementation of an RPP host. Stays ours.

Both were "RomM …" until the host learned to serve more than RomM. The
rename is worth one paragraph because of what it deliberately did **not**
touch.

**`RPP` is unchanged, and `rpp_version` stays `"1"`.** The acronym was
already the portable half of the name; expanding it differently is a
naming change, not a contract change. Nothing about the wire format, the
capability names, the validation rules or the manifest schema moved, so
bumping the version would tell every plugin author that something they
must react to had happened, when nothing had. A version number that cries
wolf once is a version number nobody reads again.

**The package rename is a real break for third-party plugins**, since
`from romm_hub_sdk import ...` is the one line of Hub-specific code a
plugin has. `romm_hub_sdk` therefore keeps resolving — to the *same*
module objects, via a meta-path alias rather than a second copy on the
same `__path__`, because a duplicate `FetchPlan` class would pass a smoke
test and then fail every `isinstance` check the host makes. It warns, and
it is scheduled for removal. Same for `ROMM_HUB_*` in the environment,
which is already written into shell profiles on the deployment target.

---

## The library backend

RomM is not the only self-hosted ROM library manager — [Gaseous][] and
[Retrom][] exist, and an operator running one of those wants the plugin
ecosystem just as much. All three now ship. Serving them cost far less
than it looks, and the reason is the thing this project already got right:

> **Plugins were always backend-agnostic.** A plugin returns a `FetchPlan`
> or a `MetadataPatch` — *descriptions*, never actions — and the host
> executes them. Nothing in a plugin has ever known RomM exists. The only
> RomM-specific code was the executor.

So the seam is one file: `src/rom_hub/backends/base.py` defines
`LibraryBackend`, `src/rom_hub/backends/{romm,gaseous,retrom}/` implement
it, and `ROM_HUB_BACKEND` (default `romm`) chooses. `importer.py` and
`metadata.py` name no product.

The three are more different than "another REST API" suggests. Each row
below was established from the backend's source and a live run, not
assumed:

| | RomM | Gaseous | Retrom |
|---|---|---|---|
| Transport | REST | REST | gRPC-Web over HTTP/1.1 + WebDAV |
| Import | chunked upload API | `POST /api/v1.1/Roms` multipart | no upload API — files land via WebDAV, then `UpdateLibrary` indexes them |
| Dedup | by hash (archives hashed as decompressed members concatenated) | filename (see platform-0 note) | filename only — Retrom stores no checksums |
| Collections | yes | no — `CollectionsController` is empty | no — not in the schema |
| Metadata write | yes | no — a rom exposes only GET/DELETE | yes, read-modify-write |
| Post-import | socket.io `scan` event | `ImportQueueProcessor` | `UpdateLibrary` |

Known upstream quirks the executors account for, worth recording:

- **Gaseous:** `OverridePlatformId` is stored, resolved and passed to
  `ImportGameFile` but never read in its body — the platform comes from the
  file signature, so an unrecognised ROM lands on platform 0 (measured:
  asked 13/DOS, got 0). Listing without a `PlatformId` joins a `Game` table
  absent from schema 1042 and 404s, so the backend always lists per
  platform. `ContentManagerController` is for attachments
  (screenshots/video/manuals, 50 MB cap), not ROMs, which is why it is not
  an artwork path.
- **RomM:** `/api/token` needs an explicit `scope` or every call 403s;
  `/complete` returns 201 with no body and needs a socket.io `scan` before
  the ROM exists.
- **Retrom:** needs a content directory inside `RETROM_DATA_DIR`; the
  backend probes and refuses with instructions before downloading.

[Gaseous]: https://github.com/gaseous-project/gaseous-server
[Retrom]: https://github.com/JMBeresford/retrom

### The interface was derived, not designed

Every method on `LibraryBackend` is there because `importer.py` or
`metadata.py` already called it on `RommClient`: authenticate, resolve a
platform name to an id, list a platform's roms, upload a file, get and
update a rom, ensure and populate a collection, trigger a post-upload
scan. Nothing was added on the theory that a second backend might want it.

That is deliberate, and it is the opposite of the usual instinct. An
interface invented ahead of its second implementation is an interface
shaped like its first one anyway — only with more surface to be wrong
about, and with the guesses indistinguishable from the requirements. What
Gaseous or Retrom turned out to need that was not here was added when there
was a caller for it, and the diff said which backend asked — the interface
has held, the only method-level accommodation being for Retrom's
asynchronous `scan`.

One thing is deliberately **absent**: there is no "create a platform"
method. `platform_id()` resolves a name and raises when nothing matches,
and that refusal is load-bearing — filing a ROM under a platform nobody
chose is the kind of wrong that is not noticed for months.

### `capabilities()` is what makes degradation honest

A backend declares a `frozenset` of what it supports: `import`,
`collections`, `metadata`, `artwork`, `scan`. RomM 4.9.2 has all five,
verified rather than assumed, which is exactly why it is stated as data
instead of taken for granted by every caller.

The declaration answers *when* to check. It does not answer *what to do
about the answer*, and getting that second half wrong was a real bug.
Knowing a capability is missing is not the same as refusing the operation
over it, and the two must be told apart per capability. The classification
lives in `backends/base.py` next to the capability names —
`ESSENTIAL_CAPABILITIES`, `OPTIONAL_CAPABILITIES`, `UNGATED_CAPABILITIES`
— with the reasoning against each, and a test asserts the three sets
partition `ALL_CAPABILITIES` so a capability added later cannot fall
through unclassified.

- **Essential** — `import` and `metadata`. Without `import` there is
  nowhere to put the ROM; without `metadata` an enrich writes nothing.
  These *refuse before any bytes move* (`require()`). The failure mode this
  prevents is the expensive one: `rom-hub import --collection "Shooters"`
  downloading four gigabytes, uploading them, and *then* 404ing on an
  endpoint that does not exist, with the ROM half-filed.
- **Optional** — `collections` and `artwork`. A collection groups a ROM
  that is already in the library; artwork is a cover on a record. The
  operation is complete without them, so the host does them when it can and
  *skips-and-reports* when it cannot (`degrade()`, which raises rather than
  degrade an essential capability — the guard rail on the whole policy).
  This is what makes `rom-hub import archive-org …` work against Gaseous
  and Retrom, both of which have no collections while the archive-org
  plugin names one by default with no way to clear it from the CLI. The
  skip appears in the job outcome and in `rom-hub jobs`, not only in a log
  line.
- **Ungated** — `scan`. RomM's `/complete` writes the file and creates
  **no database row**, so an explicit socket.io `scan` is what makes the
  ROM exist; a backend that indexes on receipt implements `scan_platform`
  as a no-op and simply does not declare `scan`. The pipeline never
  branches on it — it always calls, and the backend decides whether that
  means anything, which is why there is nothing to gate.

The one asymmetry: a `--collection` an operator *typed* still refuses (in
the CLI, up front), while a collection a plugin *defaulted* degrades.
Dropping boilerplate nobody chose costs nothing; silently not honouring a
name someone typed is how a library ends up unsorted with no error to
explain it.

### What did not move

The RomM findings that were expensive to establish stay exactly where
they were documented, in `backends/romm/`: the explicit `scope` on
`/api/token`, the bodyless 201 from `/complete`, the server-derived chunk
length, the empty-artwork-part 400, the paginated `GET /api/roms`. The
extraction was a pure refactor — the whole suite passed unchanged apart
from import paths and the two names the seam genuinely renamed.

---

## Architecture

```
                 reverse proxy (Traefik)
                 ├── romm.your-server.example ──→ RomM :8080   [UNMODIFIED]
                 │      └─ injects <script src="hub…/nav.js">
                 └── hub.your-server.example  ──→ Hub  :8090
                                                   │
 server  ┌─────────────────────────────────────────┼────────────────┐
         │  docker network: romm_default           │                │
         │                                         │                │
         │   RomM :8080  ◄──── RomM Adapter ◄── Hub Core            │
         │   (untouched)       (sole token holder)  │                │
         │                                          │                │
         │                        ┌─────────────────┴──┐             │
         │                        │  Broker + Fetcher  │             │
         │                        └─────────────────┬──┘             │
         │                   JSON-RPC over stdio    │                │
         │             ┌──────────┬─────────────────┴────┐           │
         │       [plugin A]   [plugin B]           [plugin C]        │
         │       subprocess   subprocess           subprocess        │
         │       no token · no mounts · not sandboxed (Phase 1)      │
         │                                                           │
         │   /mnt/library/roms     ◄── written ONLY by Hub Core         │
         │   /opt/romm-stream   ◄── cores installed by Hub Core      │
         └───────────────────────────────────────────────────────────┘
```

### Deployment

A **separate Docker Compose stack on the library host**, alongside RomM. This is still a
"separate installation" — brought up, torn down and upgraded independently —
but it shares the `romm_default` docker network, so API calls have no extra
network hop, and it already has the `/mnt/library` mounts and `/opt/romm-stream`
that the import and stream capabilities need.

Paths and the RomM base URL are config-driven, so relocating to its own LXC
later is a config change, not a rewrite.

### State

The Hub owns a **SQLite database** for plugin registrations, per-plugin config,
install/pin records and job history. **RomM's MariaDB is never touched.** This
is what keeps RomM upgrades from breaking the Hub and vice versa.

As built: plugin registrations and pins are `$ROM_HUB_HOME/state.json`, the
import job queue is `$ROM_HUB_HOME/var/jobs.db`, and in-flight downloads are
`$ROM_HUB_HOME/var/downloads/<job-id>/`. `ROM_HUB_HOME` defaults to
`~/.rom-hub` and is what points all of it at the deployment target's own
storage rather than at a workstation system drive.
The pre-rename `ROMM_HUB_HOME` is still read (deprecated), and an existing
`~/.romm-hub` directory is preferred over a `~/.rom-hub` that does not
exist yet — a rename must not relocate an operator's installed plugins
and job history without saying anything.

### RomM connection settings

The Hub reads its RomM credentials from the environment — `ROMM_URL`,
`ROMM_USER`, `ROMM_PASSWORD` — never from a file in the repo. All three are
required by `rom-hub import`, which names whichever are missing and stops
before opening a connection.

Because `subprocess.Popen` copies the parent environment into every child,
putting credentials in the environment means the broker must control what a
plugin subprocess inherits. Otherwise "the plugin never holds the RomM token"
would be a statement about the API surface only: `os.environ["ROMM_PASSWORD"]`
requires no socket, no file, and no syscall the seccomp filter can observe.

**The environment is default-deny, like the manifest and the netpolicy.** The
child's environment is built from `{}` and only `SAFE_ENV_VARS` are added —
`PATH`, plus the handful of platform variables a Python interpreter needs to
start (`SYSTEMROOT`/`COMSPEC`/`PATHEXT`/`TEMP`/`TMP` on Windows,
`HOME`/`TMPDIR` on POSIX), plus `PYTHONIOENCODING=utf-8` set by the host so the
JSON-over-pipes protocol does not depend on the ambient locale. No
`PYTHONPATH`, no `PYTHONHOME`, nothing user-defined.

This was first written as a denylist of the three `ROMM_*` names, and that was
wrong in a way worth recording: it blocked exactly the three names someone had
thought of and passed through 92 others, including a real GitHub token and a
DeepSeek API key that happened to be in the developer's shell. A denylist
cannot work for a namespace anyone can add to, and unlike a socket or a path
there is no second line of defence behind the environment — seccomp cannot see
it at all. Measured: 92 variables visible to a plugin before, 7 after.

Two things this still does not do. It does not close the class — a plugin can
read the Hub's `/proc/<pid>/environ`, so credentials cannot be *handed* to a
plugin but can still be *gone looking for*. And there is deliberately no
mechanism for a plugin to request a variable; if one is ever needed it should
be an explicit, reviewable manifest grant like `permissions.network`, not an
inherited leak.

### CLI surface

| Command | Does |
|---|---|
| `rom-hub plugin browse\|install\|list\|enable\|disable` | plugin lifecycle |
| `rom-hub plugin assets <slug> [--fetch]` | the data assets a plugin declares, whether they are cached, and (with `--fetch`) get them now |
| `rom-hub search <query> [--platform] [--limit] [--offset] [--per-source] [--expand] [--all-variants] [--no-group]` | fan out across enabled `search` plugins, then **merge, group and page** the combined set — see [Search that scales](#search-that-scales) |
| `rom-hub import <plugin> <source_id> [--platform] [--collection]` | plan → download → dedup → upload → collection |
| `rom-hub enrich <plugin> <rom_id> [--source-id]` | enrich → fetch artwork → `PUT /api/roms/{id}` |
| `rom-hub stream <plugin> <source_id> [--open] [--json] [--platform] [--server]` | resolve one item to a validated stream target and hand it over: print what to do with it, `--open` it, or emit it as JSON for a launcher |
| `rom-hub stream --library-rom <id> [--open] [--json]` | hand over the active backend's own in-browser player for a rom the library already holds. No plugin, no subprocess, no connection |
| `rom-hub cores list\|install <plugin> [<core>]` | list a plugin's cores, or download one into the configured cores directory |
| `rom-hub firmware list\|install <plugin> [<firmware>] [--no-library]` | list a plugin's BIOS/firmware **with each item's licence**, or install one into the configured firmware directory and the library |
| `rom-hub assets list|install <plugin> [<asset>] [--kind]` | list a plugin's support files **with each item's licence**, or download one into the directory configured for its kind. No library server is involved |
| `rom-hub catalog list\|add\|remove\|refresh` | the ordered list of plugin directories the Hub reads. The bundled one is always first and cannot be removed; anything added is an https URL or a local path, and comes after it — see [The plugin directory is plural, and none of it is trusted](#the-plugin-directory-is-plural-and-none-of-it-is-trusted) |
| `rom-hub jobs [--state]` | the persisted import queue, with failure reasons |

`--source-id` exists because RomM does not record which plugin an import came
from, so a `metadata` plugin cannot in general work out which of its own items
a rom is. A plugin that refuses to guess (Archive.org does) says so and names
the flag.

`--platform`/`--collection` are applied **host-side, after** the plugin's plan
has been validated and its URLs allowlist-checked, so they retarget where a ROM
files and cannot widen where bytes come from. They deliberately cannot rescue a
plugin's refusal: a "needs mapping" emulator is fixed by adding the mapping,
not by naming a platform once and leaving the gap for the next operator.

### Where things live

By convention: source is small and lives in git; anything that grows lives on
the deployment target's own storage, never on a workstation system drive.

| Thing | Location |
|---|---|
| Hub source, this doc | git repo (small — Python + Markdown) |
| Plugin git clones | deployment target `/opt/rom-hub/plugins/` |
| In-flight downloads | deployment target `/opt/rom-hub/var/downloads/` |
| Fetched artwork, in transit | `$ROM_HUB_HOME/var/artwork/<rom_id>/` |
| Plugin data assets | `$ROM_HUB_HOME/var/plugin-data/<slug>/` |
| Configured plugin directories | `$ROM_HUB_HOME/catalog-sources.json` — beside `state.json`, because it is configuration somebody typed and must survive clearing a cache |
| Fetched directories, cached | `$ROM_HUB_HOME/var/catalogs/<hash of the URL>.json` — keyed on the location, so renaming a source cannot serve it another source's bytes |
| Imported ROMs | `/mnt/library/roms` (RomM's existing library) |
| Harvested cores | `$ROM_HUB_HOME/var/cores/` by default; `ROM_HUB_CORES_DIR` points it at `/opt/romm-stream/cores` on the deployment target |
| Installed firmware | `$ROM_HUB_HOME/var/firmware/<slug>/` by default; `ROM_HUB_FIRMWARE_DIR` points it at whatever `system/` or `bios/` directory the operator's emulator already reads |
| Installed assets | `$ROM_HUB_HOME/var/assets/<kind dir>/<slug>/` by default, where the kind dirs are RetroArch's own names (`shaders`, `overlays`, `cheats`, `autoconfig`); `ROM_HUB_ASSETS_DIR` moves the root — point it at a RetroArch config directory and every file lands where RetroArch looks — and `ROM_HUB_{SHADERS,OVERLAYS,CHEATS,CONTROLLERS}_DIR` each move one kind outright |

Plugin data assets are **not** in the plugin's own directory, and that is not
a style choice: `plugins/<slug>/` is a git checkout the registry deletes and
replaces on every reinstall, so a 42 MiB cache kept there would be
re-downloaded every time a plugin was updated — and would put a dataset inside
a tree the update diff is supposed to be able to review.

The cores path is **configuration, not a constant**. Compiling
`/opt/romm-stream/cores` into the Hub would put a plugin-supplied download
outside `ROM_HUB_HOME` on every host that is not the deployment target —
including a developer workstation, where it would land on the system drive.

`.gitignore` enforces this.

### Operational risk: watchtower

The deployment target runs **watchtower**, which auto-updates containers — including RomM
(currently `rommapp/romm:4.9.2`). A sidecar depends on RomM's API shape, so an
unattended major-version jump can break the Hub with no human in the loop.

Two mitigations, both cheap, and they compose:

1. Pin RomM in watchtower (or exclude it via
   `com.centurylinklabs.watchtower.enable=false`) so upgrades are deliberate.
2. The RomM adapter records the API version it negotiated at startup and
   **fails loudly with a clear message** on an unexpected shape, rather than
   failing obscurely mid-import.

This risk is an argument *for* the sidecar, not against it: a fork would have
the same exposure plus a permanent merge burden.

---

## The plugin contract (RPP v1)

### A plugin is a git repository

Install is `git clone` **pinned to a tag or commit**. Update is an explicit
`git fetch` + re-pin — never automatic — and the **manifest diff is shown before
the update is accepted**, so a plugin cannot silently widen its own permissions.

### `manifest.toml`

```toml
[plugin]
slug        = "archive-org"
name        = "Archive.org"
version     = "1.0.0"
rpp_version = "1"
license     = "MIT"

[capabilities]                              # declare only what you implement
search   = "archive_org.search:Search"
importer = "archive_org.importer:Importer"
metadata = "archive_org.metadata:Metadata"
stream   = "archive_org.stream:Stream"
cores    = "archive_org.cores:Cores"
firmware = "open_bios.firmware:Firmware"     # clean-room BIOS, licence stated

[permissions]
network  = ["archive.org", "*.archive.org"]
romm_api = ["roms:create", "platforms:read", "collections:write"]

[[data_assets]]                             # optional; the host fetches these
name       = "catalogue.sqlite"             # what the plugin opens
url        = "https://archive.org/download/x/catalogue.zip"
sha256     = "…64 hex characters…"          # of `name`, and mandatory
size_bytes = 9118645                        # of the download, for the notice
archive    = "zip"                          # optional
member     = "catalogue.sqlite"             # required when `archive` is set

[config]                                    # user-editable schema
collections = { type = "list[str]", default = ["softwarelibrary"] }
api_key     = { type = "secret" }           # never in state.json; no default allowed
```

### Capabilities

| Capability | Method | Returns | What the host does with it |
|---|---|---|---|
| `search` | `search(query, platform, limit)` | `SearchResult[]` | merges, dedups against the library |
| `importer` | `plan(result)` | `FetchPlan` — URLs, target platform, filenames | downloads, hashes, uploads, files under a platform |
| `metadata` | `enrich(rom_ref)` | `MetadataPatch` | fetches the artwork, `PUT /api/roms/{id}` |
| `stream` | `resolve(result)` | `StreamTarget` | validates it, then routes it to a **handover**: a `url` is opened in the operator's browser, a `handle` is printed for the service that issued it. Opens nothing else, streams nothing |
| `cores` | `list()` / `plan(core)` | `CoreArtifact[]` / `FetchPlan` | downloads into the configured cores directory |
| `firmware` | `list()` / `plan(firmware)` | `FirmwareArtifact[]` / `FetchPlan` | downloads into the configured firmware directory, unpacks the declared archive members, and stores the files in the library where the backend can hold firmware |
| `assets` | `list()` / `plan(asset)` | `AssetArtifact[]` / `FetchPlan` | downloads into the directory configured for the item's `kind` — shaders, overlays, cheats, controller profiles. Touches no library at all |

**RPP v1 is fully implemented.** All seven capabilities have a host
implementation, a CLI command and tests that exercise them through a real
plugin subprocess.

**Reserved, unimplemented in v1:** `peer`, `netplay`. Reserved so that
sub-projects C and D cannot collide with a v1 name later.

A minimal plugin implements `search` + `importer` only — the ~50-line
community contribution that makes this worth building. Archive.org implements
four: `search`, `importer`, `metadata` and `stream`. It does **not** implement
`cores`, and that is a correction to an earlier draft of this document rather
than an omission — see [The Archive.org plugin](#the-archiveorg-plugin).

### Each capability's security gate

The invariant is the same one `importer` established, applied without
exception: **a plugin returns a description; the host performs every
privileged action.** Concretely, every URL a plugin hands back — in any
capability — passes `netpolicy.check_url` against that plugin's declared
allowlist before the host fetches it.

| Capability | What the plugin returns | The gate |
|---|---|---|
| `search` | result metadata only | nothing to fetch; `ctx.http` was already gated |
| `importer` | `FetchPlan` | `check_url` per file, in `PluginProcess.plan()`; `FetchFile` validates every filename; `dest_in_job_dir` contains every write |
| `metadata` | `MetadataPatch` | `check_url` on `artwork_url` in `PluginProcess.enrich()` **and** again in `metadata.run_enrich()`; the artwork filename goes through the same `bare_filename` and `dest_in_job_dir`; the RomM form-field names are an allowlist |
| `stream` | `StreamTarget` | `check_url` when `kind="url"` in `PluginProcess.resolve_stream()`, **again** in `stream.plan_handover()` when the route is decided, and a **third** time in `stream.open_handover()` immediately before a browser is launched; a `kind="handle"` may not *be* a URL, so the discriminator cannot be lied about to skip the check |
| `cores` | `CoreArtifact[]`, `FetchPlan` | the **same** `_gated_plan()` the importer uses — one implementation, so the two cannot drift |
| `firmware` | `FirmwareArtifact[]`, `FetchPlan` | the same `_gated_plan()` again. Plus: every archive member goes through `bare_filename` on the *type*, and is matched against the zip by full-name equality and written to a destination the host built with `dest_in_job_dir` — an entry named `../../etc/passwd` is simply not one of the members, and is never joined onto a path |
| `assets` | `AssetArtifact[]`, `FetchPlan` | the same `_gated_plan()` a third time. `kind` is a closed `Literal`, so the host can always choose a destination; the `asset_id` may contain `/` because it is a path *within the source tree* and is never joined onto a filesystem path — every write is built from a `FetchPlan` filename, which `bare_filename` and `dest_in_job_dir` still gate |
| *(any)* `[[data_assets]]` | nothing — it is a manifest declaration, not a return value | `check_url` at **parse** time against `permissions.network`, so a violating manifest cannot be installed; then `HttpDownloader`'s per-hop `check_url` at fetch time; then a mandatory `sha256` before the plugin is told the path |

Three things about that table are deliberate.

**`metadata`, `cores`, `firmware` and `assets` were the same hole as `importer`.** An artwork
URL and a core download URL are both "a string a plugin chose, which the host then
fetches with its own network access". Adding either without a `check_url` on it
would have made the manifest's `network` declaration decorative for that
capability, which is why the broker's module docstring enumerates the paths out
and why each one is tested with an undeclared host.

**Filename validation is reused, not re-implemented.** `bare_filename()` and
`dest_in_job_dir()` were extracted (to `types.py` and the new `paths.py`) when
the second and third callers appeared. A copy would have been a second place
for the rule to be subtly different, and the containment check is exactly the
kind of code where "subtly different" means "absent".

**`metadata`'s worst case is not an escape.** It is a *faithful* write. RomM's
update endpoint applies the record it is given, so a plugin that only knows the
name would erase every curated id if unset fields were forwarded as empty form
parts. `MetadataPatch.form_fields()` emits nothing for an unset field, and a
test fails if that stops being true. Measured against a real RomM 4.9.2: a
name-only `PUT` left an existing `igdb_id` untouched, and an *empty* artwork
part is a `400` — so, unlike `ensure_collection`, an artwork-less update must
carry no artwork part at all.

**`stream` resolves and hands over. It is not a streaming server.** See
[What `stream` does, and what it refuses to do](#what-stream-does-and-what-it-refuses-to-do).

### What `stream` does, and what it refuses to do

For a while `stream` ended at the host gate: the plugin's answer was
validated and printed. That is a *contract*, not a capability — an operator
holding a validated URL still had to work out what to do with it. The host
side now lives in `rom_hub.stream`, shaped like `cores`/`firmware`/
`emuassets`: the plugin describes, the host acts, and what the host will act
on is a closed set.

| The target | The handover | What `--open` does |
|---|---|---|
| `kind="url"` | `browser` | opens it. For the case that exists today this **is** playback: an Archive.org `/details/` page runs Emularity in the page |
| `kind="handle"` | `handoff` | refuses. The Hub does not know which service issued the identifier and will not guess a URL around it |
| *(no plugin)* `--library-rom <id>` | `browser` | opens the library's own in-browser player — `<backend base>/rom/<id>/ejs` — built entirely from the operator's settings |

`--json` emits the same handover for a launcher, a TV app or another command
to consume: the target *plus* the host's decision about it, so a consumer
never has to re-derive the route from `kind` and thereby re-implement the
part that carries the security reasoning.

**The library player table has one entry, and that is honest rather than
lazy.** RomM's `/rom/<id>/ejs` is there because it is *verified* — it is the
URL `romm-stream` itself drives when it autoplays a library rom. The other
backends have no player path this project has confirmed, and a guessed URL
is worse than a refusal: it opens, it 404s, and the operator cannot tell
whether the guess or their library is at fault. `rom-hub stream
--library-rom` against those backends says so and names the one that works.

#### `romm-stream` is asked, never driven

`romm-stream` is the streaming server. Nothing in the Hub becomes a second
one: `rom_hub.stream` imports no `subprocess`, no `socket` and no `asyncio`,
and a test reads its import graph and fails if that changes.

With `--server` (or `$ROM_HUB_STREAM_SERVER`) the Hub asks a `romm-stream`
server whether it could play a platform, over the only two endpoints that
answer a question rather than start work:

* `GET /api/play/route?platform=<slug>` → `{"tier": "local"|"stream"}`, or a
  404 carrying the server's own reason
* `GET /api/play/streamable` → the slugs it can serve

`StreamServerClient.ALLOWED_PATHS` is exactly those two and is asserted by a
test, so adding a third is a visible change. This is optional in
`backends.degrade`'s sense: the operator's answer — the resolved target — is
already in hand, so a stream server that is down or misconfigured produces a
`note` line and never a failed command.

**What cannot be done, stated plainly.** The Hub cannot start a `romm-stream`
session for a plugin-resolved target, and that is a property of the server's
API rather than a decision taken here. Its session routes are
`POST /api/stream/start`, `POST /api/rtc/offer` and `GET /api/rtc/signal`,
and each of them takes either a `platform` plus a `rom_name` that must
resolve to a file inside the stream server's *own* ROM directory, or a
`romm_rom_id` plus RomM credentials; `start`'s `url` form is gated on a
hardcoded origin allowlist that a plugin's host would not be in. There is no
route that accepts an arbitrary resolved URL or an opaque handle. So for an
Archive.org target there is nothing to hand `romm-stream`, and a Hub command
that appeared to hand it one would be a wrapper pretending to be an
integration. Playing a *library* rom through `romm-stream` would need the
Hub to forward RomM credentials to a second service, which is a design
question and not a missing function call.

#### Known gap: `romm-stream` has no TURN relay

Not a Hub bug, and recorded here because it is the thing that actually stops
remote streaming working.

`romm-stream`'s `webrtc.py` hardcodes a single public STUN server. STUN
*discovers* a peer's reflexive address; it cannot relay. Behind symmetric NAT
or CGNAT — which is where a remote client sits — both ends gather candidates
that never pair, and the failure is indistinguishable from a bad network. A
`coturn` container has been running unused on the same host.

`docs/patches/romm-stream-ice-servers.patch` makes the ICE list
configuration instead of a constant (`ROMM_STREAM_ICE_SERVERS`, or
`ROMM_STREAM_STUN_URLS` / `ROMM_STREAM_TURN_URLS` / `ROMM_STREAM_TURN_USER` /
`ROMM_STREAM_TURN_PASS`), with the previous behaviour as the default so the
file alone changes nothing until a relay is configured. It refuses to add a
TURN URL that has no credentials, and says why: an unauthenticated allocation
against a `lt-cred-mech` coturn is refused outright, so a half-configured
relay looks exactly like no relay.

Verified against a copy of the service on a spare port, not the live one:
without it the offer carried `host` and `srflx` candidates only; with it the
same offer carried a `typ relay` candidate at the site's external address.
**The patch is not applied** — it is the service owner's to apply.

### Firmware: the capability where the licence is the product

Every emulation setup needs BIOS files, and the honest problem with BIOS
files is not where to find them — it is whether you are allowed to have
them. So `firmware` is shaped by that question rather than by the download.

**`FirmwareArtifact.license` is a required field.** Not optional, not
defaulted, not inferable. A plugin cannot list a BIOS without saying what
it is, and `rom-hub firmware list` prints it as a column beside the
platform. The Hub cannot check the claim — a dumped BIOS and a clean-room
reimplementation are identical bytes on the wire — and does not pretend
to. What a type system *can* do is make silence impossible, and that is
what this does.

**`FirmwareArtifact.platform` is required too**, where `CoreArtifact.system`
is optional, because firmware is keyed by platform and the upload resolves
it against the library's own platform list. The plugin-side rule is the
same "needs mapping" rule the ROM path uses, and it is stricter here for a
reason worth stating: a ROM under the wrong system is *visibly* wrong — it
is in the library, under a heading that looks odd. A BIOS under the wrong
system is *invisible*. The emulator that needed it goes on reporting that
it has no BIOS, and nothing anywhere connects the two.

**Why `FetchPlan` and not `[[data_assets]]`.** They look close: both are a
host-performed, verified, cached download the plugin only describes. Four
differences decide it, and they are all about who chooses.

* A data asset is the *plugin's own* file. It lands in
  `var/plugin-data/<slug>/` and the plugin is handed the path. Firmware is
  for the operator's emulator and their library; the plugin never sees it.
* A data asset is resolved before **every** command, `search` included. An
  operator running a search should not be pulling BIOS files.
* The set is fixed at install time, and `MAX_DATA_ASSETS` is 8. Firmware is
  chosen one item at a time off a catalogue the operator just read — the
  same shape as `cores install <plugin> <core>`.
* The mandatory manifest `sha256` pins a plugin release to an upstream
  release. Right for a dataset published once; wrong for firmware tracked
  across upstream tags, and it leaves a plugin unable to answer "what is
  available now".

What data assets *did* contribute is their zip handling, reimplemented in
`rom_hub.firmware` for a list of members rather than one. Archive support
is not decoration: the open firmware that actually gets published is
published inside zips. SameBoy's boot ROMs ship only inside its emulator
release, so the plugin declares `archive = "zip"` plus the members it
wants and the host keeps exactly those — matched by full-name equality,
written to destinations it built itself, bounded against a bomb, and the
archive deleted afterwards so nothing an emulator scans that directory has
to ignore is left behind.

**The library half is `OPTIONAL`, and that is the whole design.** A BIOS is
installed the moment it is in a directory an emulator reads — which is why
this capability is modelled on `cores`, and `cores` never touches a library
at all. Filing it in RomM as well is a second home for bytes that are
already installed. Refusing to fetch a legally-clean Game Boy boot ROM
because the *library server* has no firmware table would be refusing the
job over the garnish, in exactly the way `--collection` once refused every
Gaseous import. So the download happens, the upload is skipped, and the
line the operator reads says which.

An **unconfigured** backend is a different thing and is treated differently:
that is a question, not a limitation the Hub can see, so it refuses and
names `--no-library`. Guessing "they meant local only" is how an operator
ends up wondering why the library never got the file.

**What each backend can actually do**, read out of their sources rather
than assumed:

| Backend | Firmware | Evidence |
|---|---|---|
| RomM | read **and** write | `backend/endpoints/firmware.py`: `add_firmware` (`POST`, `Scope.FIRMWARE_WRITE`, `files: list[UploadFile]`), `get_platform_firmware`, `get_firmware_identifiers`, `get_firmware`, `get_firmware_content`, `delete_firmware` |
| Gaseous | read **only** | `Controllers/V1.0/BiosController.cs` — four routes, all reads. Its only ingestion is `ProcessQueue/Tasks/ImportQueueProcessor.cs` calling `Bios.BiosHashSignatureLookup(md5)` against a fixed table of retail-dump MD5s in `Support/PlatformMap.json`, which a clean-room replacement cannot match by construction |
| Retrom | neither | no BIOS or firmware message, service, column or directory anywhere in the repository; the only `bios` matches are EmulatorJS's own `biosUrl?: string` in `packages/client-web/src/lib/emulatorjs/` |

One measured detail worth knowing before it looks like a bug: RomM records
a clean-room BIOS with `is_verified: false`. `Firmware.verify_file_hashes`
compares against known **retail dump** hashes, so a replacement BIOS is
correctly stored and correctly reported as not matching a dump. That is
RomM answering a different question, not a failed upload.

### Assets: the rest of an emulation stack, and the first backend-free install

`cores` gets you an emulator and `firmware` gets you a BIOS. Neither is why a
twenty-year-old game looks wrong on a modern panel, why the screen has black
bars either side of it, why the pad you plugged in does nothing, or why you
are typing Game Genie codes by hand. That is shaders, overlays, controller
profiles and cheat files — collectively the largest part of a working setup
that the Hub did not serve.

**One capability, not four.** These differ in exactly one respect the Hub
cares about: which directory an emulator reads them from. That is a lookup,
not four code paths. So `AssetArtifact` carries a `kind` — `shader`,
`overlay`, `cheat`, `controller` — and the host maps it to a directory. The
vocabulary is closed, because the host must be able to choose a destination
for every value it accepts; a plugin inventing `kind = "config"` would be
asking the host to invent a destination, and "somewhere sensible" is not a
destination anybody can audit.

`shader` is in that list with no plugin behind it, deliberately — see the
licence findings below. It is a thing RetroArch has a directory for, and a
differently-licensed shader source should be able to ship without the host
learning a new word first.

**Where the bytes go is configuration, with a default worth adopting.**
`kind` selects a leaf directory under `ROM_HUB_ASSETS_DIR`
(`$ROM_HUB_HOME/var/assets` by default), then one directory per plugin:

    shader     -> <root>/shaders      overlay    -> <root>/overlays
    cheat      -> <root>/cheats       controller -> <root>/autoconfig

Those leaf names are RetroArch's own, so pointing `ROM_HUB_ASSETS_DIR` at an
existing RetroArch configuration directory lands every file exactly where
RetroArch already looks. That is a default an operator can adopt, not a path
compiled in — and `ROM_HUB_SHADERS_DIR`, `ROM_HUB_OVERLAYS_DIR`,
`ROM_HUB_CHEATS_DIR` and `ROM_HUB_CONTROLLERS_DIR` each override one kind
outright, for the setup whose cheats and shaders do not share a parent.

#### This is the first capability with no backend dimension at all

`install_asset` takes no `backend` argument, opens no connection, and
`rom_hub.emuassets` does not import `rom_hub.backends` — all three asserted by
tests rather than by inspection. So the essential-vs-optional question has no
answer here rather than an answer of "nothing": both halves of that scheme
presuppose a backend method that might be missing.

`backends/base.BACKEND_INDEPENDENT_CAPABILITIES` records the set, and the
catalog's classification test asserts against it instead of repeating its
members. `cores` was already in it and had never been written down, because a
lone exception reads as an oversight; two make a category. The practical
statement is that `rom-hub cores install` and `rom-hub assets install` work
identically against RomM, Gaseous, Retrom **and against no configured backend
at all** — an operator with no library server can still install a CRT bezel.
This is also why neither appears in [the proof matrix](PROOF.md), which is
about what backends do.

#### Size was the design problem, and nothing clones a repository

The candidate sources are enormous: `libretro-database` is 795 MB,
`slang-shaders` 139 MB, `glsl-shaders` 56 MB, `common-overlays` 29 MB. The
rule the three shipped plugins hold to is **list from an index, fetch one
file**.

That index is GitHub's Git Trees API, one directory at a time
(`/git/trees/<ref>:<path>`), which answers with a compact JSON list carrying
each blob's path and size. One 12 KB call enumerates libretro-database's 44
cheat platforms; one 704 KB call enumerates all 2,265 NES cheat files; one
732 KB recursive call classifies the entire overlay repository. An install is
then a single `raw.githubusercontent.com` GET for a file of a few hundred
bytes to a few kilobytes.

**The contents API is the wrong endpoint and fails silently.** `/contents/`
truncates a directory listing at 1,000 entries with no error and no flag — the
NES cheat directory returns 1,000 of 2,265 files and answers `200`. A plugin
built on it would have offered a third of the catalogue and looked like it was
working. The Trees API returns all of them, sets `truncated` when it cannot,
and is *smaller* on the wire (704 KB against 1.4 MB) because it carries no
per-entry URL block. The plugins refuse a truncated listing outright rather
than showing part of a catalogue as though it were all of it.

**Why not `[[data_assets]]`?** For the four reasons `firmware` gives, each of
which bites harder here: a data asset is the *plugin's* file and these are the
operator's; it is fetched before every command, so `rom-hub search` would pull
bezels; the set is fixed at install time and capped at 8, against catalogues
of 437 and 2,265; and its mandatory sha256 pins the manifest to an upstream
commit, which for repositories that take contributions continuously would mean
a plugin release every time somebody upstreams a gamepad.

#### Licensing decided the plugin set, and dropped the most-wanted one

Every source was verified by reading the repository's own licence file, not
GitHub's summary of it. Three shipped:

| Source | Licence | How it was established |
|---|---|---|
| `common-overlays` | **CC-BY-4.0** | full CC-BY-4.0 text in `COPYING`; GitHub agrees |
| `retroarch-joypad-autoconfig` | **MIT** | `COPYING` states MIT for the profiles; GitHub reports NOASSERTION only because that file *also* carries the zlib-style SDL licence for a bundled `gamecontrollerdb.cfg` this plugin does not offer |
| `libretro-database` (`cht/`) | **CC-BY-SA-4.0** | full text in `LICENSE` at the repository root, no carve-out naming `cht/` |

**`slang-shaders` and `glsl-shaders` were dropped**, and they were the single
most-wanted item — CRT filters are the reason most people install shaders at
all. Neither repository has a `LICENSE` or `COPYING` of any kind; GitHub's
licence endpoint returns 404 for both, and the READMEs say nothing. Per-file
headers are inconsistent where they exist at all: `crt-lottes.slang` is
dedicated to the public domain by its author, `crt-geom.slang` and
`crt-geom.glsl` are GPL-2.0-or-later, and `stock.slang`, `stock.glsl` and
`handheld/shaders/lcd-cgwg/lcd-grid.slang` carry no statement whatsoever.
Guix's packaging of the same tree enumerates fourteen distinct licences, which
is the same finding reached independently from outside.

A file with no licence statement is not permissive by default — it is "all
rights reserved". `AssetArtifact.license` is a required field precisely so a
plugin cannot stay silent on this, and there is no honest value to put in it
for a tree where the answer varies per file and is frequently absent. So the
shaders are not shipped, and this paragraph is why. That is the same call this
project already made for BIOS projects and content sources, and an honest
three-plugin set beats four with a question mark.

#### The overlay format did not fit, and the rule won

A RetroArch overlay is a `.cfg` plus the images it references, named relative
to the `.cfg` — overwhelmingly as a subdirectory (`img/dpad-left.png`). A
`FetchPlan` cannot express that: `FetchFile.filename` must be a bare name,
which is the rule that keeps a plugin's downloads inside the directory chosen
for them.

Widening it would have traded a containment guarantee for a file layout, so
the plugin narrowed instead: it offers only the **self-contained** overlays,
49 of the repository's 310, including the whole `gamepads/lite/` pack. Listing
an overlay that would fail to install is worse than not listing it.

Making that filter cheap is the other half. Reading 310 `.cfg` bodies would be
310 requests for a *catalogue*, so the tree itself is the predictor — an
overlay is self-contained exactly when its own directory also holds images —
and that heuristic was checked against the content of all 310 files, agreeing
on every one with no false positives and no false negatives. `plan()` still
fetches the chosen `.cfg` and re-reads its references before planning
anything: the heuristic decides what to *offer*, the file decides what to
*install*.

### Data assets: a dataset the plugin cannot fetch itself

Some sources are not services. OpenVGDB publishes **no API at all** — its
repository holds a `.gitignore` and a 28-byte `README.md`, and the entire
project is one 9,118,645-byte SQLite database attached to a GitHub release,
last published 2021-11-11. A plugin backed by a source like that needs the
file, and RPP v1 as originally shipped gave it no way to get one. Four host
facts, each independent and each fatal on its own:

1. **Size.** `ctx.http` caps a response at `MAX_RESPONSE_BYTES` — 4 MiB — and
   refuses on `Content-Length` before a body byte is pulled.
2. **Encoding.** `HttpResponse` carries `text`, decoded with
   `errors="replace"`. There is no byte channel; a zip under the cap would
   still arrive irrecoverably mangled.
3. **Redirects.** The release asset answers `302` to
   `release-assets.githubusercontent.com`. `broker/fetcher.py` sets
   `follow_redirects=False` — correctly, since a redirect could escape the
   allowlist — and exposes no `Location`, so a plugin sees `302` and cannot
   learn where to.
4. **Nowhere to cache it.** A plugin subprocess is started per command and
   dies with it. "Download it once and keep it" had no storage to use.

The capability existed on the host side the whole time: `HttpDownloader`
already streams multi-GB files with per-hop redirect re-validation, resumable
ranges and filename containment. It was reachable only *into the library*, as
part of an import. Data assets are that same capability made reachable for a
plugin's own data — and nothing about the boundary is relaxed to do it.

```toml
[[data_assets]]
name        = "openvgdb.sqlite"                       # what the plugin opens
url         = "https://github.com/.../v29.0/openvgdb.zip"
sha256      = "a6df8311ff188d41...e075b601"           # of `name`, mandatory
size_bytes  = 9118645                                 # of the download
archive     = "zip"                                   # optional
member      = "openvgdb.sqlite"                       # required with `archive`
description = "OpenVGDB v29.0: 51,742 roms, 40.3 MiB unpacked"
```

**Declared, not requested — and that is the whole design.** The obvious
alternative is `ctx.download(url)`. It was rejected: a runtime request is not
reviewable. A declaration is a line in a manifest that a human reads before
installing, that `rom-hub plugin install` prints, that appears in the update
diff the registry shows before a new version is accepted, and that a catalog
can carry. `ctx.download(url)` is a string constructed at a moment nobody is
watching, and it would let any plugin pull arbitrary megabytes from any host
in its allowlist at any time. This is the same reasoning `permissions.network`
already embodies, applied to a second kind of traffic.

**Gated exactly like a `FetchPlan` URL, twice.** `manifest.py` refuses at parse
time an asset whose host is not in `permissions.network` — so the allowlist a
reviewer reads is a complete account of where the plugin causes traffic, and a
manifest that violates it cannot be installed at all. Then the fetch reuses
`importer.HttpDownloader`, so httpx follows nothing and each hop is re-checked
with `check_url` before the next request goes out. That is not hypothetical
here: the GitHub asset really does redirect to a different host, which is why
`release-assets.githubusercontent.com` is a *separate declared entry* rather
than something the download is allowed to reach implicitly.

**Integrity is mandatory, and there is no trust-on-first-use.** The declared
`sha256` is the digest of the file the plugin opens — the extracted member
when `archive` is set, the downloaded bytes otherwise. The host verifies
*before* the plugin is told the path, refuses on mismatch with nothing cached
(neither the wrong file nor the partial download a resume would build on), and
**re-verifies a cached copy on every use** rather than assuming it. A cache
directory is a directory on a machine; "it was correct when we fetched it" is
not a statement about the bytes that are there now. Without this, a 9 MB blob
from the network would be feeding the names and covers written into a
library — a supply chain the operator never agreed to.

**A path, not bytes.** 42 MiB will not cross an 8 MiB JSON frame, buffering it
would cost several times that in host memory, and SQLite cannot mmap a
bytestring. The plugin receives `ctx.data_assets` (`{name: path}`) and opens
the file itself, read-only.

**A separate size budget, deliberately larger.** `MAX_DATA_ASSET_BYTES` is
128 MiB, enforced on the download (declared length *and* while streaming, since
the header is a hint) and again on unpacking (declared `file_size` *and* while
decompressing, since a zip's header is written by whoever built it).
`ctx.http`'s 4 MiB cap is **not** raised: it exists because that body is
buffered in host memory and JSON-escaped into a reply frame that must stay
under `protocol.MAX_MESSAGE_CHARS`. An asset enters neither. Raising the one to
serve the other would have made every plugin response 128 MiB-shaped.

**Containment is reused, not re-implemented.** The asset `name` and the archive
`member` go through `types.bare_filename` — the same validator a `FetchPlan`
filename uses, which is what refuses `C:evil.sqlite` — and every path is joined
with `paths.dest_in_job_dir`. A zip entry name is never joined onto anything,
so an archive whose entries are called `../../etc/passwd` has nowhere to write.

**Announced before it happens, three times over.** `rom-hub plugin install`
prints the size, the origin and the digest at the moment the operator is
deciding. The fetch itself writes one line to stderr — size, full URL, digest
prefix, cache location — *before* the request. `rom-hub plugin assets <slug>`
shows what is declared and whether it is cached; `--fetch` performs the
download deliberately and on its own. And `ROM_HUB_NO_ASSET_FETCH=1` refuses
the download outright, naming the command that would perform it. A silent
multi-megabyte download on somebody's first search would be a bad surprise, and
none of the four paths above allows one.

**`rpp_version` stays `"1"`.** The contract did not break. `[[data_assets]]` is
optional and absent from all ten shipped plugins, which parse and run
untouched; `PluginContext` gained a field with a default; the `init` handshake
gained a key an older SDK ignores. A version bump would force every existing
plugin to re-declare something that did not change, which is the opposite of
what a version number is for.

**Not covered by this, on purpose.** There is no update mechanism: a new
dataset is a new `sha256` in a new plugin version, reviewed like any other
manifest change. There is no writable scratch directory for plugins — the data
directory holds host-verified files and nothing else, because a plugin that
could write there could invalidate the verification. And only `zip` is
supported: every additional archive format is another parser reading hostile
bytes, and one covers the case that exists.

### `secret` config type

**Implemented.** Specified in RPP v1 from the start and rejected by
`manifest.py` through Phase 1 — "reserved in RPP v1 but not implemented in
Phase 1" — because half-implemented credential storage is worse than none.
The store landed in `rom_hub/secrets.py`; this section describes what it does
and, more importantly, what it does not.

**The promise.** A config field declared `type = "secret"` is never written to
the Hub's plain config. `state.json` is the file an operator opens,
screenshots, pastes into an issue and sweeps up with `git add -A`; a credential
in it is a credential in all of those. So a secret goes elsewhere, and the CLI,
the job queue and every error message the host builds redact it on the way out.

**The threat model, stated before the mechanism.** It is *accidental
disclosure*: a log line, a screenshot, a config file in a public repo, a
support paste, a backup that travels. It is **not** a plugin stealing its own
key — a plugin already runs arbitrary code and is handed the value because it
needs it to make its request. And it is not an attacker with a shell as the
operator; nothing a user-level process can reach is secret from a user-level
process. Saying that first matters more than the cipher does.

**Where it lives.** Two stores, selected by `ROM_HUB_SECRET_STORE`
(`auto` by default, or `keyring` / `file`):

| Store | When | What it actually protects |
|---|---|---|
| OS keyring | `keyring` is installed **and** reports a backend with a usable priority | Whatever the OS gives. A locked login keychain is a real boundary; a desktop keyring unlocked at login is readable by anything running as you. |
| file, `ROM_HUB_SECRET_KEY` set | the variable is supplied from outside the box — a Docker secret, a systemd credential | Real encryption at rest. The ciphertext is unreadable without a key that was never written to disk. |
| file, generated key | **the default**, and what a headless Docker deployment gets | **Obfuscation, not secrecy.** `secrets.json` is encrypted and `secret.key` sits beside it; whoever can read one can read the other. What it buys is that the value is not in `state.json`, so it is not in the file that gets dumped, screenshotted or committed. |

The third row is the one that must not be oversold, and the code does not
oversell it: `StoreInfo.protection` contains the word "obfuscation", a test
asserts that it does, and `rom-hub plugin secret list` prints it verbatim so
an operator reads it before trusting it.

A keyring-only design was rejected outright. This Hub's primary deployment is
headless in Docker on Linux, where there is no keyring at all — the fallback is
the main path, not a courtesy. A `keyring` package present with a *fail* or
*null* backend is treated as no keyring, because writing a credential into one
of those reports success and stores nothing.

**The cipher, and why it is stdlib.** scrypt for the KDF, an HMAC-SHA256
counter-mode keystream, and an encrypt-then-MAC HMAC-SHA256 tag under an
independent key. No `cryptography` dependency is taken, because it would not
change the honest answer above: in the default configuration the key is next to
the ciphertext, and no cipher fixes that. Where the key *is* supplied from
outside, this construction is sound for the job — authenticated encryption of a
short value at rest — and a tampered or wrong-key entry is refused rather than
decrypted to garbage.

**Reaching the plugin.** Merged into the `init` frame's config, down the stdin
pipe. **Never through the environment**: `SAFE_ENV_VARS` is built from `{}`
upward precisely so nothing credential-shaped can arrive that way, and routing
one through it would undo the fix that allowlist exists to be.

**Coming back out.** The host knows exactly which strings it handed over, so it
removes them from anything it prints — the plugin's stderr tail, and every
error message built from plugin-controlled text (including the pydantic
validation errors that quote whatever the plugin returned). Every
`PluginCallError` in `broker/host.py` is constructed through one `_fail()`
choke point so a new raise site cannot bypass this, and a test reads the source
to keep it that way. Redaction covers *host-generated output*; a plugin that
deliberately puts its own key into a returned title has disclosed it itself,
which is outside what the host can or claims to prevent.

**Setting one.** `rom-hub plugin secret set <slug> <key>` prompts on a terminal
(nothing echoed, asked twice) and reads stdin when there is no terminal;
`--env VAR` covers automation. `--value` works and warns, because by the time
the command runs the value is already in the shell history and the process
list, and refusing it would only teach the operator to use a redirect and learn
nothing.

**Migration.** Anyone who configured `retroachievements` before this existed
has a plaintext `api_key` in `state.json`. The next command that starts the
plugin moves it into the store, removes it from the plain config, and says so
once on stderr — naming the field, never the value, and advising rotation
because moving a credential out of a file does not move it out of the backups.
Nothing breaks, and in the window before migration the value is still redacted
everywhere and flagged in `plugin secret list`.

**`rpp_version` stays `"1"`.** The contract did not break. A manifest declaring
`secret` was previously *refused*, so no installed plugin can be affected by it
now being accepted, and the other nine parse and run untouched. A version bump
would force every plugin to re-declare something that did not change — the same
reasoning `[[data_assets]]` was held to.

Sub-project C (per-peer credentials) named this as its one required contract
addition. It is no longer a prerequisite.

---

## Security: the broker model

Plugins are arbitrary code from the internet. The Hub holds a RomM admin token
and write access to the ROM library. Declared permissions are worth nothing if
they are honour-system, so the design goal is that the runtime enforces them
rather than the plugin's good behaviour:

> **Design goal: a plugin subprocess has no RomM token, no library mount, and
> no direct network access.**

Host and plugin speak **JSON-RPC over stdin/stdout**. A plugin does not fetch;
it calls `ctx.http.get(url)`, which is an RPC back to the host. The host checks
the URL against the manifest `network` allowlist and only then performs the
request. A plugin returns a *description* of privileged work ("import this URL
as platform `psx`"); the host validates and executes it.

### What is enforced today, and what is not

Phase 1 delivered the broker. Phase 1.5 confined the plugin subprocess itself.
Two of the three lines below are now closed; the third is not, and the split
matters enough to state exactly.

**Enforced: the brokered path.** On the brokered path the allowlist is real,
not advisory. `netpolicy.url_allowed` sits in front of the only code that opens
a socket (`broker/fetcher.py`), `check_url` is unavoidable en route to it — one
call site, the same variable, no TOCTOU gap — and the matcher survived a
44-case adversarial corpus (suffix confusion, userinfo, IDN and UTS-46 dot
mapping, percent-encoding, IP literals, embedded CR/LF) with no exploitable
result. Default-deny holds: a manifest with no `[permissions]` table can reach
nothing. Redirects are disabled and response headers are not exposed, so a
plugin cannot launder a `Location` into a second request.

**Enforced: network egress.** New in Phase 1.5, and it is what makes a
manifest's `network` allowlist a containment boundary rather than a declaration
of intent. The plugin subprocess installs a **self-imposed seccomp filter on
itself, before any plugin code is imported** — `PR_SET_NO_NEW_PRIVS` first,
then a filter returning `EPERM` for `socket`, `socketcall`, `connect`,
`sendto`, and `sendmsg`. Restricting *yourself* needs no privilege, which is
why this works where a namespace sandbox does not. A plugin that ignores
`ctx.http` and reaches for `import socket` gets a `PermissionError`, not a
connection. Measured on the deployment target inside **default Docker** — no
`--security-opt`, no added capabilities:

    NNP_OK → FILTER_LOADED → BLOCKED: PermissionError

**Enforced: useful process spawn.** `execve` and `execveat` are denied, so a
plugin cannot shell out to something that would run unconfined. `clone` and
`fork` are deliberately **not** blocked: CPython uses `clone` for threads, so
denying it breaks the interpreter rather than the attacker. That costs nothing
here, because a forked child **inherits the seccomp filter** and is confined
exactly as its parent is. There is no escape by forking.

**Not enforced: arbitrary file read.** seccomp **cannot filter on a path.** A
filter matches on the syscall number and register values only; it cannot
dereference a pointer argument, so the filename handed to `openat` is invisible
to it. Confining reads requires a **mount namespace**, which this deployment
cannot create (see below). A plugin can therefore still read any file the Hub
process can read — including the Hub's own config and database.

> **Consequence, scoped to what is left: only install plugins you trust.** A
> plugin's declared `network` allowlist is now a real boundary: an untrusted
> plugin cannot reach an undeclared host and cannot exec its way out. It still
> runs with the Hub's own file-read reach. Reading a manifest tells you where
> an honest plugin will go on the network; it tells you nothing about which of
> your files a dishonest one will open.

**Why not bubblewrap or nsjail.** They were the preferred candidate, and they
were measured rather than assumed. Inside default Docker:

    $ docker run --rm debian unshare --user --net echo ok
    unshare: unshare failed: Operation not permitted

Docker's own default seccomp profile refuses the `unshare` that a namespace
sandbox is built on, so `bwrap --unshare-net --ro-bind` cannot start at all
without `--security-opt seccomp=unconfined` or `--privileged` — which would
open a larger hole than the one being closed. Recorded here so the option is
not re-litigated: it is not a matter of preference, it is unavailable on this
target. A self-imposed seccomp filter needs no privilege and is what remains,
at the price of the filesystem line above.

**On a host that cannot install the filter.** `sandbox.probe()` reports
availability; the filter is Linux-only and additionally needs `pyseccomp`.
Where it is unavailable — Windows and macOS development hosts, most obviously —
the Hub **fails closed**: `PluginProcess` raises `SandboxRefused`, the plugin
does not run, and the message names the override. Setting
`ROM_HUB_ALLOW_UNSANDBOXED=1` lifts the refusal and means exactly what it
says: **no confinement at all**. With it set, a hostile plugin can open its own
sockets to undeclared hosts, spawn processes, and read any file the Hub can. It
is a development convenience, never a deployment setting.

Three consequences, all load-bearing:

1. **The permission is real** on the network. A plugin declaring `archive.org`
   genuinely cannot reach anywhere else, cooperative or not, so the worst case
   for a hostile plugin's *traffic* is bad data rather than exfiltration to a
   host of its choosing. What it can still do is read local files.
2. **Plugins are trivially testable.** All *intended* traffic crosses one
   chokepoint, so record-once/replay fixtures come free and the conformance
   suite runs offline. True today.
3. **Rate-limiting and caching live in one place** instead of being
   re-implemented — or forgotten — by every plugin author. True today for
   everything that goes through the broker.

Plus per-call timeouts: one wedged plugin cannot hang a search across the
others. Memory and output-size caps are specified alongside them but are not
yet complete — see the Phase 1 review findings.

### Filesystem confinement is a blocking prerequisite for Phase 2

Phase 1.5 closed the network and exec lines. The file-read line is still open,
and **Phase 2 introduces a RomM admin token**, which the Hub stores. A plugin
that can read the Hub's config or database can read that token — and the
seccomp filter does not make that harmless, because a token only has to *leave*
once to matter. So the Phase 1 sequencing stands, with a narrower and more
accurate reason than before: what blocks Phase 2 is the filesystem, not the
network.

Closing it needs a **mount namespace** — `--ro-bind` the plugin directory and
nothing else — which is exactly what the container denies today (above). The
realistic routes:

- **Run the plugin host in a container that permits `unshare`**, and let
  bubblewrap hold the filesystem while seccomp keeps holding the network. A
  deployment change, not a plugin-contract change.
- **Keep the RomM token where a plugin cannot read it** — a separate process or
  uid holds the credential and performs actions on request, so the plugin host
  never has the secret in a readable file. Removes the reason the file-read
  hole is fatal rather than closing the hole.

Windows and macOS have no equivalent of the seccomp path and fall back to the
container; until then they refuse to run plugins unless
`ROM_HUB_ALLOW_UNSANDBOXED=1` is set.

When filesystem confinement lands, the file-read caveat in this section, in
`README.md`, and in the `plugin install` output can be dropped. The network and
exec caveats have already been dropped — they were retracted on evidence, so do
not reintroduce them without evidence.

### The cost, stated honestly

The plugin API offers no way to open a socket, so a plugin written against it
cannot use `requests`, `httpx`, or Archive.org's own `internetarchive` SDK.
This is the price of the permission being enforceable rather than advisory, and
it is a genuine tax on the "50-line plugin" premise. (Since Phase 1.5 the
seccomp filter stops it too, so an author who reaches for `httpx` anyway gets a
`PermissionError` rather than traffic the allowlist never saw — see above.)

Mitigation: ship a **`requests`-shaped adapter** over `ctx.http`, so the idiom
plugin authors already know (`ctx.http.get(url).json()`) works unchanged. The
constraint stays; the unfamiliarity does not.

### The plugin directory is plural, and none of it is trusted

The directory used to be one file in this repository, which made the
ecosystem closed: only whoever shipped the repository could publish a plugin,
and somebody keeping their plugins on their own Gitea (say
`git.moveweight.com`) could not be found at all. `rom-hub catalog add` fixes
that — an ordered list of directories, each an https URL or a local path, the
bundled one always first. `src/rom_hub/catalog_sources.py`.

That turns a file the project wrote into **attacker-influenced input**, so
what a directory *is* has to be stated exactly.

**What a catalog can cause to happen.** Exactly one thing: it can put a name,
a description and a URL in front of a human, and — if that human types
`rom-hub plugin install <slug>` — decide which repository and which tag are
cloned. That is real influence and it is why the notice at install time
exists, but it is the whole list.

**What a catalog cannot cause to happen.** It cannot grant a permission. An
installed plugin's network allowlist is read from its own `manifest.toml` at
install time and enforced by the broker, which does not import
`catalog_sources` and never has. The `network` field in a directory entry is a
copy for a human to read before installing; if it could reach the broker,
adding one source would hand its author every plugin on the host.
`test_catalog_cannot_widen_permissions` pins that for the bundled file and
`test_a_remote_catalog_cannot_widen_an_installed_plugins_reach` pins it for a
fetched one, with a fixture that asks for a host the manifest does not.

It also cannot: run code (nothing in a catalog is executed), reach a plugin
(no capability is handed a catalog entry), change where anything is written,
or make the Hub fetch from anywhere other than the one host the *operator*
typed.

#### A catalog URL is a different trust class from `ctx.http`

Worth writing down because the two look alike and are not.

`ctx.http` is **plugin-supplied**: a sandboxed subprocess of somebody else's
code names a URL, and the host may fetch it only if that plugin's manifest
declared the host — a declaration the operator reviewed at install time. The
allowlist is the point, and `netpolicy.check_url` is what makes it real.

A catalog URL is **operator-supplied**: it is typed into `rom-hub catalog
add`, a command that does nothing else, with no plugin in the loop. There is
no manifest to consult and no allowlist that would mean anything — the
operator naming the host *is* the authorisation, exactly as it is for
`rom-hub plugin install https://...`. Gating it behind some other allowlist
would be theatre.

So the policy does not carry over. The machinery does, where it makes sense:

| | applied to a catalog URL | why |
|---|---|---|
| https only (`netpolicy.ALLOWED_SCHEMES`) | yes | over http anyone on the path rewrites the list, and every install URL a reader then trusts comes from it |
| hostname validation (`netpolicy.url_allowed`) | yes, against the one host named | the thing contacted must be the thing the operator read; userinfo is refused outright rather than stripped |
| redirect re-checking (`importer.HttpDownloader`) | yes | a 302 to another host is not the source that was added |
| size bound | yes (`MAX_CATALOG_BYTES`) | an endless response must not become an endless allocation |
| the plugin's manifest allowlist | **no** | there is no plugin; see above |
| strict parsing (`parse_catalog`) | yes, and now unknown-field-rejecting | a key this build does not read is a claim nobody will check |

#### Collisions: first source wins, and the collision is shown

The bundled directory is first and cannot be removed, so a third-party
directory can add plugins but never replace one this project ships.

*Last wins* was rejected outright: it would make adding any source a
one-command supply-chain swap of a slug people type from memory. *Refuse and
require disambiguation* was rejected because it hands every third-party
catalog a veto — claim the popular slugs and the operator's whole directory
stops working. First-wins has neither failure, and its cost is the right one:
a stranger cannot offer a "better" build of a bundled plugin under the same
slug, which is exactly the claim an attacker would make.

Nothing about it is silent. The losing entry is dropped, and the collision is
printed by `plugin browse`, by `catalog list`, and again by `plugin install`
when the slug being installed is one of them.

#### Staleness and partial answers

Fetched directories are cached for six hours (`ROM_HUB_CATALOG_TTL`). A fetch
that fails falls back to the cached copy and reports its age, because
degrading to a known-old answer beats degrading to nothing — but only if the
caller is told, which is what `SourceStatus.stale_seconds` carries.

A source that cannot be read at all costs its own plugins and nothing else,
and the listing says so: `1 of 2 catalog(s) reachable`, followed by which one
failed and why. This is the rule `search` already follows ("N of M sources
responded") and it matters more here, because a plugin missing from `browse`
does not read as a source that failed — it reads as a plugin that does not
exist.

### Rejected alternatives

- **SDK model** (scoped short-lived token + client library). Nicer to write
  against, but enforcement moves into token issuance, and RomM's token scoping
  cannot express "may upload to this one platform" — we would be building that
  ourselves, which is strictly more work for strictly weaker guarantees.
- **Container per plugin.** Strongest isolation, but writing a plugin would mean
  building an image. That kills the 50-line contribution this exists to enable.
  *Note: sub-project C may adopt this for a long-running federation peer without
  disturbing the plugin API.*

---

## Data flow: search → import

1. User searches in the Hub UI.
2. Dispatcher fans out to all enabled `search` plugins **in parallel**,
   each in its own subprocess.
3. Results normalised, merged, and **deduped against the existing RomM library
   by file hash** (RomM stores hashes; matching on name + size is both slower
   and wrong at the margins), so re-imports are visible before they happen.
4. User selects an item; dispatcher calls `importer.plan(result)`.
5. Host validates the returned `FetchPlan` against the plugin's permissions.
6. **Host** downloads (resumable, via range requests) to `var/downloads/`.
7. **Host** uploads (the mechanism is the backend's: RomM's chunked
   `upload/start`→`PUT`→`complete`, a Gaseous multipart `POST`, or a Retrom
   WebDAV write), then registers what landed (`scan`).
8. **Host** adds the ROM to an "Archive.org" collection **if the backend
   has collections** — RomM does; Gaseous and Retrom do not, so the step is
   skipped and reported rather than failing the import (see
   [`capabilities()`](#capabilities-is-what-makes-degradation-honest)).

Steps 5–8 are entirely host-side. The plugin's involvement ends at step 4.

---

## Search that scales

Step 3 above says "normalised, merged". This section is what that means,
because the naive reading of it — concatenate the lists — produces a listing
nobody can use.

### The two problems, both observed

**Duplicates dominate.** A real Game Gear shelf in the demo library returns
*Batman Returns* eight times, *Aladdin* four times, *Desert Assault* and
*Agassi* twice. Every one of those rows is a genuinely distinct ROM — a
different region, a different revision, a different dump — and the result still
reads as broken. Live against No-Intro's Game Gear directory, `sonic` returns
**47 rows**, of which twenty-one are dated *Sonic Spinball* betas.

**Concatenation does not scale.** `search_all` fanned out and extended one
list. `--limit` was applied *per plugin*, so ten sources at `--limit 25` was 250
rows in fan-out order, with no paging and no cross-source merging. Console
Living Room alone holds roughly ten thousand downloadable Genesis ROMs.

### One row per game, variants underneath

`rom_hub.grouping` turns the flat list into `GameGroup`s: one row per game per
platform, with a variant count, expandable with `--expand <#>`. It **never
discards a row** — `--no-group` prints the raw listing, `group.results` still
contains every result, and the count printed next to a collapsed row is the
count of what is inside it.

Two questions are answered separately, and keeping them apart is most of the
design:

| Question | Answer | Type |
|---|---|---|
| Which results are the same **game**? | `(platform, normalised title)` | `GameGroup` |
| Which results are the same **dump** of it? | region + revision + disc + dump flags, and any hash | `Variant` |

Cross-**source** duplicates (the identical ROM from `archive-org` and
`nointro-archive`) and cross-**variant** ones (USA vs Europe vs Rev 1) are
different problems. The first collapses to one variant listing two sources; the
second stays several variants under one game.

### How identity is decided

**1. A matching strong hash is proof.** sha256, sha1 or md5 in a result's
`extra`: two results carrying the same one are the same bytes, whatever the two
catalogues chose to call the file. This merges names that would never have met.

**2. A conflicting hash is disproof, and it outranks the name.** Two rows named
identically whose digests disagree are two different dumps that a catalogue
named carelessly, and they stay two rows. **CRC-32 counts here even though it
does not count as proof** — the same asymmetry `plugins-dev/hasheous` already
applies to metadata identity, and for the same reason: 32 bits is far too weak
to assert "these are the same file" and quite strong enough to refuse it.

**3. Otherwise the parsed name decides**, via `rom_hub.romnames`.

No-Intro/GoodTools/TOSEC naming is highly structured — `(USA)`, `(Europe)`,
`(Rev 1)`, `(Beta)`, `[!]` — and parsing it is the obvious lever. Two rules
govern that parser:

**Parse to group, never to discard.** This project has already shipped a
filename validator strict enough to drop every GoodTools `[!]` name it saw —
the *verified good dump* marker, i.e. exactly the ROMs people want most — and
nothing said so. Nothing in `romnames` filters. `parse()` is total: a name that
matches no pattern at all becomes a title with no tags and gets its own group.
The worst outcome of a parse failure is a row that failed to merge.
`tests/test_romnames.py` pins `[!]` first, before anything else.

**Wrongly merging two different games is worse than showing two rows.** Every
normalisation in `normalise_title` is one that can be justified without
reference to any particular title: case, Latin accents, `&` vs "and",
punctuation, and the leading/trailing article (`The Legend of Zelda` ≡ `Legend
of Zelda, The`). Deliberately absent, and each absence is a test:

- **Roman numerals are not folded to digits.** `Final Fantasy II` and `Final
  Fantasy 2` stay two rows. Folding them correctly requires knowing which
  trailing token is a numeral; folding them wrongly merges different games.
- **Trailing numbers are not stripped.** `Sonic the Hedgehog` and `Sonic the
  Hedgehog 2` are different games and nothing is worth risking that.
- **Subtitles are not stripped**, and near-identical names are not guessed at.
  `Agassi Tennis` and `Andre Agassi Tennis` stay two rows even though they are
  plausibly one game — the merge that would fix it would also merge things that
  are not.

Two further conservative choices, both of which cost rows:

- **Tags are read only as a suffix.** Every convention here writes them that
  way. A source that puts prose *after* its tags is one this parser declines to
  interpret: the whole title becomes the group key, which means more rows.
- **A platform nobody stated is never merged into one that was.** "Unknown" is
  not evidence, and guessing which console a ROM is for is how a library ends up
  wrong in a way nobody can see. Unknown-platform groups sort last.
- **An unrecognised tag still distinguishes a variant.** `(Sega Channel)` means
  nothing to this parser, so a row carrying it stays separate from one that does
  not, rather than merging on the strength of a pattern that did not match.

### Ordering and paging

Groups are ordered **relevance, then platform, then title** — relevance in four
coarse bands (exact, prefix, all tokens as words, all tokens as substrings)
rather than a score fine enough to reorder two titles on an irrelevant
difference. Within a group, variants are ordered verified-dump-first, then by a
region preference, then newest revision first. That ordering is **display only**:
a bad dump sorts last and is still listed.

`--limit`/`--offset` page the **merged** set. `--limit` therefore now counts
games, not raw results — a change in meaning from the pre-grouping flag, stated
in `--help` and here. Because grouping only ever collapses, each source has to
be asked for more than a page's worth; `dispatcher.fanout_limit()` computes that
(4× the page, floored at 50 and capped at 500) and `--per-source` overrides it.

### Honesty is unchanged

Grouping reorganises what came back. It cannot know what a source that failed
would have said, and it must not look like it does:

- the `N of M sources responded` line and the per-plugin stderr failures are
  printed exactly as before, alongside the grouped listing;
- a source that returned *exactly* as many results as it was allowed is now
  reported as **capped** (`PluginStatus.capped`), because a merged listing that
  silently dropped a source's tail is otherwise indistinguishable from a
  complete one;
- `--no-group` prints the pre-grouping listing, so nobody has to trust the
  grouping in order to see the raw set.

### Cost

Grouping is linear in results — a cached parse and a handful of dictionary
operations each — plus one sort of the groups. Measured on synthetic fan-outs on
one workstation: **1k rows in 15 ms, 10k in 169 ms, 50k in 0.92 s, 100k in
2.1 s** (~50k rows/s), and 50k rows with no two titles alike in 1.5 s. The
fan-out it follows is network-bound and orders of magnitude slower, so this is
not the bottleneck at any size the Hub actually sees.

Union-find is what makes the two merge rules order-independent: a hash can join
two rows the names would have kept apart, and a name can join two rows neither
of which carries a hash. Each set carries the digests its members claim, and a
union is *refused* when two sets disagree about any digest kind they both know —
which is how a conflicting CRC-32 can split two identically-named rows without
ever being allowed to merge two differently-named ones.

### Live proof

Against No-Intro's Game Gear directory (`nointro-archive`, one source, one
platform, so nothing here is a platform artefact):

```
$ rom-hub search sonic --no-group      →  47 results
$ rom-hub search sonic                 →  47 results in 11 games
```

`Sonic Spinball` collapses 21 rows to one; `Sonic The Hedgehog` (4),
`Sonic The Hedgehog 2` (3) and `Sonic The Hedgehog - Triple Trouble` (2) stay
**three** rows, which is the case this design is most concerned with getting
wrong. `--expand 5` lists all 21 Spinball betas, each with its own date.

---

## The Archive.org plugin

### Verified API surface

Both endpoints were tested against live Archive.org during design.

`GET https://archive.org/advancedsearch.php?q=collection:<c>&fl[]=identifier&fl[]=title&output=json`
→ `response.numFound` = **8,903** for `softwarelibrary_msdos_games` alone.

`GET https://archive.org/metadata/<identifier>` →

```
metadata.emulator     = "dosbox"          ← which emulator/core
metadata.emulator_ext = "zip"             ← which of the 14 files is the payload
metadata.collection   = [..., "stream_only", ...]
files[].format        = "ZIP" | "JPEG" | "Emulator Screenshot" | ...
```

### `stream_only` drives routing — not a hardcoded allowlist

This is the most important finding of the design pass. Archive.org **itself**
marks which items are in-browser-stream-only versus genuinely downloadable, via
membership in the `stream_only` collection.

So the plugin routes on IA's own signal rather than on a list we maintain:

- `stream_only` present → offer the **stream** capability, do not attempt import
- `stream_only` absent → offer **import**

This is more accurate than any allowlist we would hardcode, it stays correct as
IA reclassifies items, and it makes the "freely-accessible by default" posture
self-maintaining. A config toggle can widen scope, but the default needs no
curation.

### Capability implementations

| Capability | Implementation |
|---|---|
| `search` | `advancedsearch.php`, scoped to configured collections |
| `importer` | `emulator_ext` selects the payload from the item's file list; refuses `stream_only` items |
| `metadata` | `metadata.title` → name; `00_coverscreenshot.jpg` → cover, falling back to `files[].format` (`Emulator Screenshot`, then `Item Tile`), which makes artwork extraction deterministic |
| `stream` | the item's own `/details/` page, which is where Emularity plays it; routes on the same `stream_only` signal the importer refuses on. `rom-hub stream archive-org <id> --open` therefore plays the game, and `msdos_Oregon_Trail_The_1990` — the item the importer refuses — is exactly the one this serves |
| `cores` | **not implemented** — see below |

Two of these differ from the original design, both on evidence found while
building them.

**`metadata` needs the identifier, and refuses to guess it.** The `RomRef` the
host supplies carries RomM's name and filename, and neither is an Archive.org
identifier (`rubik.zip` is not `rubik_202308`). Searching for the rom's name
and taking the top hit would write *a* game's title and cover into the
library rather than *this* game's, and the operator would have no way to
notice. So the identifier is passed explicitly — `rom-hub enrich archive-org
<rom_id> --source-id <identifier>` — and its absence is a refusal that names
the flag. The same reasoning as the platform table's "needs mapping": silent
misfiling is worse than a visible gap.

**`cores` is not implemented, and the earlier claim that it would be was
wrong.** `metadata.emulator` names an emulator (`dosbox`, `vice`); it does not
name a downloadable artifact, and Archive.org publishes no core distribution
to harvest from. Implementing the capability would mean inventing a download
URL, and a plugin that fabricates a target is a plugin whose refusals cannot
be believed either. The Hub's `cores` capability is complete and is exercised
by a plugin subprocess in the test suite; what is missing is an upstream that
Archive.org actually offers.

### Platform mapping

`metadata.emulator` (`dosbox`, `vice`, `mame`, …) maps to RomM platform slugs via
a table shipped in the plugin repo (`archive_org/platforms.py`). Unmapped
emulators surface as "needs mapping" rather than importing to a wrong platform —
silent misfiling is worse than a visible gap.

As built, the table is an **exact-match lookup with no fallback**. Archive.org's
emulator ids have an obvious family/variant shape (`vice-resid`, `vice-pet`,
`pce-macplus`, `pce-atarist-color`), and a prefix rule would map most of them for
free — but in those two families the *variant* is the machine, so the shortcut
maps a PET to a C64 and an Atari ST to a Mac. The keys were sampled from live
Archive.org rather than from an emulator list, and the values were checked
against RomM 4.9.2's own platform-slug enum.

Payload selection: `emulator_ext` matched case-insensitively against `files[]`
(Archive.org spells it both `zip` and `ZIP`), largest match wins, and a file with
no `size` — every item's `_files.xml` — sorts below every sized one so a metadata
stub can never outrank the ROM.

---

## UI

The Hub serves its own web UI at its own hostname. **RomM's UI is not
modified and no nav entry is injected into it.**

An earlier draft specified injecting a `<script>` into RomM's `index.html` via
Traefik. That was investigated and rejected on evidence:

- Traefik has **no native response-body rewriting**. The reverse proxy this was
  measured against runs 3.6.13 with no plugins configured and no plugin storage
  directory.
- Enabling it means `experimental.plugins`, a third-party body-rewrite plugin,
  and a Traefik restart — which interrupts **every service behind that proxy** and
  adds a boot-time remote-code fetch to the reverse proxy fronting all of it.
- RomM has no custom-head/CSS hook either: its container exposes no
  `CUSTOM_*`/`HEAD`/`CSS` environment variable.

Proxy-wide risk for a nav shortcut is a bad trade. The Hub is reached at
its own hostname.

---

## Error handling

| Failure | Behaviour |
|---|---|
| Plugin crashes | stderr captured, plugin marked unhealthy, **other plugins unaffected**; search returns partial results with per-plugin status |
| Plugin hangs | per-call timeout, subprocess killed |
| Plugin floods output | output-size cap |
| Download interrupted | resumable via HTTP range requests |
| Upload interrupted | retry; `POST /api/roms/upload/{id}/cancel` on abort |
| **Hub restarts mid-import** | **persisted job queue** — a 4 GB in-flight import survives |
| Plugin requests a disallowed host | broker refuses, logged against the plugin |

A partial search that clearly reports "3 of 4 sources responded" is correct
behaviour. Silently returning 3 sources' results as if complete is not.

---

## Testing

- **RPP conformance suite** — every plugin author runs the same harness.
- **Offline fixtures** — because all plugin HTTP crosses the broker, record
  once and replay; no network in unit tests.
- **Integration** — ephemeral RomM container, one real import of a small
  public-domain item, asserted end-to-end through the upload API.
- **Permission tests** — assert a plugin declaring `archive.org` is actually
  refused when it requests anything else. The security model is a claim; it
  needs a test that fails if it regresses.

---

## Upstream contribution (deferred)

The PR is **not** "merge the Hub." It is a small, self-contained change making
RomM's metadata-provider list extensible from config, so an external provider
can register itself. Everything else stays in the sidecar.

RomM's `CONTRIBUTING.md` requires opening an issue and discussing on Discord
**before** a PR, and requires disclosing AI assistance. Both apply.

---

## Phasing

| Phase | Delivers | Proves | Blocked on | Status |
|---|---|---|---|---|
| 1 | Hub core + RPP v1 + broker + `search` + CLI | the contract is real | — | **done** |
| 2 | `importer` + RomM adapter + job queue | it is actually useful | **filesystem confinement** | **done** |
| 3 | `metadata`, `stream`, `cores` | the last three of the original five | — | **done** |
| 3.1 | `firmware` | **the capability set is complete**, and the one gap both backends' APIs had left unserved is filled | — | **done** |
| 4 | Web UI | it is pleasant | — | not started |
| 5 | sub-projects C and D | *separate design pass* | — | not started |

(Phases 3 and 4 are swapped relative to the original plan. The capabilities
were built before the UI because a UI over three unimplemented capabilities
would have had to be rewritten once they existed; nothing about the UI work
depended on doing it first. The nav-injection half of the old Phase 3 was
dropped outright on evidence — see [UI](#ui).)

Phase 1 ends with a plugin that can search Archive.org from a CLI. That is the
smallest thing that validates the riskiest assumption — the plugin contract —
before any UI work is spent on it.

**Phase 2 is blocked on filesystem confinement.** Phase 1.5 closed the plugin
subprocess's network egress and process spawn with a self-imposed seccomp
filter, but seccomp cannot filter on a path, so a plugin can still read any
file the Hub can (see [What is enforced today, and what is
not](#what-is-enforced-today-and-what-is-not)). Phase 2 is where the Hub first
holds a RomM admin token, and a token sitting in a file a plugin can read turns
a hostile plugin from a search-query leak into a full library compromise.
Closing that needs a mount namespace, which the current container denies; it is
a prerequisite, not a parallel workstream, and the file-read caveat above comes
out when it lands.

**Phase 2 shipped with that prerequisite unmet, and Phase 3 does not widen the
gap.** Stated plainly because "we said it was blocking and shipped anyway" is
the kind of thing that quietly becomes untrue in a document. What Phase 3 adds
is three more capabilities using the *same* RomM token Phase 2 already held:
`metadata` writes through it, `stream` and `cores` never touch RomM at all,
and no capability puts a new secret anywhere a plugin could read. The exposure
is unchanged — which is not the same as acceptable, and the mitigation in
`README.md` is still the honest one: only install plugins you trust.

---

## Open questions

1. **Platform-mapping table ownership** — ships in the Archive.org plugin repo,
   or in the Hub as shared infrastructure? Leaning plugin-local for v1, since a
   second plugin has not yet shown what should be shared.
2. **Multi-file items** — some IA items contain several distinct games. v1
   imports the `emulator_ext` payload only; multi-game items are a v2 question.
3. **Hub authentication** — reuse whatever SSO the deployment already runs, or
   proxy RomM's own session? SSO is the likely answer, but it is not yet
   decided.
