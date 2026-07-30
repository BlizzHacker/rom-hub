# libretro overlays plugin for ROM Hub

Implements the RPP v1 `assets` capability: RetroArch overlays — the bezels and
on-screen gamepads that fill the empty space around a 4:3 game on a 16:9
screen.

| Capability | Source | Does |
|---|---|---|
| `assets` (`overlay`) | `github.com/libretro/common-overlays` | lists self-contained overlays; the **Hub** downloads the one you pick, with its images |

## Install

    rom-hub plugin install ./plugins-dev/libretro-overlays
    rom-hub assets list libretro-overlays --kind overlay
    rom-hub assets install libretro-overlays gamepads/lite/SNES.cfg

Files land in the directory configured for the `overlay` kind — by default
`$ROM_HUB_HOME/var/assets/overlays/libretro-overlays/`. Point
`ROM_HUB_ASSETS_DIR` at your RetroArch configuration directory and they land
in `overlays/` where RetroArch already looks; `ROM_HUB_OVERLAYS_DIR` overrides
that one kind outright.

## Licensing, in plain language

**CC-BY-4.0 — Creative Commons Attribution 4.0 International.** The repository
carries the full licence text in `COPYING`, and GitHub's own detection agrees
(SPDX `CC-BY-4.0`). This was read from the repository rather than taken from a
badge.

What that means for you in practice: you may use, modify and redistribute
these overlays, including commercially, provided you give attribution to the
creators and indicate any changes. The Hub does not add attribution to the
files for you — if you redistribute an overlay, that obligation is yours.

## The catch: only 49 of the 310 overlays can be installed

This is the honest part, and it is a limitation of the *format* against ROM
Hub's containment rules, not of the licence.

A RetroArch overlay is a `.cfg` plus the images it references, and the `.cfg`
names them relative to itself. The dominant form in this repository is a
subdirectory:

    overlay0_desc0_overlay = img/dpad-left.png

A `FetchPlan` cannot express that. Every filename the Hub writes must be a
bare name — that is the rule that stops a plugin writing outside the directory
chosen for it, and it is not worth trading a containment guarantee for a file
layout.

So this plugin offers only the **self-contained** overlays: those whose `.cfg`
references its images as bare names in the same directory. Measured against
the live repository on 2026-07-29:

| | count |
|---|---|
| `.cfg` files in the repository | 310 |
| self-contained — **offered** | **49** |
| reference a subdirectory — not offered | 260 |
| reference no image at all | 1 |

The 49 include the entire `gamepads/lite/` set, which is the flat overlay pack
most people are actually looking for.

Listing an overlay that would fail to install is worse than not listing it, so
the catalogue is filtered rather than leaving the install to discover the
problem.

## How it lists 310 overlays without downloading 29 MB

**Listing** is one call to GitHub's Git Trees API with `?recursive=1` — 732 KB
of JSON for 2,359 entries, against a 29 MB clone.

That single call also does the filtering. Reading 310 `.cfg` bodies to find
out which are self-contained would be 310 requests for a catalogue; instead
the tree itself is the predictor — an overlay is self-contained exactly when
its own directory also holds images. That heuristic was checked against the
content of all 310 files and agrees on **every one**, with no false positives
and no false negatives.

**Installing** fetches the chosen `.cfg`, re-reads its references to confirm
they really are bare names, and then the Hub downloads the `.cfg` and its
images — typically 2 to 30 small files.

The contents API would have been the obvious choice for listing and is the
wrong one: it truncates at 1,000 entries with no error and no flag. See
`libretro_overlays/github.py`.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `section` | `str` | `""` | narrow to one top-level directory |

Sections are `gamepads`, `borders`, `effects`, `keyboards`, `misc`, `ctr`,
`ipad` and `wii`.

No credentials. The service is unauthenticated and this plugin sends nothing
but a GET.

## What this does not promise

**No integrity digest.** The plugin pins `master`, not a commit, so new
overlays appear as they are contributed. What you get is HTTPS to a host this
plugin's manifest declares, with every redirect re-checked against that same
allowlist by the Hub. If you want a specific reviewed revision instead, that
is what `[[data_assets]]`'s mandatory sha256 is for, and it is deliberately a
different mechanism.

**The Hub does not render the overlay.** Nothing here checks that the images
are the ones the `.cfg` expects, or that the result looks right.
