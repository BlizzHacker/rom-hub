"""Print `source_id`s from a plugin search, so a bulk import can be scripted.

`rom-hub search` prints a human-readable table that deliberately has no
`source_id` column, so there is no way to pipe its output into `rom-hub
import`. Populating a demo library means running one search and importing
a hundred of its hits, and typing a hundred identifiers by hand is not a
thing anybody does.

This is not a second search implementation. It calls
`rom_hub.dispatcher.search_all` -- the same host API `rom-hub search`
calls, with the same sandbox policy, the same allowlist enforcement and
the same per-plugin isolation -- and prints one TSV row per result:

    slug<TAB>source_id<TAB>platform<TAB>stream_only<TAB>title

Every identifier it prints came out of a plugin subprocess. Nothing here
knows what Archive.org's API looks like.

Usage:
    python scripts/showcase_discover.py <query> [--only slug] [--limit N]

An empty query is meaningful for plugins that support browsing (Archive.org
answers it by listing the configured collections), so `""` is allowed.
"""

from __future__ import annotations

import argparse
import sys

from rom_hub.broker.fetcher import HttpxFetcher
from rom_hub.cli import allow_unsandboxed, default_root, prepare_assets, prepare_secrets
from rom_hub.dispatcher import search_all
from rom_hub.registry import Registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--only", default=None, help="restrict to one plugin slug")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    plugins = Registry(default_root()).installed()
    if args.only:
        plugins = [p for p in plugins if p.slug == args.only]

    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            plugins,
            fetcher=fetcher,
            query=args.query,
            platform=args.platform,
            limit=args.limit,
            allow_unsandboxed=allow_unsandboxed(),
            assets_for=prepare_assets,
            secrets_for=prepare_secrets,
        )
    finally:
        fetcher.close()

    for result in outcome.results:
        title = (result.title or "").replace("\t", " ").replace("\n", " ")
        print(
            "\t".join(
                (
                    result.plugin,
                    result.source_id or "",
                    result.platform or "",
                    result.extra.get("stream_only", "false"),
                    title,
                )
            )
        )
    print(
        f"# {outcome.responded} of {outcome.total} sources responded, "
        f"{len(outcome.results)} results",
        file=sys.stderr,
    )
    for status in outcome.statuses:
        if not status.ok:
            print(f"# ! {status.slug}: {status.error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
