# Standalone emulators plugin for ROM Hub

Implements the RPP v1 `cores` capability for **standalone emulators** —
DuckStation, mGBA, PCSX2 and melonDS — downloaded from each project's own
GitHub releases into the Hub's configured cores directory.

| Capability | Endpoint | Does |
|---|---|---|
| `cores` | `api.github.com/repos/<owner>/<repo>/releases/latest` | lists the latest build for the configured target |
| `cores` | `github.com/<owner>/<repo>/releases/download/<tag>/<asset>` | names a URL; the **Hub** fetches it |

## Install

    rom-hub plugin install ./plugins-dev/emulators
    rom-hub cores list emulators
    rom-hub cores install emulators mgba

Binaries land in `$ROM_HUB_HOME/var/cores/emulators/`, or wherever
`ROM_HUB_CORES_DIR` points. That is the Hub's decision, not this plugin's:
a plugin returns a filename and never a path. They land in their own
`emulators/` subdirectory, so they never collide with libretro's cores.

**Nothing is unpacked and nothing is run.** What arrives is the release
archive exactly as the project published it — a `.AppImage`, a `.zip`, a
`.7z`, a `.tar.xz`. Unpacking it and running it is yours to do.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `target` | `str` | `"linux/x86_64"` | which machine the emulator has to run on |
| `only` | `list[str]` | `[]` | narrow the catalogue to these project ids; empty means all |

No credentials. The GitHub REST API is used unauthenticated, which is
limited to 60 requests per hour per address; a `cores list` costs one
request per project (four by default, or fewer with `only`), and a
`cores install` costs one.

### Targets

**The target is not detected from the host OS, on purpose.** The Hub is
frequently not running on the machine that will run these emulators — a
Linux container serving a household's library while the emulator runs on a
Windows desktop is the ordinary case. So the target is config, and a
target that is not in the table is refused **by name**.

| `target` | duckstation | mgba | pcsx2 | melonds |
|---|:-:|:-:|:-:|:-:|
| `linux/x86_64` | ✓ | ✓ | ✓ | ✓ |
| `linux/aarch64` | ✓ | ✓ | | ✓ |
| `windows/x86_64` | ✓ | ✓ | ✓ | ✓ |
| `windows/x86` | | ✓ | | |
| `windows/arm64` | ✓ | | | ✓ |
| `macos/universal` | ✓ | | ✓ | ✓ |

A project that publishes nothing for the configured target is simply
absent from the listing — that is not an error, it is an accurate answer.

## Licensing

**These are other people's programs and their licences travel with them,
not with this plugin.** The plugin is MIT; that says nothing about what
you may do with a build it points you at. Each project's licence was read
from its own repository on 2026-07-29 via
`GET /repos/{owner}/{repo}/license`, and it is printed in the `NAME`
column of `rom-hub cores list` so it reaches you before you install rather
than after.

| Project | Licence | What that means here |
|---|---|---|
| mGBA | `MPL-2.0` | Open source. Free to use and redistribute. |
| PCSX2 | `GPL-3.0` | Open source. Free to use and redistribute. |
| melonDS | `GPL-3.0` | Open source. Free to use and redistribute. |
| DuckStation | `CC-BY-NC-ND-4.0` | **Not open source.** Non-commercial use only, and no derivatives. |

DuckStation is the one worth reading twice. It relicensed away from GPL to
Creative Commons Attribution-NonCommercial-NoDerivatives 4.0, which
permits redistributing the **unmodified** build for **non-commercial**
purposes and forbids distributing anything derived from it. GitHub's API
reports its SPDX id as `NOASSERTION`, so a plugin that trusted the SPDX
field alone would have shown a blank; the value in the table is read from
the `LICENSE` file itself.

Nothing here redistributes anything. The plugin names the project's own
release URL and the Hub fetches it once, for the operator who asked —
the same act as clicking the asset on the release page.

**None of these emulators ships or requires copyrighted firmware from this
plugin.** PCSX2 and DuckStation want a console BIOS to run games; where
that comes from is not this plugin's business, and the `open-bios`
firmware plugin is the Hub's answer for the parts that are freely
licensed.

## Why `cores`, and not a new capability

RPP's `cores` contract is *"list installable emulator binaries; return a
`FetchPlan`; the host installs into a configured local directory"*. That
is this plugin exactly, with nothing left over:

- `CoreArtifact` carries every field a standalone emulator needs — id,
  name, version, system, description — and its `system` is documented as a
  label for the operator rather than a library platform slug, which is
  what `Sony PlayStation` is here.
- The host side is already right: `rom_hub.cores.install_core` writes into
  `<cores_dir>/<plugin slug>/`, so these land beside libretro's cores and
  never on top of them.
- A new `emulators` capability would need a line in
  `manifest.KNOWN_CAPABILITIES`, a protocol method, a dispatcher branch, a
  CLI subcommand and a host installer — all of it duplicating `cores`, to
  express a distinction (`.so` versus `.AppImage`) nothing downstream acts
  on.

**Not `assets`.** A sibling plugin is adding an `assets` capability for
shaders, overlays and cheats, and this could plausibly have gone there —
both are "a file the host puts somewhere local". It should not. Those are
*data an emulator reads*; this is *the emulator*. The consequence of the
wrong file differs in kind: a bad shader renders oddly, a bad binary is
code you then run. Executables belong under the capability whose entire
documentation is about executables, and whose CLI output an operator
already reads as "things I will run".

## What is filtered out, and why

Listing every asset of every release would be useless noise — the four
releases carry 47 files between them and 43 of them are the wrong answer
for any given machine. Selection is an **explicit anchored pattern per
(project, target)**, and it must match **exactly one** asset or it
refuses: zero means upstream renamed something, two means the pattern is
too loose, and both are cases where picking anyway installs a plausible
wrong file.

Never selected, by construction:

- **Debug symbols** — `duckstation-windows-x64-release-symbols.7z`,
  `pcsx2-…-Qt-symbols.7z`. Not the emulator.
- **Installers** — `PCSX2-…-installer.exe`, `mGBA-…-win64-installer.exe`.
  The Hub drops a file in a directory; it does not run installers, and it
  should not.
- **Flatpak** — `pcsx2-…-x64-Qt.flatpak` needs a Flatpak runtime the Hub
  cannot provide.
- **Distribution-pinned Linux builds** — mGBA's five
  `ubuntu64-<codename>` tarballs and melonDS's `ubuntu-*` builds link
  against one distribution's libraries; the AppImage does not.
- **Console homebrew ports** — mGBA's 3DS, Switch, Vita and Wii builds are
  emulators *for a game console*, not for any machine in the target list.
- **BSD builds** — melonDS's FreeBSD, NetBSD and OpenBSD archives have no
  name in the target list. Easy to add; not guessed at.
- **CPU-variant fallbacks** — DuckStation's `-sse2-` builds are for CPUs
  without AVX2. Two rows for one machine with nothing to choose between
  them is worse than one.

And one whole platform is left out on purpose: **mGBA on macOS**. Release
0.10.5 ships both `mGBA-0.10.5-macos.dmg` and `mGBA-0.10.5-osx.dmg` and
says nowhere which macOS version or which architecture either targets.
Offering one would be a guess, so `macos/universal` is simply not a target
mGBA has here, and asking for it says why.

## Dolphin

**Not included, and it is not an oversight.**
`https://api.github.com/repos/dolphin-emu/dolphin/releases/latest` answers
`404` — Dolphin publishes no GitHub releases at all, because its builds
ship from `dolphin-emu.org/download/`.

That page cannot be used from here either. `dolphin-emu.org` sits behind a
bunny.net JavaScript proof-of-work challenge that answers `403` to
everything, including its own `/robots.txt`. A plugin with no sockets and
no browser cannot pass a proof-of-work challenge, and working around an
anti-bot wall is not something this plugin will do. Both facts checked
2026-07-29.

`emulators/projects.py` records this in a `DECLINED` table, so asking for
`dolphin` by name gets that explanation rather than "no such core".

## Terms

GitHub's REST API is public, documented and meant to be read by software;
`api.github.com` serves no `robots.txt`. Downloads go to
`github.com/<owner>/<repo>/releases/download/…`, which `github.com`'s
`robots.txt` disallows to *crawlers* under `Disallow: /*/download`.
Nothing here crawls: the Hub fetches exactly the one asset an operator
named, once, on an explicit `cores install`, following no links and
indexing nothing. The catalogue itself is read only from the API.

Asset URLs answer `302 Found` with a `Location` on
`release-assets.githubusercontent.com` — exactly one hop, verified live —
and every hop is re-checked against this plugin's `network` allowlist, so
that host is declared. `objects.githubusercontent.com`, which GitHub used
to redirect to, is deliberately **not** declared: it is not in today's
chain, and a permission granted "just in case" is a permission granted for
no reason.
