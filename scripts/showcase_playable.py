"""How many roms in RomM can RomM's own web player actually run.

A rom filed under a platform RomM has no emulator core for is imported and
dead: it appears in the library, and clicking it does nothing. Counting
those separately is the difference between a showcase and an advert.

The list of playable platforms is **not** hardcoded here. It is read out of
the RomM server's own frontend bundle at run time -- the `slug -> [core]`
map its player consults -- so this reports what the server in front of you
can do rather than what some other version could when this file was
written.

    python scripts/showcase_playable.py

Needs `ROMM_URL`, `ROMM_USER`, `ROMM_PASSWORD`, like every other RomM
command here.
"""

from __future__ import annotations

import re
import sys

import httpx

from rom_hub import backends, env


def playable_slugs(base_url: str) -> set[str]:
    """The platform slugs RomM's player has a core for, from its own bundle.

    The bundle is minified, so the map's *name* is gone; what survives is
    its shape -- `slug:["core", ...]` entries, one of which is
    `genesis_plus_gx`. Anchoring on a core name and then walking out to the
    enclosing object literal is what makes this survive a rebuild with
    different mangled identifiers.
    """
    index = httpx.get(f"{base_url.rstrip('/')}/", timeout=30).text
    match = re.search(r'src="(/assets/index-[^"]+\.js)"', index)
    if not match:
        raise SystemExit("could not find the frontend bundle in RomM's index.html")
    src = httpx.get(f"{base_url.rstrip('/')}{match.group(1)}", timeout=60).text

    anchor = src.find("genesis_plus_gx")
    if anchor < 0:
        raise SystemExit("no core map in the bundle (RomM changed its player?)")

    start, depth = anchor, 0
    while start > 0:
        start -= 1
        if src[start] == "}":
            depth += 1
        elif src[start] == "{":
            if depth == 0:
                break
            depth -= 1
    end, depth = anchor, 0
    while end < len(src):
        if src[end] == "{":
            depth += 1
        elif src[end] == "}":
            if depth == 0:
                break
            depth -= 1
        end += 1

    chunk = src[start : end + 1]
    pairs = re.findall(r'(?:"([a-z0-9._-]+)"|\b([a-z][a-z0-9_-]*))\s*:\s*\[[`"\']', chunk)
    return {a or b for a, b in pairs}


def main() -> int:
    base = env.get("ROMM_URL")
    if not base:
        raise SystemExit("ROMM_URL is not set")

    playable = playable_slugs(base)
    print(f"RomM's player has a core for {len(playable)} platform slugs")

    backend = backends.load("romm")
    try:
        rows = []
        for platform in backend.client.list_platforms():
            slug = str(platform.get("fs_slug") or "")
            roms = backend.list_roms(platform.get("id"))
            if roms:
                rows.append((slug, len(roms), slug in playable))
    finally:
        backend.close()

    live = sum(n for _, n, ok in rows if ok)
    dead = sum(n for _, n, ok in rows if not ok)
    print(f"{live} roms on a platform the player can run")
    print(f"{dead} roms catalogued only -- no core, so the player will not start them")
    print()
    print(f"{'PLATFORM':<22} {'ROMS':>5}  PLAYABLE")
    for slug, n, ok in sorted(rows, key=lambda r: (-r[2], -r[1])):
        print(f"{slug:<22} {n:>5}  {'yes' if ok else 'NO'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
