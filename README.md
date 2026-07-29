# ROM Hub

[![CI](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml)
[![coverage 87%](https://img.shields.io/badge/coverage-87%25-brightgreen)](#coverage)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![licence MIT](https://img.shields.io/github/license/BlizzHacker/rom-hub)](LICENSE)

The suite is 1461 tests and it runs on every push, on Linux and Windows, on
Python 3.12 and 3.13. On Linux the seccomp confinement tests must *pass* — CI
fails if they merely skip, because a skipped containment test looks exactly
like a passing one. [docs/PROOF.md](docs/PROOF.md) is a generated matrix of
what actually works against a live server of each of the three backends, cell
by cell, with the evidence for each; [Coverage](#coverage) has the honest
numbers and says which one of them is misleading and why.

**A plugin host for self-hosted ROM library managers.** It runs beside
[RomM](https://github.com/rommapp/romm),
[Gaseous](https://github.com/gaseous-project/gaseous-server) or
[Retrom](https://github.com/JMBeresford/retrom) as a sidecar and never modifies
the library server. Plugins add sources — searching them, importing from them,
enriching what you already have — to a server that has no plugin system of its
own.

**A plugin is backend-agnostic, and that is structural rather than a promise.**
A plugin never talks to a library server and holds no credential for one. It
returns a *description* of work — which files to fetch, which metadata to set,
where an item can be streamed — and the Hub executes that description against
whichever server `ROM_HUB_BACKEND` selects. Nothing inside a plugin has ever
known which of the three is on the other side, so a plugin written against one
works against all of them, as far as that server is capable (`rom-hub backend
info` says what the chosen one can do).

The same shape is what makes untrusted plugins tractable: a plugin runs as its
own subprocess with no token, no filesystem mount and no sockets, and reaches
the network only through an RPC the host checks against the allowlist that
plugin declared. See [Security model](#security-model) — including, plainly,
what is *not* confined.

- **[docs/PLUGINS.md](docs/PLUGINS.md)** — the plugin directory: seven
  published plugins, what each one asks for, and the terms of the source it
  reads from.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to write a plugin and get it
  listed.
- **[docs/DESIGN.md](docs/DESIGN.md)** — the architecture.
- **[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md)** —
  deferred federation and multiplayer work.

MIT licensed; see [LICENSE](LICENSE). Each plugin is a separate work under its
own licence, carried in its own repository.

## Renamed from `romm-hub`

The project, its packages and its `ROMM_HUB_*` environment variables lost a
letter, because the host is no longer about one library server. Nothing in the
plugin contract changed with it.

| Was | Is | Old name still works? |
|---|---|---|
| `romm-hub` (CLI, project) | `rom-hub` | no — reinstall |
| `romm_hub`, `romm_hub_sdk` | `rom_hub`, `rom_hub_sdk` | **yes**, deprecated |
| `ROMM_HUB_HOME` | `ROM_HUB_HOME` | **yes**, deprecated |
| `ROMM_HUB_ALLOW_UNSANDBOXED` | `ROM_HUB_ALLOW_UNSANDBOXED` | **yes**, deprecated |
| `ROMM_HUB_CORES_DIR` | `ROM_HUB_CORES_DIR` | **yes**, deprecated |
| "RomM Provider Protocol" | "**ROM** Provider Protocol" | acronym unchanged |

**`rpp_version = "1"` is still correct and must not be bumped.** The protocol
was renamed, not revised: the acronym, the capability names, the wire format
and every validation rule are byte-for-byte what they were. A manifest written
last week needs no edit.

**For plugin authors, one line changes:** `from romm_hub_sdk import ...` becomes
`from rom_hub_sdk import ...`. The old import still resolves — to the *same*
objects, so `isinstance` still holds — and warns. It will be removed.

`ROMM_URL`, `ROMM_USER` and `ROMM_PASSWORD` were **not** renamed. They are
RomM's name, not the Hub's, and they configure one backend among several.

## Status

**Phase 3 — RPP v1 is fully implemented.** All five capabilities have a host
implementation and a CLI command:

| Capability | Command | What it does |
|---|---|---|
| `search` | `rom-hub search <query>` | fans out across every enabled plugin |
| `importer` | `rom-hub import <plugin> <source_id>` | plan → download → hash-dedup → upload → register → collection |
| `metadata` | `rom-hub enrich <plugin> <rom_id>` | plugin describes metadata, the Hub fetches the artwork and writes to the library |
| `stream` | `rom-hub stream <plugin> <source_id>` | resolves one item to a validated stream target and prints it |
| `cores` | `rom-hub cores list\|install <plugin> [<core>]` | lists a plugin's emulator cores, downloads one |

Plus the broker, a seccomp-confined plugin subprocess, and a job queue that
survives a restart. No web UI yet.

## Which library server

`ROM_HUB_BACKEND` selects it; `romm` is the default. Three ship:
[RomM](https://github.com/rommapp/romm),
[Gaseous](https://github.com/gaseous-project/gaseous-server) and
[Retrom](https://github.com/JMBeresford/retrom).

| Backend | Settings | Can | Cannot |
|---|---|---|---|
| `romm` | `ROMM_URL`, `ROMM_USER`, `ROMM_PASSWORD` | import, scan, metadata, artwork, collections | — |
| `gaseous` | `GASEOUS_URL`, `GASEOUS_USER`, `GASEOUS_PASSWORD` | import, scan | collections, metadata, artwork |
| `retrom` | `RETROM_URL` | import, scan, metadata, artwork | collections |

`ROM_HUB_BACKEND_URL`/`_USER`/`_PASSWORD` also work for any of them, for a
deployment that would rather not name a product in its unit file. Retrom has no
accounts, so it reads only the URL.

    rom-hub backend info

    backend          romm
    selected by      default (romm)
    available        gaseous, retrom, romm
    settings         ROMM_URL, ROMM_USER, ROMM_PASSWORD
    configured       no -- ROMM_PASSWORD not set

    can:
      artwork        attach cover art to a rom
      collections    group roms into a named collection (rom-hub import --collection)
      import         accept a ROM upload, and list the library so a duplicate is caught first
      metadata       write a rom's metadata fields (rom-hub enrich)
      scan           needs an explicit registration step after an upload

**It opens no connection.** The person most likely to run it is the one whose
connection is not working yet.

### How the three differ

A plugin never sees any of this — it returns a *description* and the host
executes it against whichever backend is configured. The differences below are
the host's problem, not the plugin's, and every row was established from the
backend's source and a live run rather than assumed.

| | RomM | Gaseous | Retrom |
|---|---|---|---|
| Transport | REST | REST | gRPC-Web over HTTP/1.1 + WebDAV |
| Import | chunked upload API | `POST /api/v1.1/Roms` multipart | no upload API — files land via WebDAV, then `UpdateLibrary` indexes them |
| Dedup | by hash (archives hashed as decompressed members concatenated) | filename (see platform-0 note) | filename only — Retrom stores no checksums |
| Collections | yes | no — `CollectionsController` is empty | no — not in the schema |
| Metadata write | yes | no — a rom exposes only GET/DELETE | yes, read-modify-write |
| Post-import | socket.io `scan` event | `ImportQueueProcessor` | `UpdateLibrary` |

### Cannot-do-the-job vs cannot-do-an-extra

A backend that cannot do something says so — but *what it does about it*
depends on whether the missing capability is essential to the operation or an
optional extra layered on top. The split is deliberate and is decided per
capability in `src/rom_hub/backends/base.py`:

- **Essential — refuse before anything is downloaded.** `import` (there is
  nowhere to put the ROM) and `metadata` (`rom-hub enrich` writes nothing
  otherwise) refuse up front. A backend that cannot be imported to fails the
  job before a single byte moves, with a message naming the backend — never a
  four-gigabyte download followed by a 404 from an endpoint that does not exist.
- **Optional — do the job, report the skip.** `collections` and `artwork` are
  extras. A collection groups a ROM that is already in the library; artwork is a
  cover on a record. If the backend cannot do one, the import (or enrich)
  proceeds without it and the outcome plainly says what was skipped and why —
  in the result the CLI prints *and* in the job record, shown by `rom-hub jobs`
  as a `~` note (a skip, not the `!` a failure gets).

This is why `rom-hub import archive-org rubik_202308` now completes against
Gaseous and Retrom. The archive-org plugin files everything under an
"Archive.org" collection and its `collection` config cannot be emptied
(`config.get("collection") or "Archive.org"`), so against a backend with no
collections the whole import used to stop at that check with nothing
downloaded. A collection is a grouping nicety, not part of getting a ROM into a
library; it is now skipped and noted, and the ROM lands.

**A `--collection` you typed is different.** Dropping a plugin's default costs
you nothing you asked for; silently not honouring a name you typed is how a
library ends up unsorted with no error to explain it. So `rom-hub import
--collection "Shooters"` against a collection-less backend still refuses, up
front before the plugin subprocess starts, and the refusal names the way out
(re-run without the flag).

### Gaseous

[Gaseous](https://github.com/gaseous-project/gaseous-server) imports and scans
but does not write metadata or group into collections, and two upstream
quirks are worth knowing before you point the Hub at one:

- **A rom you import may land on platform 0.** `OverridePlatformId` is stored,
  resolved and passed into `ImportGameFile`, but its body never reads the
  argument — the platform is taken from the file signature instead. An
  unrecognised ROM therefore lands on platform 0 regardless of what you asked
  for (measured: asked for 13/DOS, got 0). The Hub cannot correct this from
  outside; it is Gaseous's own import path.
- **Listing without a `PlatformId` 404s.** The unfiltered rom listing joins a
  `Game` table that is absent from schema 1042, so the Hub always lists per
  platform. Not a limitation you will hit through `rom-hub`, but it explains
  why the backend never issues a bare list.
- **`ContentManagerController` is not a ROM route.** It handles attachments —
  screenshots, video, manuals, 50 MB cap — not game files, which is why it is
  not wired up as an artwork path. A Gaseous rom exposes only GET and DELETE, so
  there is no metadata write to make.

### Retrom

[Retrom](https://github.com/JMBeresford/retrom) works differently enough from
RomM to be worth a few lines before you point the Hub at one.

**Its library is the filesystem.** Retrom has no upload API — no `CreateGame`,
no `CreatePlatform`, no RPC that carries file content anywhere in its schema. A
scan (`UpdateLibrary`) walks the configured content directories and creates a
platform per directory and a game per entry. So the Hub files a ROM by *writing
a file*, over Retrom's own WebDAV service, and then asking for a rescan.

**That WebDAV service is rooted at Retrom's data directory**, so a content
directory has to live inside `RETROM_DATA_DIR` (`/app/data` in the official
image) for the Hub to be able to write into it. The stock compose file mounts
libraries at `/lib1` and `/lib2` instead, which is *outside* it: move or
bind-mount your content directory under the data directory, e.g.
`/app/data/library`. If it is not reachable, the backend probes and **refuses
with instructions before anything is downloaded**.

**A platform must already exist.** Retrom derives one from a directory name, so
create `<content dir>/<platform>` and scan once before importing. The name has
to match what the plugin plans — the archive-org plugin plans `dos` for a
DOSBox item, so the directory is `dos`, not `dosbox`.

Retrom has **no accounts** — there is no auth layer on any of its three
services and none of its RPCs take a credential — so `RETROM_URL` is the whole
configuration. Put a reverse proxy in front of it if it needs protecting.

It has **no collections** and stores **no checksums**, so it dedups by filename
only. A plugin-defaulted collection is skipped and reported (see above); an
explicit `--collection` is refused up front.

### RomM

Two RomM quirks the Hub works around, recorded because they cost time to find:

- **`/api/token` needs an explicit `scope`.** Without one, every subsequent
  call 403s. The Hub requests the scopes it needs at auth time.
- **`/complete` returns 201 with no body, and the ROM does not exist yet.** The
  completion endpoint writes the file into the library directory and creates no
  database row; RomM's own UI emits a socket.io `scan` after every upload, and
  so does the Hub. The rom is identified afterwards by finding its digest in the
  library, which doubles as proof it actually landed.

## Quick start

    git clone https://github.com/BlizzHacker/rom-hub
    cd rom-hub
    python -m pip install -e ".[dev]"

    rom-hub plugin browse                  # the seven published plugins
    rom-hub plugin install archive-org     # clones the repo, pinned to its tag
    rom-hub search "oregon trail" --limit 5

`plugin install` takes a catalog slug, a git URL, or a local path. A slug is
resolved through [`catalog/plugins.json`](catalog/plugins.json), which supplies
the repository **and** the tag, so these two are the same install:

    rom-hub plugin install archive-org
    rom-hub plugin install https://github.com/BlizzHacker/rom-hub-archive-org --ref v0.2.0

Every install is pinned to a tag and the resolved commit SHA is recorded, so a
tag moved after the fact does not change what you have. Updating is an explicit
re-run with a new ref; nothing updates itself.

**Searching needs no library server at all** — it fans out across installed
plugins and prints results. `import` and `enrich` are the commands that need
one configured.

On Linux the install also pulls `pyseccomp`, which is what lets the plugin
subprocess confine itself. If it is missing, `rom-hub` refuses to run plugins
rather than running them unconfined. On **Windows and macOS there is no
confinement available at all**, and plugins refuse to run without

    ROM_HUB_ALLOW_UNSANDBOXED=1

which means exactly what it says. See [Security model](#security-model).

## Importing

`import` takes a plugin's own id for an item and puts the ROM in the library.
The plugin only says *what* to fetch; the Hub downloads it, hashes it, checks it
is not already in the library, and uploads it.

    rom-hub import archive-org rubik_202308
    rom-hub import archive-org rubik_202308 --platform dos --collection "Archive.org"

`--platform` and `--collection` override what the plugin planned. They retarget
where a ROM files; they cannot make the Hub fetch from anywhere the plugin's
manifest does not already allow, and they cannot override a plugin's refusal —
if a plugin says an emulator "needs mapping", the fix is to add the mapping,
not to name a platform by hand and leave the gap open for the next person.
`--collection` against a backend with no collections is refused up front (a name
you typed is not dropped silently); a collection a plugin *defaulted* is skipped
and reported instead — see [Cannot-do-the-job vs
cannot-do-an-extra](#cannot-do-the-job-vs-cannot-do-an-extra).

An import already in the library is reported as a duplicate and **not**
uploaded. Matching is by file hash where the backend records one, and by
filename where it does not (Gaseous and Retrom).

    rom-hub jobs                # every import job and its state
    rom-hub jobs --state FAILED # just the ones that went wrong, with reasons

Job state lives in `$ROM_HUB_HOME/var/jobs.db` and downloads land in
`$ROM_HUB_HOME/var/downloads/`, so an interrupted multi-GB import is resumed
rather than restarted.

## Enriching metadata

`enrich` asks a plugin what it knows about a rom already in RomM, then writes
it. The plugin describes; the Hub fetches the artwork and holds the token.

    rom-hub enrich archive-org 1 --source-id rubik_202308

**Only what the plugin actually set is written.** An unset field is absent from
the request, not sent as an empty one — verified against a real RomM: a
name-only update leaves an existing `igdb_id` alone. That distinction is the
difference between a partial patch and erasing a curated library.

`--source-id` is there because RomM does not record which plugin an import came
from, so a plugin generally cannot tell which of its own items a rom is. A
plugin that will not guess says so and names the flag. Archive.org will not
guess: searching for the rom's name and taking the top hit would write another
game's title and cover into your library with nothing to notice it by.

Artwork can come from a URL (the Hub fetches it, and only from a host the
plugin's manifest declares) or from bytes the plugin already has. It lands in
`$ROM_HUB_HOME/var/artwork/<rom_id>/` on its way to RomM.

## Streaming

    rom-hub stream archive-org msdos_Oregon_Trail_The_1990
    url     https://archive.org/details/msdos_Oregon_Trail_The_1990
    title   The Oregon Trail
    type    text/html
    emulator        dosbox
    stream_only     true

That is the whole command, on purpose. `romm-stream` is a separate service;
the Hub resolves and validates a target and hands it over rather than building
a second streaming transport of its own. Items Archive.org marks `stream_only`
are exactly the ones `import` refuses, so this is where they go.

## Emulator cores

    rom-hub cores list <plugin>
    rom-hub cores install <plugin> <core>

Cores land in `$ROM_HUB_HOME/var/cores/<plugin>/` by default. Point them
somewhere else — `/opt/romm-stream/cores` on the deployment target — with

    ROM_HUB_CORES_DIR=/opt/romm-stream/cores rom-hub cores install ...

A core download is gated by exactly the same code as a ROM import: the same
allowlist check, the same filename validation, the same containment check. It
is a binary from the internet landing on your disk, so it earns the same
treatment.

Archive.org does **not** offer cores. Its metadata names an emulator, not a
downloadable artifact, so implementing the capability there would mean
inventing a URL — and a plugin that fabricates a download target is one whose
refusals cannot be believed either.

### RomM connection settings

`import` and `enrich` need a RomM account permitted to upload. It is read from
the environment, never from a file in the repo:

| Variable | Meaning | Example |
|---|---|---|
| `ROMM_URL` | base URL of the RomM instance | `http://romm.example:8080` |
| `ROMM_USER` | RomM username | `admin` |
| `ROMM_PASSWORD` | that user's password | |

All three are required; both commands name whichever are missing and stop
before opening any connection. `ROM_HUB_HOME` (default `~/.rom-hub`) decides
where plugins, the job database, downloads, artwork and cores live;
`ROM_HUB_CORES_DIR` moves just the cores.

**The plugin never sees any of this.** The token, the upload, the artwork
fetch, the metadata write and the collection call are all host-side; a
plugin's whole involvement is returning a description. See the security model
below.

## Tests

    python -m pytest          # offline; live tests deselected
    python -m pytest -m live  # also hits the real Archive.org

On a host with no seccomp — Windows and macOS — the live test and the CLI both
need the opt-out, because the Hub otherwise refuses to run a plugin it cannot
confine:

    ROM_HUB_ALLOW_UNSANDBOXED=1 python -m pytest -m live -q

### What CI checks that a green exit code does not

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the suite on
`ubuntu-latest` and `windows-latest`, on Python 3.12 and 3.13. Two of this
project's guarantees are invisible to pytest's exit code, so
[`scripts/ci_gate.py`](scripts/ci_gate.py) asserts them against the junit
record instead:

- **A skipped containment test looks exactly like a passing one.** The seccomp
  tests carry `skipif(sys.platform != "linux")`. On Windows that skip is
  honest; on Linux it would mean `pyseccomp` failed to build and the suite went
  green having proven nothing about the claim in
  [Security model](#security-model). So the Linux job requires each of them
  **by name to have passed**, and the Windows job asserts that seccomp is the
  only thing skipped there.
- **`-m 'not live'` is a default, and defaults get overridden.** A gate proves
  the four network-hitting tests still carry the marker and that none of them
  is collected by default, so the suite's colour can never come to depend on a
  third-party service being up.

### Coverage

`pytest --cov` reports **86.6 %** on Linux and **86.9 %** on Windows (branch
coverage, `rom_hub` + `rom_hub_sdk`). CI enforces a floor and publishes the
per-module table to the run summary. One number in that table is misleading and
is explained rather than fixed: `rom_hub_sdk/runner.py` measures ~14 % because
it only ever executes inside the *plugin subprocess*, whose environment is
built from `{}` upward — instrumenting it would mean punching a hole in the
allowlist that `tests/test_hostile_plugin.py` exists to defend. It is covered
by tests; it is not covered by `coverage`.

### Proof against real backends

[`scripts/proof_matrix.py`](scripts/proof_matrix.py) runs the real import and
enrich pipelines against a live RomM, Gaseous and Retrom, and writes
[docs/PROOF.md](docs/PROOF.md) — backend × capability, with the evidence for
each cell and with **UNSUPPORTED** kept distinct from **FAIL**, so a backend
that genuinely has no collections never looks like a broken one.
[`scripts/proof-stack.compose.yml`](scripts/proof-stack.compose.yml) stands up
the three disposable servers it needs.

## Security model

Plugins run as subprocesses and are given **no RomM token and no filesystem
mount**, and the plugin API offers no way to open a socket. A plugin calls
`ctx.http`, which is an RPC back to the host; the host checks the URL against
the plugin's declared `network` allowlist before opening any connection.

That check is genuinely enforced **on the broker path**. `check_url` is
unavoidable en route to the only code that opens a socket, and the matcher is
adversarially tested. `tests/test_netpolicy.py` and
`test_disallowed_fetch_never_reaches_the_fetcher` in
`tests/test_broker_host.py` are the tests that hold it up; if either regresses,
the allowlist stops meaning anything at all.

**Every capability's return value gets the same check.** `ctx.http` is only the
first way a plugin can make the Hub reach a host; a `FetchPlan` URL, a
`MetadataPatch` artwork URL and a `StreamTarget` of kind `url` are the others,
and each one passes `check_url` against the same allowlist before anything is
fetched. Each is tested with an undeclared host, in `tests/test_broker_plan.py`,
`tests/test_broker_enrich.py`, `tests/test_stream.py` and `tests/test_cores.py`.
A `stream` target of kind `handle` may not itself be a URL, so the
discriminator cannot be lied about to skip the check.

Any filename a plugin supplies that the Hub writes to disk — a ROM, a cover, a
core — goes through one validator (`types.bare_filename`) and one containment
check (`paths.dest_in_job_dir`). They are shared functions, not three similar
copies: a containment rule that exists in three places is a containment rule
that is subtly different in one of them.

### What is confined, and what is not

**Network egress: now enforced.** The plugin subprocess installs a
**self-imposed seccomp filter on itself, before any plugin code is imported**
(`PR_SET_NO_NEW_PRIVS`, then `EPERM` for `socket`, `socketcall`, `connect`,
`sendto`, `sendmsg`). Restricting *yourself* needs no privilege, which is why
this works in an unmodified container. Measured on the deployment target inside
**default Docker** — no `--security-opt`, no added capabilities:

    NNP_OK → FILTER_LOADED → BLOCKED: PermissionError

A plugin that ignores `ctx.http` and reaches for `import socket` gets a
`PermissionError`, not a connection. The declared `network` allowlist is a
containment boundary now, not a statement of intent.

**Useful process spawn: enforced.** `execve` and `execveat` are denied, so a
plugin cannot shell out to something that would run unconfined. `clone` and
`fork` are deliberately *not* blocked — CPython uses `clone` for threads, so
denying it breaks the interpreter rather than the attacker. That is safe here
because a forked child **inherits the filter** and is confined anyway; there is
no escape by forking.

**Arbitrary file read: still NOT enforced.** seccomp cannot filter on a path.
It matches on syscall numbers and register values and cannot dereference a
pointer argument, so the filename passed to `openat` is invisible to it.
Confining reads needs a **mount namespace**. **A plugin can therefore still
read any file the Hub process can read**, including the Hub's own config and
database.

**Why not bubblewrap:** measured, not assumed. Inside default Docker,
`docker run --rm debian unshare --user --net` fails with `Operation not
permitted` — Docker's own default seccomp profile refuses the `unshare` that a
namespace sandbox is built on. Allowing it would mean
`--security-opt seccomp=unconfined` or `--privileged`, a larger hole than the
one being closed. Recorded here so it does not get re-litigated.

> ### ⚠️ Only install plugins you trust — scoped to what is left
>
> An untrusted plugin can no longer reach an undeclared host and can no longer
> exec its way out. It still runs with the Hub's own file-read reach: **a
> plugin can read any file the Hub process can read.** A manifest tells you
> where an honest plugin will go on the network; it tells you nothing about
> which of your files a dishonest one will open.

### Hosts that cannot install the filter

The filter is Linux-only and additionally needs `pyseccomp`. Where
`rom_hub.sandbox.probe()` reports it unavailable — Windows and macOS, most
obviously — the Hub **fails closed**: plugins refuse to run, and the error
names the override. Setting

    ROM_HUB_ALLOW_UNSANDBOXED=1

lifts the refusal and means exactly what it says: **no confinement at all**.
With it set, a hostile plugin can open its own sockets to undeclared hosts,
spawn processes, and read any file the Hub can. It is a development
convenience, never a deployment setting.

### The Hub holds a RomM token, and file reads are still unconfined

Phase 2 is the point at which the Hub first holds RomM credentials, and
`docs/DESIGN.md` named filesystem confinement a prerequisite for reaching it.
**That prerequisite has not been met** — a mount namespace is what confining
reads needs, and default Docker denies the `unshare` it is built on (measured;
see above). Phase 2 shipped anyway, and Phase 3 adds three capabilities on top
of the *same* token — `metadata` writes through it, `stream` and `cores` never
touch RomM at all, and none of them puts a new secret anywhere a plugin could
read. The exposure is unchanged, which is not the same as fixed. The honest
statement of where that leaves things:

- The RomM token is **never given to a plugin**. It is created inside the host
  process, used only by host-side code, and never crosses the pipe. Nothing a
  plugin can *call* returns it.
- The plugin subprocess **inherits almost nothing from the environment**.
  `subprocess.Popen` copies the parent's environment to the child by default,
  which would hand a plugin every secret the operator's shell happens to hold —
  needing no socket, no file, and no syscall the seccomp filter can see. So the
  child's environment is built from `{}` upward and only these are added
  (`broker/host.py`, `SAFE_ENV_VARS`):

  | | |
  |---|---|
  | Everywhere | `PATH` |
  | Windows | `SYSTEMROOT`, `COMSPEC`, `PATHEXT`, `TEMP`, `TMP` |
  | POSIX | `HOME`, `TMPDIR` |
  | Set by the host | `PYTHONIOENCODING=utf-8` |

  Nothing else: no `PYTHONPATH`, no `PYTHONHOME`, no user-defined variables,
  nothing secret-shaped. Measured on the development workstation, a plugin's
  visible environment went from **92 variables to 7**. This is an **allowlist**
  because a denylist cannot work here — the next secret is always the one
  nobody listed. Should a plugin ever legitimately need a variable, that is a
  manifest declaration to be designed, not a hole reopened here.
  `test_the_plugin_environment_is_an_allowlist_not_an_inheritance` asserts both
  that seeded secrets do not arrive *and* that the total count stays small, so
  a regression that reinstates inheritance fails loudly.
- **But a plugin can still read any file the Hub process can**, and on Linux
  that includes `/proc/<hub-pid>/environ`, which is same-uid readable. A
  hostile plugin cannot be *handed* the credentials and cannot pick them up by
  accident — it is not prevented from going and looking for them.

That last gap is why "install only plugins you trust" is stated as strongly as
it is, and it is not closed by anything in Phase 2. See
[docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model).
