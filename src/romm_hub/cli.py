"""romm-hub command line.

Phase 1 surface: install plugins, list them, and search across them.
"""

import argparse
import os
import sys
from pathlib import Path

from .broker.fetcher import HttpxFetcher
from .catalog import CatalogError, load_catalog, symbol_for
from .dispatcher import search_all
from .manifest import ManifestError
from .registry import Registry, RegistryError
from .sandbox import probe

CATALOG_PATH = Path(__file__).resolve().parents[2] / "catalog" / "plugins.json"


def default_root() -> Path:
    return Path(os.environ.get("ROMM_HUB_HOME", Path.home() / ".romm-hub"))


def allow_unsandboxed() -> bool:
    """Whether the operator has opted out of the fail-closed sandbox policy.

    Read at call time, not import time, so tests and shells can flip it.
    """
    return os.environ.get("ROMM_HUB_ALLOW_UNSANDBOXED", "") == "1"


def _catalog_entry(slug: str):
    """Resolve a bare slug through the directory, or return None.

    A source that looks like a URL or an existing path is used as given --
    the catalog is a convenience, never a required intermediary.
    """
    if "/" in slug or "\\" in slug or Path(slug).exists():
        return None
    try:
        entries = load_catalog(CATALOG_PATH)
    except CatalogError:
        return None
    return next((e for e in entries if e.slug == slug), None)


def _cmd_plugin_browse(args) -> int:
    try:
        entries = load_catalog(CATALOG_PATH)
    except CatalogError as exc:
        print(f"catalog unreadable: {exc}", file=sys.stderr)
        return 1
    print(f"{'':<3}{'SLUG':<16} {'VERSION':<9} {'CAPABILITIES':<22} AUTHOR")
    for e in sorted(entries, key=lambda x: x.slug):
        caps = ",".join(e.capabilities)
        mark = symbol_for(e.status, getattr(sys.stdout, "encoding", None))
        print(f"{mark:<3}{e.slug:<16} {e.version:<9} {caps:<22} {e.author}")
        print(f"   {e.repository}")
        # Shown before install so the permission ask is a decision, not a
        # surprise discovered afterwards.
        print(f"   requests network: {', '.join(e.network) or '(none)'}")
    print()
    print(f"{len(entries)} plugin(s). Install with: romm-hub plugin install <slug>")
    return 0


def _cmd_plugin_install(args) -> int:
    reg = Registry(default_root())
    source, ref = args.source, args.ref
    entry = _catalog_entry(source)
    if entry is not None:
        source, ref = entry.install, ref or entry.ref
        print(f"resolved {entry.slug!r} from the catalog: {source} @ {ref}")
    plugin = reg.install(source, ref)
    caps = ", ".join(sorted(plugin.manifest.capabilities))
    print(f"installed {plugin.slug} {plugin.manifest.version} (capabilities: {caps})")
    print(f"  pinned commit: {plugin.commit or '(unknown)'}")
    print(f"  declared network allowlist: {plugin.manifest.network or '(none)'}")
    # An operator approving an install must be told exactly how much of that
    # allowlist is a boundary and how much is a declaration of intent, and the
    # answer depends on whether this host can confine the subprocess at all.
    available, reason = probe()
    if available:
        print("  note: the plugin subprocess installs a seccomp filter on itself")
        print("        before importing any plugin code, so network egress and")
        print("        process spawn are blocked outright — the allowlist above is")
        print("        enforced, not advisory. File reads are NOT confined: seccomp")
        print("        cannot filter on a path, so a plugin can still read any file")
        print("        this process can. Install only plugins you trust.")
    else:
        print("  note: this host cannot confine plugins, so plugins will refuse to")
        print("        run unless ROMM_HUB_ALLOW_UNSANDBOXED=1 is set. With it set")
        print("        there is no confinement at all: a hostile plugin can ignore")
        print("        the allowlist above, open its own sockets, read any file this")
        print("        process can, and spawn processes. Install only plugins you")
        print("        trust.")
        print(f"        reason: {reason}")
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
            allow_unsandboxed=allow_unsandboxed(),
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
    install.add_argument("source", help="a catalog slug, git URL, or local path")
    install.add_argument(
        "--ref", default=None, help="branch, tag, or commit SHA to install"
    )
    install.set_defaults(func=_cmd_plugin_install)

    browse = psub.add_parser("browse", help="list plugins in the directory")
    browse.set_defaults(func=_cmd_plugin_browse)

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
    except (RegistryError, ManifestError, CatalogError, OSError) as exc:
        # ManifestError escapes a bad manifest on an otherwise clean install,
        # and OSError is what a read-only or nonexistent ROMM_HUB_HOME gives
        # from Registry.__init__'s mkdir. Neither deserves a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
