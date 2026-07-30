# libretro cheats plugin for ROM Hub

Implements the RPP v1 `assets` capability: RetroArch cheat files — the
Game Genie and Action Replay style code lists RetroArch loads per game.

| Capability | Source | Does |
|---|---|---|
| `assets` (`cheat`) | `github.com/libretro/libretro-database`, `cht/` | lists cheat files for the systems you choose; the **Hub** downloads the one you pick |

## Install

    rom-hub plugin install ./plugins-dev/libretro-cheats
    # first run tells you which systems exist and asks you to choose one
    rom-hub assets list libretro-cheats
    rom-hub assets install libretro-cheats "cht/Nintendo - Game Boy/Tetris (World) (Rev A).cht"

Files land in the directory configured for the `cheat` kind — by default
`$ROM_HUB_HOME/var/assets/cheats/libretro-cheats/`. Point `ROM_HUB_ASSETS_DIR`
at your RetroArch configuration directory and they land in `cheats/` where
RetroArch already looks; `ROM_HUB_CHEATS_DIR` overrides that one kind
outright.

## Licensing, in plain language

**CC-BY-SA-4.0 — Creative Commons Attribution-ShareAlike 4.0 International.**
`libretro-database` carries the full licence text in `LICENSE` at its
repository root, and GitHub's own detection agrees (SPDX `CC-BY-SA-4.0`). Read
from the repository, not from a badge.

What that means in practice: you may use, modify and redistribute these cheat
files, including commercially, provided you give attribution **and** license
any redistributed derivative under the same terms. ShareAlike is the
difference from the overlays plugin's CC-BY-4.0 — if you publish a modified
cheat collection, it has to carry CC-BY-SA-4.0 too.

**One caveat, stated because it is real.** The source repository's README notes
that much of `libretro-database` is imported from third parties — No-Intro,
Redump, TOSEC, GameTDB — and it does not say which upstream terms attach to
which subtree. That caveat is about the **DAT and metadata imports** (`dat/`,
`metadat/`, `rdb/`). This plugin only touches `cht/`, which is contributed
directly rather than imported from those projects, and which the
repository-level `LICENSE` covers with no carve-out naming it. If that ever
changes upstream, this plugin should be the thing that changes with it.

## The size problem, and why this plugin makes you choose

`libretro-database` is **795 MB**. The `cht/` tree alone holds tens of
thousands of files across 44 systems — 2,265 for the NES, 2,050 for the
PlayStation.

**Nothing here downloads any of that.** Listing one system is a single Git
Trees API call (704 KB of JSON for all 2,265 NES entries), and installing is
one `raw.githubusercontent.com` GET for a file of a few hundred bytes.

Because "every system at once" is far past the 512 assets a plugin may return
— and is not something anyone actually wants — this plugin requires you to
choose. With no `systems` set, the first run makes one cheap call, lists the
44 system directories that exist, and asks. An empty catalogue would have been
technically true and useless.

### The trap this plugin was built around

GitHub's **contents API truncates a directory listing at 1,000 entries with no
error and no flag.** `/contents/cht/Nintendo - Nintendo Entertainment System`
returns 1,000 of 2,265 files and answers 200. A plugin built on it would have
offered a third of the NES catalogue and looked like it was working.

The Git Trees API returns all 2,265, sets a `truncated` boolean when it cannot,
and is *smaller* on the wire (704 KB against 1.4 MB) because it carries no
per-entry URL block. This plugin refuses a truncated listing outright rather
than showing you part of a catalogue as though it were all of it. See
`libretro_cheats/github.py`.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `systems` | `list[str]` | `[]` | which `cht/` system directories to offer |
| `match` | `str` | `""` | keep only files whose name contains this, case-insensitive |

System names are the directory names exactly as the repository spells them:
`Nintendo - Game Boy`, `Sony - PlayStation`, `Sega - Mega Drive - Genesis`, and
so on. The first run prints the list.

`match` is what makes a big system usable — the NES directory is 2,265 files,
so `match = "zelda"` is the difference between a catalogue and a wall.

No credentials. The service is unauthenticated and this plugin sends nothing
but a GET.

## What this does not promise

**No integrity digest.** The plugin pins `master`, not a commit, so cheats
added upstream appear. What you get is HTTPS to a host this plugin's manifest
declares, with every redirect re-checked against that same allowlist by the
Hub. If you want a specific reviewed revision instead, that is what
`[[data_assets]]`'s mandatory sha256 is for, and it is deliberately a
different mechanism.

**The Hub does not read the cheats.** A `.cht` is text RetroArch parses;
nothing here checks that the codes work, that they match your dump, or that
they are for the game the filename claims.
