# libretro content plugin for ROM Hub

Implements the RPP v1 `search` and `importer` capabilities against
`https://buildbot.libretro.com/assets/cores/` — the directory RetroArch's own
**Content Downloader** reads.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `/assets/cores/<system>/` | matches filenames in one or more system directories |
| `importer` | `/assets/cores/<system>/` | re-reads the listing, then plans the exact file |

**Twenty-nine RomM platforms**, from NES and Mega Drive to Vectrex,
Intellivision, Neo Geo Pocket, Pokémon Mini, TIC-80 and WASM-4. That breadth is
the reason this plugin exists: the other free-content source in this directory,
`homebrew`, is Game Boy and NES only.

## Why this material is legitimate

**libretro ships it in RetroArch.** These directories are not a scrape target
that happens to be readable — they are the back end of the *Load Content →
Download Content* menu in the emulator itself, published unauthenticated so
that software reads them. This plugin is a second reader of a feed built for
readers.

What is in them, and why each part may be redistributed:

- **Homebrew and demos** written by their authors and given to libretro for
  distribution — `Alter Ego`, `Chrono Knight`, `Bobl`, `Sheep It Up`,
  `Break An Egg`. The rights holder is the author, and the author put it here.
- **Test suites and technical software** — `240p Test Suite` (MIT-licensed),
  emulator conformance ROMs. Openly licensed outright.
- **Open-source game data** for engine cores — Cave Story's freeware release,
  the Quake shareware episode, `Jump 'n Bump`, `Dinothawr`.
- **The GCE Vectrex library**, which is the one entry needing a sentence of
  its own. It is *not* public domain: Smith Engineering (Jay Smith, who
  designed the Vectrex) granted permission in 1992 for Vectrex ROMs, manuals
  and overlays to be copied and distributed **as long as it is not for
  profit**. That is a real, specific grant from the actual rights holder, and
  it is also a *condition*: this content is free to acquire and to keep, and
  it is not free to sell. If you are building something commercial, that
  directory is the one to leave out.

`buildbot.libretro.com/robots.txt` carries only Cloudflare content-signal
declarations about AI training and search indexing and `Disallow`s **nothing** —
no path, no user agent. Verified 2026-07-29.

## Search

There is no query API. The buildbot is a static tree, so a search is "fetch
some listings and match names in them", and the only real question is how many
listings.

    rom-hub search libretro-content "alter ego" --platform nes    # one request

`--platform` maps one RomM slug to one directory, so a platform-scoped search
is a **single** round trip. A platform this source has nothing for — Jaguar,
3DO, Amiga — returns an empty list **without a request**. That is not an error.

Without `--platform` the plugin walks `systems` (RomM slugs; defaults to
`nes snes genesis gb gba sms atari2600 vectrex`) and stops at `max_systems`.
The bound is not politeness: the host kills a plugin at 30 seconds, each
listing is its own round trip, and walking all 29 mapped directories does not
reliably finish inside that. An unbounded default would make "no results" and
"timed out" look identical from the outside.

Matching is case-insensitive, every whitespace-separated term must appear in
the filename, and order does not matter. An empty query browses.

## Importing

    rom-hub import libretro-content "GCE - Vectrex/Berzerk (World).zip"

The `source_id` is `<system directory>/<filename>`, exactly as search returns
it. Before planning anything the importer **re-reads the directory listing and
requires an exact filename match**. The buildbot rebuilds this tree; a name
from an older search can disappear, and importing the nearest remaining name
would file a game nobody asked for.

Everything lands in the `libretro content` RomM collection by default, so you
can see at a glance what came from here.

## Platform mapping

`libretro_content/platforms.py` is an exact-match table with no fallback and
no prefix matching. It is keyed on the directory names this server actually
serves, read from the live listing — **not** copied from the libretro
*thumbnail* server, which spells two of the same machines differently
(`Coleco - Colecovision` here vs `Coleco - ColecoVision` there;
`Nintendo - GameBoy` here vs `Nintendo - Game Boy` there). Copying would have
produced two silent 404s.

An unmapped directory refuses with one of **three** different sentences,
because "add a row" is the right advice for exactly one of them:

- **needs mapping** — a real RomM platform nobody has added yet.
- **not a platform** — `Images`, `Video`, `Utilities`. Screensavers, test
  videos and tools. There is no shelf for them.
- **ambiguous** — `Nintendo - GameCube - Wii` is one directory holding two
  consoles, and RomM keeps `ngc` and `wii` separate. Pass `--platform` to say
  which; the plugin will not pick.

Also deliberately absent: the single-game engine directories (`DOOM`, `Quake`,
`Cave Story`, `Tomb Raider`, `Rick Dangerous`, …), which are game data for one
core each rather than systems, and the fantasy consoles RomM does not carry
(`Uzebox`, `Vircon32`, `MicroW8`, `LowResNX`, `CHIP-8`, …). `TIC-80` and
`WASM-4` *are* mapped, precisely because RomM does carry those two.

## Install

    rom-hub plugin install https://github.com/BlizzHacker/rom-hub-libretro-content --ref v0.1.0

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `systems` | `list[str]` | `[]` | RomM slugs to walk when no `--platform` is given; empty means the built-in eight |
| `max_systems` | `int` | `8` | Hard bound on listings per search (capped at 24) |
| `collection` | `str` | `libretro content` | RomM collection imports are filed under |

## Notes for the next person

- **There is no `.index-extended` here.** The sibling `libretro-cores` plugin
  reads one for cores; the content tree has none (checked live: HTTP 404). The
  h5ai listing *is* the catalogue.
- **h5ai renders with JavaScript** and ships a plain `<table>` fallback in the
  HTML. This plugin reads the fallback. That fallback is the only reason this
  source is scriptable without a browser.
- **The size column is not a size.** h5ai prints `65 KB`, rounded, and there
  is no byte count anywhere in the document. It is carried as `extra.size_text`
  and never as `size_bytes` — a rounded number in a field the host verifies
  against would turn every import into a mismatch.
- **Do not infer type from the extension.** `Quake II` is a directory and
  `Break An Egg.md` is a Mega Drive ROM. The listing's `alt="folder"` /
  `alt="file"` is the discriminator.
- A listing that does not parse as a listing **raises** rather than returning
  no rows. That is the `nointro-archive` lesson: Myrient answered HTTP 200 with
  a shutdown notice for every path, and a parser that cannot tell "empty" from
  "not a listing" cannot tell a dead source from a quiet one.
