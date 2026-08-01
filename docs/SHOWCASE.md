# Showcase

Screenshots of a demo stack where **every item was placed by a ROM Hub plugin**
installed by slug from its public repo. Nothing was hand-copied, no database was
written directly, and no row was staged for the photograph.

Captured 2026-08-01 against RomM 4.9.2, Gaseous 2.0.0-rc.3 and Retrom 0.8.4,
running side by side. The stack was torn down afterwards; the CLI transcripts in
this directory are the same run.

---

## RomM — 45 games across 18 platforms

![RomM populated by ROM Hub plugins](screenshots/romm.png)

The **Collections** row is the point: each one is named after the plugin that
filled it — Aminet, Archive.org, Demozoo, Homebrew, IF Archive, libretro
content, No-Intro, ScummVM freeware. That is the plugin system made visible.

Read it honestly:

* **Covers are partial.** 24 of 45 have real box art, from `libretro-thumbnails`
  and `openvgdb`. The rest show RomM's `?` placeholder, because those titles are
  homebrew, demoscene and interactive fiction that no art database carries. A
  screenshot with 45 covers would mean a smaller, easier library.
* 6 BIOS files came from `open-bios` (firmware), and are not counted in the 45.
* Platforms span Amiga, Atari 2600/7800, C64, DOS, Game Boy/Color, Glulx,
  Nintendo 3DS/DS/NES, ScummVM and more — every one resolved through a plugin's
  platform-mapping table, never guessed.

## Gaseous — 12 games

![Gaseous populated by ROM Hub plugins](screenshots/gaseous.png)

Same plugins, different backend, and the differences are real rather than
cosmetic:

* **No cover art**, because Gaseous exposes no metadata-write API. `capabilities()`
  declares that, so the host does not attempt it. The placeholder art is
  Gaseous's own.
* **No collections** — `CollectionsController` is empty upstream. The import
  reports the grouping as skipped rather than failing the ROM.
* 11 of 12 land on platform 0: Gaseous derives platform from its own file
  signature and ignores the requested id. Documented upstream quirk, not a
  ROM Hub bug.

## Retrom — 32 entries

![Retrom populated by ROM Hub plugins](screenshots/retrom.png)

**14 of these came from plugins.** The other 18 are single-byte platform seeds
(`demo-seed.*`) written to bootstrap the library, because Retrom will not scan an
empty content directory. They are named so you can tell them apart, and they are
counted separately here for the same reason.

Retrom has no upload API at all — files arrive over its WebDAV service and
`UpdateLibrary` indexes them. 5 entries carry box art.

---

## The command line

The transcripts beside this file are the same session:

| file | what it shows |
|---|---|
| `01-plugin-browse.txt` | all 22 plugins in the directory |
| `03/04/05-backend-info-*.txt` | what each backend can and cannot do |
| `06-search-fanout-sonic.txt` | one query fanned across every search plugin |
| `08/15/16-import-*.txt` | real imports into all three backends |
| `09/10/17-enrich-*.txt` | metadata and cover art being written |
| `11/12/13-*.txt` | cores, firmware and assets |
| `14-jobs.txt` | the job queue — **including failures** |

`14-jobs.txt` is worth opening. Two ROMs uploaded successfully but failed to
register, and the tool says exactly that: *"uploaded to romm successfully, but
registering it in the library failed … do not re-upload it."* A showcase that
only contained successes would be a worse advertisement than one that shows the
failure path working.

## What is not shown

* `retroachievements` — needs a web API key the demo stack had none for. It
  refused cleanly rather than half-working.
* `ludusavi` — matches PC-game titles exactly; nothing in this library matched,
  which is the conservative behaviour it is built for.
* `itch-io` — answered the fan-out search but cannot import (robots.txt forbids
  its download path).
