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

Plugins are untrusted git repos run as subprocesses with **no RomM token, no
filesystem mount, and no network sockets**. A plugin calls `ctx.http`, which is
an RPC back to the host; the host checks the URL against the plugin's declared
`network` allowlist before opening any connection.

`tests/test_netpolicy.py` and `test_disallowed_fetch_never_reaches_the_fetcher`
in `tests/test_broker_host.py` are the tests that hold this claim up. If either
regresses, the permission model is decorative.
