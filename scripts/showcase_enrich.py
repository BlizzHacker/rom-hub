"""Fill in missing cover art by trying each metadata plugin in turn.

Populating a library one `rom-hub enrich` at a time is fine for one rom
and unreasonable for three hundred, and which plugin can identify a given
rom is not knowable in advance -- `libretro-thumbnails` has box art for
commercial titles under their No-Intro names, `openvgdb` has names and
some art, `archive-org` has whatever the item itself carries, and none of
them has everything.

So this walks the roms that have no cover and tries the plugins in the
order given, stopping at the first one that attaches artwork.

    python scripts/showcase_enrich.py --plugin libretro-thumbnails \\
                                      --plugin openvgdb

It **shells out to `rom-hub enrich`**, one process at a time. That is the
point: nothing here writes to the library itself, so every cover in the
finished screenshots went through exactly the command a reader can run.
Serial, because the job queue is one SQLite file without WAL and the
library server registers an upload by scanning -- two of these at once is
how a batch loses roms to "uploaded, but did not appear".

`--source-id-from-filename` is for `archive-org`, whose plugin refuses to
guess which item a rom came from: it passes the rom's filename stem as
`--source-id`, which is the Archive.org identifier only when the import
came from there, and a harmless refusal otherwise.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from rom_hub import backends


def covers(rom: dict) -> bool:
    return bool(str(rom.get("path_cover_small") or "").strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin", action="append", required=True)
    ap.add_argument("--platform", action="append", default=None, help="fs_slug filter")
    ap.add_argument("--limit", type=int, default=0, help="stop after N roms")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    backend = backends.load("romm")
    try:
        todo: list[tuple[int, str]] = []
        for platform in backend.client.list_platforms():
            slug = str(platform.get("fs_slug") or "")
            if args.platform and slug not in args.platform:
                continue
            for rom in backend.list_roms(platform.get("id")):
                if not covers(rom):
                    todo.append((rom["id"], str(rom.get("fs_name") or "")))
    finally:
        backend.close()

    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} rom(s) without a cover", flush=True)
    if args.dry_run:
        return 0

    fixed = 0
    for rom_id, filename in todo:
        for plugin in args.plugin:
            cmd = ["rom-hub", "enrich", plugin, str(rom_id)]
            if plugin == "archive-org":
                stem = filename.rsplit(".", 1)[0]
                if not stem:
                    continue
                cmd += ["--source-id", stem]
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
            out = proc.stdout + proc.stderr
            if proc.returncode == 0 and "artwork" in out:
                fixed += 1
                print(f"rom {rom_id}: cover from {plugin}", flush=True)
                break
        else:
            print(f"rom {rom_id}: no plugin had art for {filename!r}", flush=True)

    print(f"{fixed} of {len(todo)} gained a cover", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
