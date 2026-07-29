# libretro cores plugin for ROM Hub

Implements the RPP v1 `cores` capability: emulator cores from libretro's
public buildbot, downloaded into the Hub's configured cores directory.

| Capability | Endpoint | Does |
|---|---|---|
| `cores` | `buildbot.libretro.com/nightly/<target>/latest/.index-extended` | lists the cores that target ships |
| `cores` | `buildbot.libretro.com/nightly/<target>/latest/<core>_libretro.<ext>.zip` | names a URL; the **Hub** fetches it |

## Install

    rom-hub plugin install ./plugins-dev/libretro-cores
    rom-hub cores list libretro-cores
    rom-hub cores install libretro-cores gambatte

Cores land in `$ROM_HUB_HOME/var/cores/libretro-cores/`, or wherever
`ROM_HUB_CORES_DIR` points. That is the Hub's decision, not this plugin's:
a plugin returns a filename and never a path.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `target` | `str` | `"linux/x86_64"` | which build target's cores to offer |
| `only` | `list[str]` | `[]` | narrow the catalogue to these core ids; empty means all |

No credentials. The buildbot is public and unauthenticated, and this plugin
sends nothing but a GET.

### Targets

Verified live against the buildbot on 2026-07-29 — each one answers `200`
for `.index-extended` and its entries carry the suffix shown.

| `target` | Buildbot path | File |
|---|---|---|
| `linux/x86_64` | `nightly/linux/x86_64/latest/` | `.so.zip` |
| `linux/x86` | `nightly/linux/x86/latest/` | `.so.zip` |
| `linux/aarch64` | `nightly/linux/aarch64/latest/` | `.so.zip` |
| `linux/armhf` | `nightly/linux/armhf/latest/` | `.so.zip` |
| `linux/armv7-neon-hf` | `nightly/linux/armv7-neon-hf/latest/` | `.so.zip` |
| `windows/x86_64` | `nightly/windows/x86_64/latest/` | `.dll.zip` |
| `windows/x86` | `nightly/windows/x86/latest/` | `.dll.zip` |
| `macos/x86_64` | `nightly/apple/osx/x86_64/latest/` | `.dylib.zip` |
| `macos/arm64` | `nightly/apple/osx/arm64/latest/` | `.dylib.zip` |
| `ios/arm64` | `nightly/apple/ios-arm64/latest/` | `.dylib.zip` |
| `tvos/arm64` | `nightly/apple/tvos-arm64/latest/` | `.dylib.zip` |

**The target is not detected from the host OS, on purpose.** The Hub is
frequently not running on the machine that will load these cores — a Linux
container serving a Windows RetroArch over a share is the ordinary case. A
plugin that read `platform.system()` would hand a `.so` to somebody whose
frontend loads `.dll`, and the symptom would be "this core does nothing"
weeks later. So the target is config, and a target that is not in the table
is refused **by name** rather than defaulted.

`android` and `emscripten` are deliberately absent: the first publishes
RetroArch `.apk` builds rather than loose cores, and neither serves an
`.index-extended`. `apple/osx` is absent too — it is a directory of
sub-architectures, and the two current ones are listed above.

## Why `.index-extended` and not the directory page

Every build-target directory carries a file called `.index-extended`
alongside the cores:

    2026-07-29 edf888ae mednafen_supergrafx_libretro.so.zip
    <date>     <crc32>  <filename>

That is the entire format. It is used here in preference to parsing the
rendered index for two reasons: it is what RetroArch's own core updater
consumes, so it is maintained rather than incidental; and it is ~10 KB of
text where the rendered directory is a JavaScript file browser whose markup
is nobody's contract.

**A filename is validated, never repaired.** A line whose filename is not a
plain bare name, or does not carry this target's suffix, is skipped. The
host opens `FetchFile.filename` for writing, so a name that could be read as
a path must never reach it — and an entry that does not look like a core is
not a core, so it should show up as one absent core rather than as a mystery
download.

## What it does not do

- **It does not verify the crc32.** The index prints one and this plugin
  ignores it, because it cannot do otherwise: the plugin never sees the
  bytes — the *host* fetches them. Claiming a checksum check that does not
  happen would be worse than not mentioning it.
- **It does not unzip.** Cores arrive as `.zip`, which is how libretro ships
  them; unpacking is the frontend's job, and a plugin has no filesystem
  anyway.
- **It does not name a system it is not sure of.** See below.

## `system` is partial, and blank means blank

`CoreArtifact.system` is filled in from a hand-kept table in
`libretro_cores/systems.py`. A core that is not in it gets no system at all,
and prints as an empty column.

That is not laziness, it is the only honest option available. The buildbot
ships 218 cores for Linux x86_64 and its index says nothing about what any of
them run — it is filenames, a date and a crc32. The only machine-readable
mapping libretro publishes lives inside `assets/frontend/info.zip`, and a
plugin cannot open it: `ctx.http` returns text, deliberately, because a
plugin has no sockets and the broker is not a file transfer service.

So a blank means "this plugin does not know", which is true, where a name
derived from the core id would be false — `2048` is not a console and
`mame2003_plus` is not a system. An operator reading a system name while
choosing a core has to be able to trust it.

Nothing about `system` is load-bearing: it is never a RomM platform slug,
never a path component, and never consulted when deciding what to fetch.
Adding a row is a one-line change and cannot break an install. The spellings
are libretro's own, taken from the directory names under
`https://buildbot.libretro.com/assets/cores/`.

## Terms

libretro's buildbot is a public, unauthenticated distribution point for the
cores libretro itself builds, and `.index-extended` exists so that software
can read it — RetroArch's core updater is the reference consumer. This plugin
uses it the way it is meant to be used and circumvents nothing.
`buildbot.libretro.com/robots.txt` carries only content-signal declarations
about AI training and search indexing; it `Disallow`s nothing.

The cores are other people's software under their own licences — mostly
GPL/LGPL, some with more restrictive terms — and each core's licence travels
with the core, not with this plugin. This plugin is MIT; what it downloads
is not.
