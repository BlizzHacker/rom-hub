# ROM Hub

qBittorrent-style plugins for a self-hosted ROM library, as a sidecar. The
library server is never modified.

[RomM](https://github.com/rommapp/romm) is the backend that ships, and the
default. It is not the only one the plugins work with: a plugin returns a
*description* of work — which files to fetch, which metadata to set — and the
Hub executes it, so nothing in a plugin has ever known which server is on the
other side. `ROM_HUB_BACKEND` picks; `rom-hub backend info` says what the
chosen one can do.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and
[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md) for the
deferred federation and multiplayer work.

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
| `importer` | `rom-hub import <plugin> <source_id>` | plan → download → hash-dedup → chunked upload → collection |
| `metadata` | `rom-hub enrich <plugin> <rom_id>` | plugin describes metadata, the Hub fetches the artwork and writes to RomM |
| `stream` | `rom-hub stream <plugin> <source_id>` | resolves one item to a validated stream target and prints it |
| `cores` | `rom-hub cores list\|install <plugin> [<core>]` | lists a plugin's emulator cores, downloads one |

Plus the broker, a seccomp-confined plugin subprocess, and a job queue that
survives a restart. No web UI yet.

## Which library server

`ROM_HUB_BACKEND` selects it; `romm` is the default and, today, the only one
built. Its connection settings are `ROMM_URL`, `ROMM_USER` and `ROMM_PASSWORD`
(`ROM_HUB_BACKEND_URL`/`_USER`/`_PASSWORD` also work, for a deployment that
would rather not name a product in its unit file).

    rom-hub backend info

    backend          romm
    selected by      default (romm)
    available        romm
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

**A backend that cannot do something says so before it costs anything.** If the
active backend has no collections, `rom-hub import --collection "Shooters"`
refuses immediately — before a plugin subprocess is started, before a
connection is opened, before a byte is downloaded — and names the backend and
the capability. The alternative is a four-gigabyte download followed by a 404
from an endpoint the operator has never heard of, with the ROM half-filed. The
same refusal covers a collection the *plugin's* plan named, which is not the
same path and would otherwise slip through.

## Quick start

    python -m pip install -e ".[dev]"
    python -m rom_hub.cli plugin install ./plugins-dev/archive-org
    python -m rom_hub.cli search "oregon trail" --limit 5

On Linux that install also pulls `pyseccomp`, which is what lets the plugin
subprocess confine itself. If it is missing, `rom-hub` refuses to run plugins
rather than running them unconfined (see below).

## Importing

`import` takes a plugin's own id for an item and puts the ROM in RomM. The
plugin only says *what* to fetch; the Hub downloads it, hashes it, checks it is
not already in the library, and uploads it.

    rom-hub import archive-org rubik_202308
    rom-hub import archive-org rubik_202308 --platform dos --collection "Archive.org"

`--platform` and `--collection` override what the plugin planned. They retarget
where a ROM files; they cannot make the Hub fetch from anywhere the plugin's
manifest does not already allow, and they cannot override a plugin's refusal —
if a plugin says an emulator "needs mapping", the fix is to add the mapping,
not to name a platform by hand and leave the gap open for the next person.

An import that is already in RomM is reported as a duplicate and **not**
uploaded. Matching is by file hash, not by name.

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
| `ROMM_URL` | base URL of the RomM instance | `http://192.168.0.104:8080` |
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
