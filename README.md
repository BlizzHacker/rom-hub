# RomM Hub

qBittorrent-style plugins for [RomM](https://github.com/rommapp/romm), as a
sidecar. RomM itself is never modified.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and
[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md) for the
deferred federation and multiplayer work.

## Status

**Phase 2** — plugin engine, broker, search, a seccomp-confined plugin
subprocess, and **import**: plan, download, hash-dedup, chunked upload, and a
job queue that survives a restart. No web UI yet.

## Quick start

    python -m pip install -e ".[dev]"
    python -m romm_hub.cli plugin install ./plugins-dev/archive-org
    python -m romm_hub.cli search "oregon trail" --limit 5

On Linux that install also pulls `pyseccomp`, which is what lets the plugin
subprocess confine itself. If it is missing, `romm-hub` refuses to run plugins
rather than running them unconfined (see below).

## Importing

`import` takes a plugin's own id for an item and puts the ROM in RomM. The
plugin only says *what* to fetch; the Hub downloads it, hashes it, checks it is
not already in the library, and uploads it.

    romm-hub import archive-org rubik_202308
    romm-hub import archive-org rubik_202308 --platform dos --collection "Archive.org"

`--platform` and `--collection` override what the plugin planned. They retarget
where a ROM files; they cannot make the Hub fetch from anywhere the plugin's
manifest does not already allow, and they cannot override a plugin's refusal —
if a plugin says an emulator "needs mapping", the fix is to add the mapping,
not to name a platform by hand and leave the gap open for the next person.

An import that is already in RomM is reported as a duplicate and **not**
uploaded. Matching is by file hash, not by name.

    romm-hub jobs                # every import job and its state
    romm-hub jobs --state FAILED # just the ones that went wrong, with reasons

Job state lives in `$ROMM_HUB_HOME/var/jobs.db` and downloads land in
`$ROMM_HUB_HOME/var/downloads/`, so an interrupted multi-GB import is resumed
rather than restarted.

### RomM connection settings

`import` needs a RomM account permitted to upload. It is read from the
environment, never from a file in the repo:

| Variable | Meaning | Example |
|---|---|---|
| `ROMM_URL` | base URL of the RomM instance | `http://192.168.0.104:8080` |
| `ROMM_USER` | RomM username | `admin` |
| `ROMM_PASSWORD` | that user's password | |

All three are required; `import` names whichever are missing and stops before
opening any connection. `ROMM_HUB_HOME` (default `~/.romm-hub`) decides where
plugins, the job database, and downloads live.

**The plugin never sees any of this.** The token, the upload, and the
collection call are all host-side; a plugin's whole involvement is returning a
`FetchPlan`. See the security model below.

## Tests

    python -m pytest          # offline; live tests deselected
    python -m pytest -m live  # also hits the real Archive.org

On a host with no seccomp — Windows and macOS — the live test and the CLI both
need the opt-out, because the Hub otherwise refuses to run a plugin it cannot
confine:

    ROMM_HUB_ALLOW_UNSANDBOXED=1 python -m pytest -m live -q

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
`romm_hub.sandbox.probe()` reports it unavailable — Windows and macOS, most
obviously — the Hub **fails closed**: plugins refuse to run, and the error
names the override. Setting

    ROMM_HUB_ALLOW_UNSANDBOXED=1

lifts the refusal and means exactly what it says: **no confinement at all**.
With it set, a hostile plugin can open its own sockets to undeclared hosts,
spawn processes, and read any file the Hub can. It is a development
convenience, never a deployment setting.

### Phase 2 holds a RomM token, and file reads are still unconfined

Phase 2 is the point at which the Hub first holds RomM credentials, and
`docs/DESIGN.md` named filesystem confinement a prerequisite for reaching it.
**That prerequisite has not been met** — a mount namespace is what confining
reads needs, and default Docker denies the `unshare` it is built on (measured;
see above). Phase 2 shipped anyway, so the honest statement of where that
leaves things:

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
