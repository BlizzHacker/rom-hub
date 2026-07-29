"""romm-hub command line.

Phase 1 surface: install plugins, list them, and search across them.
"""

import argparse
import os
import sys
from pathlib import Path

from .broker.fetcher import HttpxFetcher
from .dispatcher import search_all
from .registry import Registry, RegistryError


def default_root() -> Path:
    return Path(os.environ.get("ROMM_HUB_HOME", Path.home() / ".romm-hub"))


def _cmd_plugin_install(args) -> int:
    reg = Registry(default_root())
    plugin = reg.install(args.source, args.ref)
    caps = ", ".join(sorted(plugin.manifest.capabilities))
    print(f"installed {plugin.slug} {plugin.manifest.version} (capabilities: {caps})")
    print(f"  network allowlist: {plugin.manifest.network or '(none)'}")
    return 0


def _cmd_plugin_list(args) -> int:
    plugins = Registry(default_root()).installed()
    if not plugins:
        print("no plugins installed")
        return 0
    for p in plugins:
        state = "enabled" if p.enabled else "disabled"
        caps = ",".join(sorted(p.manifest.capabilities))
        print(f"{p.slug:<20} {p.manifest.version:<10} {state:<9} [{caps}]")
    return 0


def _cmd_plugin_enable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, True)
    print(f"enabled {args.slug}")
    return 0


def _cmd_plugin_disable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, False)
    print(f"disabled {args.slug}")
    return 0


def _cmd_search(args) -> int:
    plugins = Registry(default_root()).installed()
    searchable = [p for p in plugins if p.enabled and "search" in p.manifest.capabilities]
    if not searchable:
        print("no plugins available for search — install one with 'romm-hub plugin install'")
        return 0

    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            plugins,
            fetcher=fetcher,
            query=args.query,
            platform=args.platform,
            limit=args.limit,
        )
    finally:
        fetcher.close()

    for r in outcome.results:
        size = f"{r.size_bytes / 1_048_576:.1f} MB" if r.size_bytes else "-"
        flag = " [stream-only]" if r.extra.get("stream_only") == "true" else ""
        print(f"{r.plugin:<14} {r.platform or '-':<12} {size:>10}  {r.title}{flag}")

    print()
    print(f"{outcome.responded} of {outcome.total} sources responded, {len(outcome.results)} results")
    for status in outcome.statuses:
        if not status.ok:
            print(f"  ! {status.slug}: {status.error}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="romm-hub", description="RomM plugin host")
    sub = parser.add_subparsers(dest="command", required=True)

    plugin = sub.add_parser("plugin", help="manage plugins")
    psub = plugin.add_subparsers(dest="plugin_command", required=True)

    install = psub.add_parser("install", help="install a plugin from a git repo or path")
    install.add_argument("source")
    install.add_argument("--ref", default=None, help="tag or branch to pin")
    install.set_defaults(func=_cmd_plugin_install)

    listing = psub.add_parser("list", help="list installed plugins")
    listing.set_defaults(func=_cmd_plugin_list)

    enable = psub.add_parser("enable")
    enable.add_argument("slug")
    enable.set_defaults(func=_cmd_plugin_enable)

    disable = psub.add_parser("disable")
    disable.add_argument("slug")
    disable.set_defaults(func=_cmd_plugin_disable)

    search = sub.add_parser("search", help="search across enabled plugins")
    search.add_argument("query")
    search.add_argument("--platform", default=None)
    search.add_argument("--limit", type=int, default=25)
    search.set_defaults(func=_cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
