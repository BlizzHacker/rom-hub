# libretro overlays plugin for ROM Hub

Implements the RPP v1 `assets` capability: RetroArch overlays — the bezels and
on-screen gamepads that fill the empty space around a 4:3 game on a 16:9
screen.

| Capability | Source | Does |
|---|---|---|
| `assets` (`overlay`) | `github.com/libretro/common-overlays` | lists every overlay in the repository; the **Hub** downloads the one you pick, with the images its `.cfg` references |

## Install

    rom-hub plugin install ./plugins-dev/libretro-overlays
    rom-hub assets list libretro-overlays --kind overlay
    rom-hub assets install libretro-overlays borders/gb.cfg

Files land in the directory configured for the `overlay` kind — by default
`$ROM_HUB_HOME/var/assets/overlays/libretro-overlays/`. Point
`ROM_HUB_ASSETS_DIR` at your RetroArch configuration directory and they land
in `overlays/` where RetroArch already looks; `ROM_HUB_OVERLAYS_DIR` overrides
that one kind outright.

## All 310 overlays, up from 49

The first release of this plugin offered **49 of the repository's 310**
overlays, and the README said so plainly. This one offers all 310.

The reason for the gap was real and the reason it closed is not a relaxation.
A RetroArch overlay is a `.cfg` plus the images it references, and the `.cfg`
names them relative to itself. The dominant form is a subdirectory:

    overlay0_desc0_overlay = img/dpad-left.png

Every destination a `FetchPlan` could express used to be a bare name — the
rule that stops a plugin writing outside the directory chosen for it. So the
plugin offered only the overlays whose references happened to be bare names,
because listing an overlay that would fail to install is worse than not
listing it.

But the guarantee behind that rule is *"a plugin must never steer a host write
outside its own install directory"*, and a bare name is one way to get it
rather than the only one. `FetchFile` now carries an optional `subdir`: a
relative path whose every component goes through the same validator a filename
does, with `rom_hub.paths.dest_under_dir` resolving the join and asserting the
result is inside the target. `filename` was not weakened — it still means one
bare name, and a plugin that puts a path in it is refused exactly as before.

| | before | now |
|---|---|---|
| `.cfg` files in the repository | 310 | 310 |
| offered | **49** | **310** |
| references a subdirectory | 260, not offered | offered |
| references no image at all | 1, not offered | offered (it is a real config file) |

Measured against the live repository on 2026-08-01.

## Where the files go, and why they are not renamed

An overlay installs at its **own path in the repository tree**:

    borders/gb.cfg      -> <overlays>/libretro-overlays/borders/gb.cfg
    borders/img/gb.png  -> <overlays>/libretro-overlays/borders/img/gb.png

Two things follow from the format, not from taste.

**The layout is preserved** because the `.cfg` names its images relatively, so
an image has to sit where the `.cfg` says it does. Preserving the tree also
means two overlays cannot collide: `borders/` and `gamepads/` both hold a
`snes.cfg`, and both hold an `img/`.

**Nothing is renamed.** The previous release sanitised upstream filenames into
something the host would accept. That is exactly wrong for a bundle: a renamed
`dpad-left.png` is a sprite the `.cfg` no longer finds, and the failure looks
like a broken download rather than a rename. So this plugin installs every
path verbatim **or refuses the overlay**, with a message naming the offending
path. Checked against all 310 on 2026-08-01: every path in this repository is
expressible verbatim, so that refusal is a guard against a future contribution
rather than a filter on today's.

The deepest install this repository produces is four directories:
`effects/scanlines/nesguy_scanlines/img/3x-scanlines1-1280x720.png`. The
largest bundle is 180 files; the median is 16.

## Licensing, in plain language

**CC-BY-4.0 — Creative Commons Attribution 4.0 International.** The repository
carries the full licence text in `COPYING`, and GitHub's own detection agrees
(SPDX `CC-BY-4.0`). This was read from the repository rather than taken from a
badge.

What that means for you in practice: you may use, modify and redistribute
these overlays, including commercially, provided you give attribution to the
creators and indicate any changes. The Hub does not add attribution to the
files for you — if you redistribute an overlay, that obligation is yours.

## How it lists 310 overlays without downloading 29 MB

**Listing** is one call to GitHub's Git Trees API with `?recursive=1` — 583 KB
of JSON for 2,359 entries, against a 29 MB clone. No `.cfg` body is read to
build the catalogue.

**Installing** fetches the chosen `.cfg`, resolves each reference against the
`.cfg`'s own directory, and then the Hub downloads the `.cfg` and its images.
Each reference is checked three ways before it becomes a download:

* it must stay inside the repository — a `../../..` in somebody's `.cfg` is
  not something this plugin will follow, and none of the 310 does today;
* it must actually exist in the tree — six of the 310 name at least one image
  the repository does not have, and a plan containing one would 404 halfway
  through an install, so the missing sprite is skipped rather than planned;
* its path must be expressible verbatim, or the overlay is refused.

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
