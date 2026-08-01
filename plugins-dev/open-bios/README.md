# open-bios plugin for ROM Hub

Implements the RPP v1 `firmware` capability: **clean-room, openly licensed
BIOS replacements**, downloaded into the Hub's configured firmware
directory and — where the library server can hold firmware — filed there
too.

Nothing in this plugin is a dump of a retail chip. Every item is a
reimplementation published under a licence that permits redistribution,
and **the licence is printed in the listing** so you can tell what you are
installing without leaving the terminal.

| Capability | Source | Does |
|---|---|---|
| `firmware` | `raw.githubusercontent.com/Cult-of-GBA/BIOS` | names a URL; the **Hub** fetches it |
| `firmware` | `github.com/LIJI32/SameBoy/releases/download/…` | names a zip; the **Hub** fetches it and keeps the declared members |

## Install

    rom-hub plugin install ./plugins-dev/open-bios
    rom-hub firmware list open-bios
    rom-hub firmware install open-bios cult-of-gba

Files land in `$ROM_HUB_HOME/var/firmware/open-bios/`, or wherever
`ROM_HUB_FIRMWARE_DIR` points — point it at the `system/` or `bios/`
directory your emulator already reads and there is nothing to copy
afterwards. That is the Hub's decision, not this plugin's: a plugin
returns a filename and never a path.

`firmware install` also stores the files in your library, on a backend
that can hold firmware. One that cannot is not an error: the download
happens, the library step is skipped, and the line you get back says so.
`--no-library` skips it deliberately.

## What it offers

| `firmware` | Platform | Licence | Files |
|---|---|---|---|
| `cult-of-gba` | `gba` | MIT | `gba_bios.bin` (16 KiB) |
| `sameboy-dmg` | `gb` | MIT (Expat) | `dmg_boot.bin`, `mgb_boot.bin` |
| `sameboy-cgb` | `gbc` | MIT (Expat) | `cgb_boot.bin`, `cgb0_boot.bin`, `agb_boot.bin` |

### `cult-of-gba` — Game Boy Advance

[Cult-of-GBA/BIOS](https://github.com/Cult-of-GBA/BIOS), by DenSinH and
fleroviux. A from-scratch GBA BIOS: the repository is the assembly source
for every BIOS call and for the boot screen, with the built 16 KiB image
committed beside it. Fetched at a **pinned commit**, not from `master`, so
the bytes cannot move under a plugin that claims to know what it serves.

Installed as `gba_bios.bin`, which is the name mGBA, VBA-M and the
libretro cores look for.

**Licence: MIT.** `LICENSE` in that repository is the Expat text,
"Copyright 2020 - 2021 DenSinH and fleroviux", and GitHub reports the
repository as MIT.

### `sameboy-dmg` and `sameboy-cgb` — Game Boy and Game Boy Color

[SameBoy](https://github.com/LIJI32/SameBoy), by Lior Halphon. Clean-room
replacements for the Game Boy boot ROMs, assembled from `BootROMs/*.asm`.

**Licence: MIT (Expat) — and that needed checking.** GitHub reports the
repository as `NOASSERTION`, which is not a licence problem so much as a
metadata one: the `LICENSE` file says *"All files and directories in this
repository, except for the iOS and HexFiend directories, are licensed
under the Expat License"*, and the iOS carve-out is what defeats GitHub's
detector. `BootROMs/` is neither of the excepted directories, so the boot
ROMs are Expat and redistribution is permitted. The release archive
carries the same `LICENSE` file alongside them.

**Why a zip.** SameBoy publishes no standalone boot-ROM asset — the built
binaries ship inside its emulator releases. The plugin therefore declares
`archive = "zip"` and the members it wants, and the **host** unpacks
exactly those and discards the rest, so what lands in your firmware
directory is a handful of 256- and 2304-byte files and not a Windows
emulator build. `sameboy_winsdl_*.zip` (1.6 MB) is the smallest asset that
carries them; the boot ROMs are identical bytes in every build.

The two SameBoy items name the same archive, so installing both downloads
it twice. Each item is independent on purpose — you should be able to take
the Game Boy Color boot ROMs without the Game Boy ones.

## What is deliberately not here

The value of this plugin is that everything in it is unambiguously yours
to have. Four real candidates were considered and left out; the reasons
are in `open_bios/catalogue.py` in full, and in short:

- **PCSX-ReDux OpenBIOS** (PlayStation, GPL-2.0) — the most-wanted one, and
  it is not installable. `grumpycoders/pcsx-redux` publishes **no releases
  and no tags at all**, so there is no `openbios.bin` to fetch; it builds
  from source under a Docker MIPS toolchain, and the only prebuilt copies
  ride inside the emulator's own AppImage on distrib.app. Shipping an entry
  for it would ship a 404. If the project ever publishes the binary this
  becomes a one-row change.
- **nds-bootstrap** (GPL-3.0) — real and maintained, but **not firmware**.
  Its release ships `.nds` homebrew-loader binaries that run on a DS to
  boot ROMs from an SD card. It does not replace the DS BIOS or firmware
  image, which is what melonDS and DeSmuME actually ask for.
- **C-BIOS** (MSX) — openly licensed and genuinely clean-room, but
  `cbios/cbios` publishes no built `.rom` files. Source only.
- **SameBoy's Super Game Boy boot ROMs** — in the archive already, under
  the same licence, and absent anyway: the Super Game Boy is a SNES
  peripheral with no unambiguous library platform, and this plugin never
  guesses a platform. See `open_bios/platforms.py`.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `sameboy_release` | `str` | `"v1.0.3"` | which SameBoy release the boot ROMs come out of |

Pinned rather than resolved at runtime. Firmware is bytes an emulator
*executes*, and you should get the release this README says was verified
rather than whatever was tagged this morning. The value is validated as a
tag before it becomes part of a URL.

No credentials. Both hosts are public and unauthenticated, and this plugin
sends nothing at all — it makes no request of its own. The host makes one
GET per install.

## Platforms

`open_bios/platforms.py` maps this plugin's system names to library
platform slugs by exact match, with **no fallback**. A system that is not
in the table raises "needs mapping" and names itself, and the catalogue
refuses to be built.

That is stricter than the equivalent table on the ROM side, and
deliberately. A ROM filed under the wrong system is visible — it is in the
library, under a heading that looks wrong. A BIOS filed under the wrong
system is *invisible*: the emulator that needed it goes on reporting that
it has no BIOS, and nothing anywhere says why.

| System | Platform slug |
|---|---|
| Game Boy | `gb` |
| Game Boy Color | `gbc` |
| Game Boy Advance | `gba` |

## Terms

Everything here is redistributable by its own project's licence, and each
item states which. That is the whole point: this is the legally clean way
to get a BIOS, and an item whose standing was unclear was left out rather
than shipped with a caveat.

The Hub cannot verify any of it — a dumped BIOS and a reimplemented one
look identical on the wire — so it is a rule about what this catalogue may
contain, enforced by review of `open_bios/catalogue.py`, where every claim
sits next to the evidence for it.

## Licence

MIT (this plugin's own code). The firmware it installs carries the licence
of the project that publishes it, listed above and printed by `rom-hub
firmware list`.

---

## Seen working

This plugin installs into a local directory rather than a library backend, so it does not appear in the screenshots. The command transcripts in the showcase show it listing and installing real files, with sizes and hashes.

Full showcase — all three backends (RomM, Gaseous, Retrom), every command transcript, and an honest account of what the pictures do *not* show: **[https://github.com/BlizzHacker/rom-hub/blob/master/docs/SHOWCASE.md](https://github.com/BlizzHacker/rom-hub/blob/master/docs/SHOWCASE.md)**

Part of [ROM Hub](https://github.com/BlizzHacker/rom-hub) — install with `rom-hub plugin install open-bios`.
