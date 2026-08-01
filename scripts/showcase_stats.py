"""Count what is actually in each backend, and how much of it has a cover.

The number this exists for is the **cover-art ratio**: how many tiles in
the library show a picture rather than the server's `?` placeholder. A
showcase that implies every tile has art when a third of them do not is
the thing this script exists to make impossible to do by accident.

    python scripts/showcase_stats.py            # every configured backend
    python scripts/showcase_stats.py --backend romm

Counting goes through `rom_hub.backends`, the same code path the importer
uses, so the numbers come from the servers rather than from a tally kept
while importing.

Cover detection, per backend:

* **RomM** -- `path_cover_small` non-empty on the rom record. RomM writes
  that path when, and only when, a cover file exists on disk for the rom.
* **Retrom** -- `cover_url` set on the game's metadata row.
* **Gaseous** -- not reported. Gaseous exposes no metadata-write API, so
  the Hub never puts a cover there; any art in its UI is Gaseous's own
  IGDB lookup, which is not this project's work to claim.
"""

from __future__ import annotations

import argparse

from rom_hub import backends
from rom_hub.backends import BackendNotConfigured


def _platforms(backend) -> list[dict]:
    return backend.client.list_platforms()


def romm_stats(backend) -> dict:
    total = covered = 0
    by_platform: dict[str, tuple[int, int]] = {}
    for platform in _platforms(backend):
        roms = backend.list_roms(platform.get("id"))
        have = sum(1 for r in roms if str(r.get("path_cover_small") or "").strip())
        if roms:
            by_platform[str(platform.get("fs_slug") or platform.get("id"))] = (
                len(roms),
                have,
            )
        total += len(roms)
        covered += have
    return {"total": total, "covered": covered, "by_platform": by_platform}


def retrom_stats(backend) -> dict:
    total = covered = 0
    by_platform: dict[str, tuple[int, int]] = {}
    for platform in _platforms(backend):
        games = backend.list_roms(platform.get("id"))
        have = 0
        for game in games:
            try:
                meta = backend.client.game_metadata(game.get("id"))
            except Exception:  # noqa: BLE001
                meta = {}
            if str(meta.get("cover_url") or "").strip():
                have += 1
        if games:
            name = str(platform.get("name") or platform.get("path") or platform.get("id"))
            by_platform[name] = (len(games), have)
        total += len(games)
        covered += have
    return {"total": total, "covered": covered, "by_platform": by_platform}


def gaseous_stats(backend) -> dict:
    """Distinct roms, counted once.

    Not `backend.list_roms(platform)` summed over platforms: that call
    deliberately widens to Gaseous' "unknown" platform 0, because Gaseous
    derives a rom's platform from its own file signature and puts most
    imports there. Summing it over 100+ platform ids therefore counts the
    same rom 100+ times. The library listing is games-then-roms, deduped
    on rom id, which is what "how many roms are in there" means.
    """
    client = backend.client
    seen: set[int] = set()
    by_platform: dict[str, tuple[int, int]] = {}
    names = {p.get("id"): str(p.get("name") or p.get("id")) for p in _platforms(backend)}
    for game in client.list_games():
        map_id = game.get("metadataMapId")
        if not isinstance(map_id, int) or isinstance(map_id, bool):
            continue
        for platform_id in (game.get("platformIds") or [0]):
            for rom in client.roms_for_game(map_id, platform_id):
                rom_id = rom.get("id")
                if rom_id in seen:
                    continue
                seen.add(rom_id)
                label = names.get(rom.get("platformId"), "Unknown Platform")
                n, have = by_platform.get(label, (0, 0))
                by_platform[label] = (n + 1, have)
    return {"total": len(seen), "covered": None, "by_platform": by_platform}


READERS = {"romm": romm_stats, "gaseous": gaseous_stats, "retrom": retrom_stats}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", action="append", default=None)
    ap.add_argument("--platforms", action="store_true", help="break down by platform")
    args = ap.parse_args()

    for name in args.backend or ["romm", "gaseous", "retrom"]:
        try:
            backend = backends.load(name)
        except BackendNotConfigured as exc:
            print(f"{name}: not configured ({exc})")
            continue
        try:
            stats = READERS[name](backend)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: could not be counted: {type(exc).__name__}: {exc}")
            continue
        finally:
            backend.close()

        total, covered = stats["total"], stats["covered"]
        if covered is None:
            print(f"{name:<8} {total:>4} roms   (the Hub cannot write cover art here)")
        else:
            pct = (100.0 * covered / total) if total else 0.0
            print(
                f"{name:<8} {total:>4} roms   {covered:>4} with cover art  {pct:5.1f}%"
            )
        if args.platforms:
            for slug, (n, have) in sorted(
                stats["by_platform"].items(), key=lambda kv: -kv[1][0]
            ):
                print(f"           {slug:<22} {n:>4} roms  {have:>4} with cover")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
