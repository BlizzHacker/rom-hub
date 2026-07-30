# Universal-DB plugin for ROM Hub — 3DS and DS homebrew

Implements the RPP v1 `search` and `importer` capabilities against
[Universal-DB](https://db.universal-team.net), Universal-Team's open database of
Nintendo 3DS and Nintendo DS **homebrew** — software written by hobbyists for
those consoles and published by the people who wrote it.

| Capability | Reads | Does |
|---|---|---|
| `search` | `db.universal-team.net/data/full.json` | filters the whole catalogue by title, author, console and category |
| `importer` | the same document | plans the one release file that is a title on the console you asked for |

## Install

    rom-hub plugin install ./plugins-dev/universal-db
    rom-hub search "wordle" --limit 5
    rom-hub import universal-db wordle-ds --platform nds

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `category` | `str` | `""` | restrict to one kind of entry: `game`, `app`, `utility`, `emulator`, `media`, `save-tool`. Empty means no filter |
| `require_license` | `bool` | `false` | hide, and refuse to import, entries whose licence the database does not state |
| `collection` | `str` | `Homebrew` | RomM collection imported ROMs are grouped into |

## Why this source is legitimately redistributable

**Homebrew is new software written by hobbyists for old hardware.** It is not a
dump of a commercial cartridge. The copyright in each entry belongs to the
person who wrote it, and Universal-DB exists because those authors wanted their
work listed and findable.

Concretely:

- **Universal-DB hosts nothing.** Every entry points at wherever its author
  already publishes — a GitHub release in 319 of the 331 importable cases, plus
  a handful on GitLab, Codeberg, the author's own CDN and, for two entries, the
  database's own asset folder. This plugin fetches the author's file from the
  author's host. Nothing is mirrored, re-hosted or republished.
- **The database is an open, reviewable repository.** It is
  [`Universal-Team/db`](https://github.com/Universal-Team/db), GPL-3.0, and
  entries arrive as pull requests and app requests against it. Anything in it is
  there on the record, with its history.
- **The permission is per entry and held by the author**, not asserted here.
  270 of the 400 live entries record a licence — GPL-3.0 (117), MIT (78),
  GPL-2.0 (23), Apache-2.0, MPL-2.0, BSD, Unlicense, CC0, zlib and `other` — and
  this plugin shows it on every row.

That is the honest boundary, and it is the same one the `homebrew` (gbdev) and
`demozoo` plugins draw: **the author published the file; the database says where
it is; this plugin fetches it from there.**

### Entries that state no licence

**130 of the 400 entries have no `license` key at all.** This plugin reports
those as **`unstated`** and does not invent, infer or hide anything.

- `unstated` is a **fact about the database**, not a licence, and specifically
  not a synonym for public domain. Most of these entries do carry terms in their
  own repository; the database simply has not recorded one, usually because the
  project has no `LICENSE` file for GitHub's API to report.
- They are **shown and importable by default**, because not knowing the terms is
  a reason to put the gap in front of you, not a reason to pretend the entry is
  not there.
- Set **`require_license = true`** to work only with the ones that state terms.
  It hides them from search *and* refuses them at import, so the setting cannot
  be half-applied.

If you need a specific licence before you use something, the entry's page —
which every result links to — is where its source, author and repository are.

### Not sourced from hShop or Myrient

Neither appears anywhere in Universal-DB, and there is nothing in this plugin
that could reach them: the manifest allowlist is the eight hosts the data
actually resolves to, and a download anywhere else is refused **by name** before
a socket is opened. hShop's own catalogue is commercial Nintendo content —
official 3DS themes and unreleased prototypes — with no legitimately
redistributable subset to scope to, which is why this database is the 3DS source
here instead of that one.

## What is excluded, and why

**Three of the database's nine categories are never shown and never imported**
(14 of 400 entries):

| Category | Entries | Why it is not a title |
|---|---|---|
| `plugin` | 8 | Luma3DS `.3gx` plugins. They are injected into a *commercial* game that is already running; they do not boot |
| `firm` | 6 | Boot-chain firmware (`boot.firm`). Flashing one is a custom-firmware install step, not adding a title to a library |
| `exploit` | 1 | An entry point into another game's save data |

The counts sum to 15 and remove 14 entries, because `nexus3ds` is tagged both
`utility` and `firm` and ships only a `boot.firm`. Everything else the database
publishes — **games, apps, utilities, emulators, multimedia and save tools** —
boots on the console as a title in exactly the way a `game` does, and stays.
Narrowing further to `game` alone was considered and rejected: an emulator is as
much a `.cia` as a puzzle game is, and `category = "game"` is there for anyone
who wants only games.

**Within an entry, only files that are titles are ever planned.** The database
lists release notes (`.md`, `.txt`), banner images (`.png`), IPS patches, a
`.torrent` and a `.pak` alongside the real payloads. None of them is a thing to
put in a library.

**`nightly` and `prerelease` builds are ignored.** They move under the same URL,
so a library row importing one stops describing the bytes on disk. Ignoring them
also keeps the manifest allowlist honest — three download hosts appear *only*
under those keys.

## Choosing the file

An entry's `downloads` is a dict of release assets, and one entry routinely
holds things that are not interchangeable: `PKCount.3dsx`, `PKCount.cia` and
`PKCount.nds` are three consoles' worth of one app, and
`thextech-3ds-assets-smbx13-v1.3.7.3.zip` is 48 MB of level data sitting next to
a 4 MB program. There are exactly two kinds of evidence available, and this
plugin uses both and nothing else.

**The extension, for a bare file.** A `.cia` is a 3DS title and a `.nds` is a DS
one — not a heuristic, but what the formats are. `.cia` is preferred over
`.3dsx` because a CIA installs into the HOME menu as a title where a 3DSX runs
only from the Homebrew Launcher; `.nds` over `.dsi` because the DSi build is the
narrower one. Where two files share a format they are alternate builds and the
larger wins, which is the same rule — and the same reason — as the Archive.org
plugin's.

**The database's own `archive` map, for an archive.** Universal-DB publishes,
per entry, a table of what is inside a release archive, which Universal-Updater
uses to install one. An archive is a candidate **only** when that table matches
it and names a title for the console being imported. `CrossCraft-3DS.zip`
qualifies; `CrossCraft-Linux.zip` never can. The map's keys are *regular
expressions* — `Apotris-(.*)?3(ds|DS)(-.*)?\.zip` — so they are compiled, not
compared.

Archives are imported rather than refused because 76 entries ship only that way,
including Apotris, Super Haxagon and CrossCraft — a 3DSX plus its romfs assets
is not one file — and the Hub already handles archive payloads.

**Where the evidence runs out, the import refuses and names what it saw.** 61 of
the 394 entry-platform pairs land here:

- `sudokul` ships 3DS, GameCube, PSP and two Windows builds as bare zips with no
  map. Choosing by size takes the x64 Windows build.
- `thextech` matches its program archive and both asset packs against one
  pattern, so the database does not say which is which.

Those entries still **appear in search**, because hiding something a person can
see on the database's own site is worse than showing why it will not import.

## Platform mapping

`universal_db/platforms.py` maps the database's `systems` tokens to RomM
platform slugs:

| Universal-DB | RomM | Entries |
|---|---|---|
| `3DS` | `3ds` | 325 |
| `DS` | `nds` | 67 |
| both | both | 8 |

That is the entire vocabulary. Exact match, no fallback: an unknown value raises
**"needs mapping"** and names itself.

**DS and 3DS never collapse into one another.** They are two consoles, two RomM
platforms and two incompatible executable formats. The eight entries published
for both ship a *different file* for each, so:

- **search returns one row per console** — `pkcount` appears twice, once as
  `3ds` and once as `nds`, because they are two importable things;
- **importing one without `--platform` is refused**, not guessed. A library that
  quietly merged the two cannot be un-merged later, because nothing afterwards
  records which rows were guessed.

## Searching

Universal-DB publishes a file, not a search API, so the query is applied here:
one request fetches the whole 1.66 MB catalogue and 400 entries are filtered
locally. That is the opposite trade to the `homebrew` plugin, which pushes `q`
to a server, and it is the right one at this size — there is no smaller form to
ask for (`data/3ds.json`, `data/ds.json` and `data/index.json` all 404), and one
request answers substring queries the site's own search cannot.

Every word of the query must appear in the entry's **title or author**.
Description is deliberately not searched: it is a sentence of prose per entry,
and including it would make a two-letter query return most of the database — the
mistake the Archive.org plugin made when a bare term reached its default field
and answered `Die Hard` for `sonic`.

## Network

Declared allowlist, and it is computed from the data rather than guessed — every
host below is reached by a payload this plugin will actually plan, measured
across all 400 entries on 2026-07-29:

| Host | What for |
|---|---|
| `db.universal-team.net` | `full.json`, and two entries' own assets |
| `github.com` | 319 of 331 importable payloads |
| `*.githubusercontent.com` | where a GitHub release **redirects** to |
| `gitlab.com`, `codeberg.org` | four entries published there |
| `gbatemp.net` | three entries hosted as forum attachments |
| `cdn.classicube.net` | ClassiCube's own CDN |
| `apotrisstorage.blob.core.windows.net` | Apotris's own storage |

`*.githubusercontent.com` is not laziness. A GitHub release URL is a **302** to
`release-assets.githubusercontent.com` (verified live), the broker re-checks
**every hop**, and an undeclared redirect target is a download that fails after
the plan looked fine. The wildcard rather than that one name because GitHub has
renamed this host before — it used to be `objects.githubusercontent.com` — and
`raw.githubusercontent.com` is separately used by entries publishing straight
out of a repository.

Hosts that appear in the data but are **deliberately absent**:
`www.chishm.com`, `buildbot.libretro.com` and `downloads.scummvm.org`, because
no entry that resolves to a payload uses them; and `imaginye.ddns.net`, which
belongs to the one entry categorised `plugin`.

Two entries — `lolsnes` and `tasmanquest` — publish over plain **`http`**.
`rom_hub.netpolicy` permits `https` only, and this plugin does not work around
that; it refuses those two by name and says why.

Three GBAtemp-hosted entries answer **HTTP 403** to anything without a browser
session. They are declared because they are genuinely what the data points at,
and they fail as an ordinary failed job with GBAtemp's own status on it.

## Robots

`db.universal-team.net/robots.txt` is **HTTP 404** — the site publishes no crawl
directives at all, so there is nothing to observe and nothing to work around.
The plugin makes **one** request per command regardless, because the database is
one document.
