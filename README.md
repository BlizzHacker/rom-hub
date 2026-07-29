# RomM Hub

qBittorrent-style plugins for [RomM](https://github.com/rommapp/romm), as a
sidecar. RomM itself is never modified.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and
[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md) for the
deferred federation and multiplayer work.

## Status

**Phase 1.5** — plugin engine, broker, search, and a seccomp-confined plugin
subprocess. No import, no web UI yet.

## Quick start

    python -m pip install -e ".[dev]"
    python -m romm_hub.cli plugin install ./plugins-dev/archive-org
    python -m romm_hub.cli search "oregon trail" --limit 5

On Linux that install also pulls `pyseccomp`, which is what lets the plugin
subprocess confine itself. If it is missing, `romm-hub` refuses to run plugins
rather than running them unconfined (see below).

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

Filesystem confinement remains a blocking prerequisite for Phase 2, which is
where the Hub first holds a RomM admin token. See
[docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model).
