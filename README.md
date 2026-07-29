# RomM Hub

qBittorrent-style plugins for [RomM](https://github.com/rommapp/romm), as a
sidecar. RomM itself is never modified.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and
[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md) for the
deferred federation and multiplayer work.

## Status

**Phase 1** — plugin engine, broker, and search. No import, no web UI yet.

## Quick start

    python -m pip install -e ".[dev]"
    python -m romm_hub.cli plugin install ./plugins-dev/archive-org
    python -m romm_hub.cli search "oregon trail" --limit 5

## Tests

    python -m pytest          # offline; live tests deselected
    python -m pytest -m live  # also hits the real Archive.org

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

> ### ⚠️ Phase 1 does not sandbox plugins
>
> The plugin subprocess is a plain `Popen` of the Python interpreter — no
> namespace, no seccomp filter, no job object, no separate uid. Plugin code
> inherits everything the host process can do, so a **hostile** plugin can
> ignore `ctx.http`, open its own socket to an undeclared host, read files
> outside its directory, and spawn processes. None of that crosses the broker,
> so none of it is checked.
>
> In Phase 1 the allowlist therefore constrains *cooperative* plugins and
> documents intent. It is not a containment boundary. **Only install plugins
> you trust.**
>
> Real isolation (bubblewrap/nsjail `--unshare-net --ro-bind`, or the container
> boundary) is a blocking prerequisite for Phase 2, which is where the Hub
> first holds a RomM admin token. See
> [docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model).
