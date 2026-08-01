# nointro-archive: No-Intro sets on Archive.org, for ROM Hub

Implements the RPP v1 `search`, `importer` and `census` capabilities against
Archive.org — a plain HTTP **directory index** for the first two, and the
search and metadata APIs for the third.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `<base_url><directory>/` | reads the index once, caches it, matches and **ranks** file names |
| `importer` | the same index | confirms the file is still listed, then plans it |
| `census` | `advancedsearch.php` + `metadata/<id>` | enumerates **all 71** `nointro*` items, classifies each, and records what it could not account for |

## How much of No-Intro this reaches

**All of it, and the number has a denominator behind it.**

    $ rom-hub catalogue build nointro-archive
    nointro-archive: complete -- 29,955 of 29,955 declared entries
      across 43 units; 28 units excluded (1,411 entries)
      29,771 catalogued rows -> 27,219 distinct dumps in 14,214 games
      skipped, by reason:
             184  archive.org bookkeeping (torrent, _meta.xml, thumbnails)

That is the claim this plugin is now willing to make, and every part of it
is checkable. `29,955` is not counted by the walk: it is the sum of
Archive.org's own `files_count` for each item, taken from
`advancedsearch.php`, while the enumeration reads the *metadata* endpoint.
Two services, two requests — verified to agree on all 71 items. Then, per
item, `kept + skipped == declared_total`, so every single declared entry is
either catalogued or skipped for a named reason.

| | 0.2.1 | 0.3.0 | 0.4.0 |
|---|---:|---:|---:|
| items reached | 12 | 25 | **71 (all of them)** |
| entries accounted for | 6,628 | 15,165 | **31,366** |
| entries catalogued | 6,628 | 15,165 | **29,771** |
| distinct dumps after dedup | — | — | **27,219** |
| coverage claim | "reachable" | "reachable" | **29,955 of 29,955, 28 units excluded by name** |

The older rows say *reachable*, and that word is the point. 15,165 was the
size of a list in `manifest.toml` — a fact about this plugin's
configuration, not about Archive.org. `search` still works exactly that
way and is unchanged; `census` is what answers *what is all of it?*

### What is excluded, and why

`--kinds` chooses what gets walked; `roms` is the default. Nothing is
dropped silently — each excluded unit is printed with its reason.

| kind | units | declared | what it is |
|---|---:|---:|---|
| `roms` | 43 | 29,955 | one entry per game — walked by default |
| `pack` | 11 | 215 | archives-of-archives; each file is a whole machine's set (`NoIntroROMsCollection` is 62 files / 44.8 GB) |
| `cdn-dump` | 4 | 1,105 | a console maker's distribution tree (`nointro_wiiu_cdn_nov_2020_2` is **928 GB**; two PS Vita items are 291 GB and 167 GB) |
| `media` | 3 | 37 | soundtracks and screenshots, not ROMs |
| `other` | 10 | 54 | too few files to be a set — a loose archive, a DAT bundle |

The classifier reads only `mediatype`, `files_count` and `item_size`, which
one search response already returns, so the whole scope is decided before
any item is opened. It is not a curated list of these 71 items: a hand list
would be right about them and silent about the 72nd.

### Duplicates are collapsed on evidence

The census records Archive.org's published `md5`, `sha1` and `crc32` for
every file, under the keys `rom_hub.grouping` reads — so the Hub's one
deduplicator does the work rather than this plugin guessing:

* `NoIntro_VirtualBoy` and `NoIntroVirtualBoy` are separate uploads with
  **31 byte-identical archives**. They collapse on proof.
* `NoIntroNintendo` is titled "No Intro - Nintendo" and shares 31 hashes
  with `NoIntroVirtualBoy`. It is a **mislabelled Virtual Boy set**, and
  that is why it is mapped to `virtualboy` — the hashes say so where the
  title does not.
* `nointro.ws` (`.7z`) and `NoIntro_BandiWonderSwan` (`.zip`) share **zero**
  hashes: the containers differ even though the ROMs inside do not, so the
  name parse decides those instead.

That is also why several overlapping directories are now mapped that
0.3.0 deliberately withheld. `nointro-2600` and `NoIntro-Atari` share 523
byte-identical archives — withholding 1,400 real files to avoid a merge the
deduplicator performs anyway is incompleteness chosen on purpose.

### 912 files are catalogued with no platform

Real files this plugin will not guess a machine for: Satellaview (452) and
Sufami Turbo (26) are SNES *peripherals* with their own RomM slugs and no
EmulatorJS core; `NoIntroIBMPc` (318) is PC software; Benesse Pocket
Challenge, Casio Loopy, PV-1000 and Konami Picno have no verified RomM
slug. They are counted, catalogued and searchable — filing them under a
neighbouring platform would be a remap onto hardware they are not.

What was missing was not a long tail. It was the Nintendo shelf: **no Game
Boy, no Super Nintendo**, and no Amiga or C64 either, because the twelve
shipped directories were the twelve items in the dotted `nointro.*`
family and that family has no `nointro.gb` and no usable `nointro.snes`
(`nointro.snes_202203` is a single 3.2 GB .zip, which is not a directory
of games). `identifier:nointro*` finds 71 items on Archive.org; most are
a lone archive, a DAT-only upload or a "merged" dump with a private tree,
and each of the thirteen added here was checked against the item's file
list rather than read off its title.

Five of them are a **subdirectory of a multi-system item**, which is the
shape that unlocked the most machines for the least work.
`NoIntro-Atari`'s root holds no ROMs — its five consoles are in five
folders named after them — so `NoIntro-Atari/Atari - Lynx` is the
directory, and `archive.org/download/NoIntro-Atari/Atari%20-%20Lynx/` is
an ordinary index page. Same for the ColecoVision, the VIC-20 and the
Plus/4.

| added | platform | archives |
|---|---|---:|
| `nointro-nintendo-gameboy` | `gb` | 1,958 |
| `nointro-snes` | `snes` | 1,746 |
| `nointro.ca` | `amiga` | 3,169 |
| `NoIntroArduboy` | `arduboy` | 532 |
| `nointro.c64` | `c64` | 327 |
| `nointro-commodore-plus4-vic20/Commodore - VIC-20` | `vic-20` | 292 |
| `NoIntro-Coleco/Coleco - ColecoVision` | `colecovision` | 194 |
| `NoIntro-Atari/Atari - Jaguar (J64)` | `jaguar` | 109 |
| `NoIntro-Atari/Atari - Lynx` | `lynx` | 95 |
| `NoIntro_PokemonMini` | `pokemon-mini` | 44 |
| `NoIntroVirtualBoy` | `virtualboy` | 31 |
| `NoIntroVMLabs` | `nuon` | 22 |
| `nointro-commodore-plus4-vic20/Commodore - Plus-4` | `c-plus-4` | 18 |

Three of those platforms have **no EmulatorJS core**, so a ROM imported
under them is catalogued and not playable: `arduboy`, `nuon` and
`pokemon-mini`. That is said here, said by `rom-hub platforms`, and
recorded machine-readably in `rom_hub.playability.NO_EQUIVALENT` with a
sentence per machine naming what it is and what it is not. None of them
was remapped onto a playable slug to make the shelf look better.

**`NoIntroSatellaview` (206) and `NoIntroSufamiTurbo` (13) are
deliberately absent.** Both are Super Nintendo peripherals with their own
RomM slugs and no core; filing them under `snes` — the obvious shortcut —
would be a remap onto hardware they are not, since neither boots without
the peripheral's own BIOS and mapper.

### The cost, stated

Reading all 25 indexes takes **34.8 seconds and 8.75 MB**, timed against
Archive.org index by index on 2026-08-01, and the host kills a plugin at
30 seconds. So a search with **no** `--platform` opens at most
`max_directories` of them (default 10, about fourteen seconds) in
configured order. With `--platform` the budget does not apply at all: one
platform is one directory, so nothing in the list above is out of reach.

## Read this first: this plugin is not Myrient

**Myrient (myrient.erista.me) shut down on 31 March 2026.** This plugin
**sources Archive.org's No-Intro mirrors** — `https://archive.org/download/`,
the `nointro.*` items — and does not contact Myrient at all. `myrient.erista.me`
is not in the manifest allowlist, so it could not reach it even if configured
to try.

It was called `myrient` during development, and shipping it under that name
would have been misleading: the name would have promised a source that no
longer exists while every request actually went to the Internet Archive. Hence
`nointro-archive`, which describes what it really does.

**What is retained is the Myrient *shape*, deliberately.** `base_url` +
directory + a name-matched listing is a layout several mirrors reproduce, and
`nointro_archive/platforms.py` still carries Myrient's own
`No-Intro/<Platform>` directory names. The Myrient index parser is kept, and
so is its regression fixture — a real Myrient listing captured from the
**Wayback Machine** (`tests/fixtures/nointro_archive/myrient_no_intro_game_boy.html`),
because myrient.erista.me no longer serves one. If a mirror reproducing that
tree ever appears, pointing this plugin at it is a config change plus one line
in `manifest.toml`; nothing has to be re-derived.

### Why the shutdown check cannot use status codes

Myrient is not merely offline. It answers **`200 OK` with a static shutdown
notice for every path it ever served — and for paths it never served**:

    $ curl -sI 'https://myrient.erista.me/files/No-Intro/Nintendo - Game Boy/Tetris (World) (Rev 1).zip'
    HTTP/2 200
    content-type: text/html          # 2,334 bytes of shutdown notice

    $ curl -sI 'https://myrient.erista.me/this/path/never/existed'
    HTTP/2 200
    content-type: text/html          # the same 2,334 bytes, byte for byte

There is no status code, no header and no length difference to key off. A
plugin that trusted status codes would report "no results" forever and never
say why, and an importer that trusted them would download the notice, hash it,
upload it, and report `DONE` with an HTML page filed as a ROM.

So the plugin **checks that a page is actually an index** instead: a `200`
from which no entries can be parsed is an error that says so out loud. That
guard (`MIN_USABLE_ENTRIES` in `nointro_archive/index.py`) is the only thing
standing between a dead source and silently returning garbage, and there is a
test replaying the real shutdown page
(`tests/fixtures/nointro_archive/myrient_shutdown.html`) to keep it that way.

### MiNERVA is deliberately not used

MiNERVA Archive, the successor most often pointed to, is live — but its
robots.txt `Disallow`s `/browse/` and `/rom/`, which are exactly the paths a
scripted client would need. Working around a robots directive was not on the
table, so MiNERVA is not a `base_url` default and is not in the allowlist.

## Install

    rom-hub plugin install ./plugins-dev/nointro-archive
    rom-hub search "streets of rage" --platform genesis --limit 5
    rom-hub search tetris --platform gb --limit 5

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `base_url` | `str` | `https://archive.org/download/` | mirror root; must be `https://` and its host must be in the manifest allowlist |
| `collections` | `list[str]` | the 25 directories above | directories to search, **in order** |
| `max_directories` | `int` | `10` | how many indexes a search with **no** `--platform` may open. Measured against the host's 30-second ceiling; `--platform` ignores it |
| `collection` | `str` | `No-Intro` | RomM collection imported ROMs are grouped into |

The default `collections` order is the order a platform-less search opens
them in, so the sets people ask for most are first; `manifest.toml`
carries the list with its per-directory counts beside it.

**Repointing `base_url` at another host also needs a `manifest.toml` edit and
a reinstall.** That is deliberate. The allowlist is what the broker enforces;
if config alone could move it, config alone could widen the plugin's network
reach, and installing a plugin would stop being a decision about where it
goes.

## Being a considerate client

A No-Intro platform directory is hundreds of kilobytes of HTML listing
thousands of files, served by someone giving bandwidth away.

- **Each directory is fetched at most once per plugin process** and shared
  between `search` and `importer`, so an import that follows a search costs no
  request at all. The cache is bounded (32 directories, oldest evicted) so a
  long-lived host cannot accumulate everything it has ever seen.
- **A browse stops as soon as `limit` results exist.** With no query there is
  nothing to rank, so the first directory answers it as well as three would.
- **A query opens at least three directories before it may stop**, and at most
  `max_directories`. That is the one place this plugin deliberately spends
  more than it used to, and it buys the ranking below.
- **`--platform` filters before any request.** A file's platform *is* its
  directory here, so `--platform genesis` is one fetch instead of twenty-five.
- **No concurrency is added.** The plugin has no sockets; `ctx.http` is an RPC
  the host serves one call at a time, and nothing here tries to work around
  that.

## Parsing

One parser, not one per server. Apache's `<pre>` block, nginx's fancyindex
table, lighttpd's table and Archive.org's petabox table disagree about
everything except that an entry is an `<a href>` with a size somewhere to its
right — so entries are selected by *shape*:

- **Only same-directory relative links count.** Anything absolute, any
  `?C=N&O=A` sort link, any `#anchor`, any `../` is chrome. Filtering by shape
  rather than by a list of known chrome strings is what lets one parser handle
  four servers.
- **A duplicate href is chrome too.** Archive.org prints every file twice —
  `<a href="Game.7z">Game.7z</a> (<a href="Game.7z/">View Contents</a>)` — and
  the twin differs only by a trailing slash, so deduplicating on the
  slash-stripped href drops it without this code knowing the words "View
  Contents".
- **Names are decoded, hrefs are not.** The name is what you search; the href
  is what the server said, and the plan uses it verbatim so the plugin never
  has to guess how a mirror percent-encodes.
- **Sizes are a hint.** `35.9 KiB` (Myrient) and `70.2K` (petabox) both parse,
  both as 1024-based; anything unparseable becomes `None` rather than an
  error, because the host learns the real length from the response and a
  display number must not be able to fail a plan.
- **Metadata files are not payloads.** `*_meta.xml`, `*_files.xml`,
  `*_meta.sqlite`, `*_archive.torrent`, `*_reviews.xml` are Archive.org
  bookkeeping. They never appear in results and are refused by name if asked
  for directly.

Subdirectories are listed but never descended into. Walking a whole mirror is
not a thing a search should do to someone else's bandwidth; naming the
directory in `collections` is.

## Results are ranked, not taken in directory order

The old walk kept the first `limit` matches it happened to meet and
stopped. Two things followed, and both are the kind of wrong that looks
like a thin source:

- **A better match one directory down was invisible.** `batman` at
  `--limit 2` returned two `Adventures of Batman & Robin, The (…) (Beta)`
  rows out of the Game Gear index and never opened another directory — so
  the Lynx's `Batman Returns`, which actually starts with the word, was
  never seen.
- **A beta outranked the game.** No-Intro filenames sort alphabetically
  and `Klax (USA, Europe) (Beta).zip` sorts *before* `Klax (USA,
  Europe).zip`, so the beta came first.

Matches are now scored across every directory the walk opened:

| score | means |
|---:|---|
| 3 | the title **is** the query, once regions, revisions and punctuation are stripped |
| 2 | the title **starts with** the query |
| 1 | every term appears somewhere in the filename |

then ties break on the shorter filename (which prefers the plain release
over its `(Rev 1) (Beta)` siblings) and then on configured directory
order. Three tiers rather than a similarity score, because the useful
distinction is coarse and a metric would invent precision the data does
not have.

Grouping in the host then collapses regional variants of one game into a
single row with a variant count, so casting a wider net costs an operator
nothing on screen.

## Platform mapping

The only thing a directory index says about a ROM's platform is which
directory it is in, so `nointro_archive/platforms.py` maps directory names to RomM
platform slugs. **Exact match, no fallback** — an unmapped directory raises
**"needs mapping"** and names itself.

A prefix rule over `nointro.*` would look free and be wrong exactly where it
matters, because the suffixes are abbreviations chosen by whoever uploaded the
set:

| Directory | Is | Not |
|---|---|---|
| `nointro.sg` | PC Engine **SuperGrafx** (`supergrafx`) | Sega Game Gear |
| `nointro.ca` | Commodore **Amiga** (`amiga`) | anything starting "ca" |
| `nointro.ms-mkiii` | Master System / Mark III (`sms`) | — |
| `nointro.md` | Mega Drive, which RomM files as `genesis` | — |
| `NoIntro-Atari/Atari - Lynx` | the Lynx subdirectory of a five-console item | the item root, which holds no ROMs |

The table is checked **before any request**: a `collections` entry nobody
mapped is a configuration error, not a per-result oddity, and paying for a
fetch to discover it helps nobody. Values were checked against RomM's own
platform-slug enum.

## The importer confirms before it plans

A URL could be built from a source id by concatenation alone. It would be
wrong the first time a set is rebuilt and a file renamed — and the host would
then fetch the mirror's 404 page, hash it, upload it and report `DONE`. So the
importer reads the directory index (usually from the cache the search already
filled) and matches the entry by name. No entry, no plan.

Source ids are `<directory>/<file>`. A Myrient-layout directory contains
slashes of its own, so the split is done against the configured `collections`
rather than at the last slash — which also means a source id naming a
directory this install does not search is refused rather than guessed at.

## Legal position

**Plainly: the default `collections` are No-Intro sets of commercial console
ROMs, held on the Internet Archive. They are copyrighted works, and this
plugin does not launder that.** No-Intro sets are checksum-verified dumps of
retail cartridges; the copyright in those games belongs to their publishers,
most of whom have never licensed redistribution. Whether you may download them
depends on where you live and on whether you own the original media — in the
United States, for example, the archival exemption courts have recognised does
not extend to downloading a copy of something you do not own.

What this plugin does and does not do:

- It fetches only from hosts named in `manifest.toml`, over HTTPS, one request
  at a time, with the Hub's `rom-hub/0.1` User-Agent.
- It does not circumvent any access control, paywall, login or robots
  directive. `https://archive.org/download/` is a public, unauthenticated
  directory listing; the Internet Archive's robots.txt does not disallow it.
- It does not mirror or bulk-download: a query reads index pages, and an
  import fetches exactly one file you asked for.
- **MiNERVA Archive was considered and rejected** as a `base_url` default
  because its robots.txt `Disallow`s `/browse/` and `/rom/` — the paths a
  scripted client would need. Working around that was not on the table.

If you want this plugin pointed only at material that is unambiguously free to
redistribute, that is what `base_url` and `collections` are for; the
`homebrew` plugin in this repository is built for that case from the start.

## Network

Declared allowlist: `archive.org`, `*.archive.org`. Downloads redirect from
`archive.org` to a node like `dn721808.ca.archive.org`, and the Hub
re-validates **every redirect hop** against this list, which is why the
wildcard is there and why nothing broader is. `myrient.erista.me` is
deliberately *not* listed: an allowlist is a statement about where the plugin
actually goes, and a dead host in it would be decoration.
