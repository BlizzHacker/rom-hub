# RetroArch controller profiles plugin for ROM Hub

Implements the RPP v1 `assets` capability: RetroArch controller
autoconfiguration profiles, so a gamepad your emulator does not recognise gets
the button mapping somebody already worked out.

| Capability | Source | Does |
|---|---|---|
| `assets` (`controller`) | `github.com/libretro/retroarch-joypad-autoconfig` | lists profiles; the **Hub** downloads the one you pick |

## Install

    rom-hub plugin install ./plugins-dev/retroarch-autoconfig
    rom-hub assets list retroarch-autoconfig --kind controller
    rom-hub assets install retroarch-autoconfig "udev/8BitDo_ Wired_Xbox.cfg"

Files land in the directory configured for the `controller` kind — by default
`$ROM_HUB_HOME/var/assets/autoconfig/retroarch-autoconfig/`. Point
`ROM_HUB_ASSETS_DIR` at your RetroArch configuration directory and they land
in `autoconfig/` where RetroArch already looks; `ROM_HUB_CONTROLLERS_DIR`
overrides that one kind outright.

## Licensing, in plain language

**The profiles are MIT.** The repository's `COPYING` states the MIT License,
copyright 2019 The RetroArch team, and that covers the `.cfg` profiles this
plugin offers.

**GitHub reports this repository as NOASSERTION, and that is not a problem.**
It says so because `COPYING` contains *two* licences, which defeats GitHub's
single-licence detection. The second is the zlib-style SDL licence
(copyright 1997–2025 Sam Lantinga) covering the bundled SDL
`gamecontrollerdb.cfg` — permissive, redistributable, and not a file this
plugin offers anyway. Both halves were read from the repository itself rather
than taken from GitHub's summary.

So: MIT, stated on every item in the `LICENCE` column of `rom-hub assets list`.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `driver` | `str` | `"udev"` | which input driver's profiles to offer |
| `match` | `str` | `""` | keep only profiles whose filename contains this, case-insensitive |

`driver` is **not** detected from the machine running the Hub, deliberately —
the Hub need not be the machine the pad is plugged into. It is `input_driver`
in your `retroarch.cfg`. The repository has `android`, `dinput`, `hid`,
`linuxraw`, `mfi`, `parport`, `qnx`, `sdl2`, `sdl3`, `udev`, `winraw`, `x` and
`xinput`; an unrecognised value is refused by name rather than defaulted.

No credentials. The service is unauthenticated and this plugin sends nothing
but a GET.

## How it lists 437 profiles without downloading the repository

The repository is 2.6 MB, which is small enough that cloning it would be
merely wasteful rather than absurd — but the mechanism is the same one the
overlays and cheats plugins need, where it is neither.

**Listing** is one call to GitHub's Git Trees API for a single directory:
`/git/trees/master:udev`. That returns every entry with its size, and a
`truncated` flag when it could not return them all.

**Installing** is one `raw.githubusercontent.com` GET for the single `.cfg`
chosen — about 1.7 KB.

The contents API (`/contents/udev`) would have been the obvious choice and is
the wrong one: **it truncates at 1,000 entries with no error and no flag**.
`udev` is under that today, but `libretro-database`'s NES cheat directory is
2,265 files, and a plugin family that used the truncating endpoint would have
quietly offered a third of it. See `retroarch_autoconfig/github.py`.

## What this does not promise

**No integrity digest.** The plugin pins `master`, not a commit, because a pad
released next month is the entire point of this source — pinning a sha would
freeze the catalogue at packaging time. What you get is HTTPS to a host this
plugin's manifest declares, with every redirect re-checked against that same
allowlist by the Hub. If you want a specific reviewed revision instead, that
is what `[[data_assets]]`'s mandatory sha256 is for, and it is deliberately a
different mechanism.

**The Hub does not read the file.** A controller profile is text that
RetroArch parses; nothing here validates that it maps the pad you own, or that
it maps anything at all.
