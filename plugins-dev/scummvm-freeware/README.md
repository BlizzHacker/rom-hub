# ScummVM freeware plugin for ROM Hub

Implements the RPP v1 `search`, `importer` and `metadata` capabilities
against `https://downloads.scummvm.org/frs/extras/` — **twenty-eight**
complete adventure games the ScummVM project distributes free, because
their rights holders said so.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `/frs/extras/<directory>/` | matches a table of twenty-eight games, then lists the matching directories |
| `importer` | the same listing | re-reads it, then plans the exact archive |
| `metadata` | **nothing** | reads the table and gives an archive its real title |

Beneath a Steel Sky. Flight of the Amazon Queen. Lure of the Temptress.
Dráscula. DreamWeb. Sołtys. Sfinx. Mystery House. Nippon Safes, Inc. Broken
Sword 2.5. The Griffon Legend. God of Thunder — and, new in 0.2.0, the
sixteen SLUDGE, Wintermute and WAGE games on the same page: Above The
Waves, Cubert Badbone P.I., Frasse and the Peas of Kejick, Full Moon, The
Interview, Lepton's Quest, Life Flashes By, Mandy Christmas Adventure,
Nathan's Second Chance, Out Of Order, Robin's Rescue, Sam and Max
Flintlocked, The Game That Takes Place on a Cruise Ship, The Secret of
Tremendous Corporation, Helga Deep In Trouble, and the WAGE collection.

## What changed in 0.2.0

| | before | after |
|---|---:|---:|
| games | 12 | **28** |
| directories read | 12 | 15 |
| games one search may list | 6 | **16** (capped at 28) |
| capabilities | search, importer | + **metadata** |

**The sixteen came from re-reading the source, not from widening a rule.**
`www.scummvm.org/games/` — whose heading is literally "Download freeware
games" — names 28 games where this table carried 12. The ones it was
missing all live in engine directories rather than one-per-title ones, and
that is the reason they were skipped: `SLUDGE/`, `Wintermute/` and `WAGE/`
hold many authors' games together, and a directory allowlist cannot
describe them.

**So those rows name their archives.** `SLUDGE/` lists 35 files where the
games page names 30; `Wintermute/` lists 17 where it names one. A row with
an empty file list offers whatever its directory lists — which is what
keeps a ScummVM re-release (`drascula-int-1.0.zip` becoming `-1.1.zip`)
working with no code change — and a row with a file list offers exactly
those names. Five SLUDGE archives and sixteen Wintermute ones stay
unreachable because nobody vouched for them, which is the same rule the
twelve-row version applied one level up.

One directory is also read **once** however many of its games matched. The
fourteen SLUDGE games used to be fourteen identical round trips.

## Why this material is legitimate

**Each of the twelve is free because a named rights holder released it**, and
the plugin carries that sentence per game in `games.py`'s `freed_by` column —
surfaced on every search result, so the claim travels with the game instead of
living only here:

- **Revolution Software** released *Beneath a Steel Sky* and *Lure of the
  Temptress* as freeware in 2003. It holds the rights to both.
- **John Passfield and Steve Stamatiadis**, the authors, released *Flight of
  the Amazon Queen* as freeware in 2004.
- **Neil Dodwell and David Dew** of Creative Reality released *DreamWeb* as
  freeware in 2012.
- **Alcachofa Soft** released *Dráscula*; **L.K. Avalon** released *Sołtys* and
  *Sfinx*; **Dynabyte** released *Nippon Safes, Inc.*
- **Ken and Roberta Williams** placed *Mystery House* in the public domain in
  1987 — the one entry here that is public domain rather than freeware.
- *Broken Sword 2.5* is a free fan-made game distributed by its own authors;
  *The Griffon Legend* and *God of Thunder* were released free by theirs.

None of this is abandonware and none of it is a dump of retail media. What you
download is ScummVM-ready game data the project has hosted for years and links
from its own front page.

## robots.txt, in detail, because it matters here

Two hosts, two different answers, and the plugin uses exactly one of them.

- **`downloads.scummvm.org`** — where this plugin reads and where the archives
  live. `GET /robots.txt` answers **404**: the host publishes no crawl
  directives at all. Verified 2026-07-29.
- **`www.scummvm.org`** — a different host, whose robots.txt does
  `Disallow: /frs` and `Disallow: /downloads` for `User-agent: *`. **This
  plugin never requests anything from it.** It is not in `manifest.toml`'s
  `network` list, so the broker would refuse the request if a future version
  tried.

The list of twelve games in `games.py` was read once, by a human, from
`https://www.scummvm.org/games/` — a path that same robots.txt permits — and
checked in. It is not fetched at runtime. That ordering is the point: a
robots-permitted page was read to build a static table, and the runtime
traffic goes only to the host with no directives at all.

## The directory table is the safety model

`/frs/extras/` is one directory per *title*, and **only some of those titles
are free games.** The rest are add-ons for games still very much on sale:
`Blade Runner/` holds subtitles, `Toonstruck/` holds cutscene subtitles,
`Elvira 2/` holds sound samples, `Broken Sword I and II/` holds a subtitle
pack.

So `games.py` is an **allowlist**, not a convenience. A directory not in it is
unreachable: search never looks at it and the importer refuses a `source_id`
naming it. It is not filtered out after the fact — there is no code path that
can build a URL into it.

Within an allowed directory, checksum sidecars (`*.sha256`) and manuals
(`dreamweb-manuals-en-highres.zip`) are excluded by name. Language and audio
add-ons (`drascula-audio-flac-2.0.zip`, `lang_he.b25c`) are **kept**: unlike a
manual they are part of playing the game, and an operator who asks for the FLAC
audio pack knows what they asked for.

## Search

    rom-hub search scummvm-freeware "steel sky"
    rom-hub search scummvm-freeware drascula

Matching is done **in memory** against the twenty-eight titles and slugs, so a query
nobody's title contains costs zero requests and a one-word query typically
costs one. Only the games that matched have their directory listed.

One result per *file*, not per game: Dráscula is six archives and DreamWeb is
seven language editions, and collapsing those would mean the plugin picking a
language for you. The filename is in the title so the choice is visible.

## Importing

    rom-hub import scummvm-freeware "soltys/soltys-en-v1.0.zip"

The importer re-reads the directory and requires an **exact** filename match.
ScummVM re-releases these archives — `drascula-int-1.0.zip` became
`drascula-int-1.1.zip` — so a name from an older search can be gone, and the
refusal lists what the directory offers now.

Everything lands in the `ScummVM freeware` RomM collection by default.

## Platform

All twenty-eight are filed under RomM's `scummvm` platform, which is what they are:
ScummVM-ready game data, not dumps of the original floppies or CDs. The slug is
stated **per row** rather than as one module constant, so a row added later for
a DOS-only or Amiga-only freeware drop can say so without anyone having to
notice that a shared default stopped being true. `--platform` overrides.

## What `metadata` sets

**`name`, and nothing else, and it makes no request at all.**

That is the honest shape of it. The source is a download server:
`downloads.scummvm.org` publishes archives and checksums and nothing else
— no cover art, no description, no release date, no JSON of any kind. The
one thing this plugin knows that a library does not is ScummVM's own title
for the archive, and that is checked into `games.py`. There is no
`artwork_url` here and there will not be one until the source grows
artwork; fetching a cover from somewhere else would be this plugin
asserting something it has no standing to assert.

**One field is still worth a capability here**, because of what these
archives are called. An import lands as `atw.zip`, `tgttpoacs.zip`,
`nsc.zip`, `gotfree.zip` — fourteen of the twenty-eight games are on an
engine shelf where the archive name is an abbreviation nobody would
recognise. *The Game That Takes Place on a Cruise Ship* is a real title
and `tgttpoacs.zip` is what a library shows without this.

Matching is by archive name first (exact, from the table, so a rom named
`frasse-2.03.zip` resolves with no guessing at all) and then by title, for
the twelve directory-per-title rows whose archives are deliberately not
enumerated.

## This plugin is catalogue-only

**RomM's web player cannot run any of these**, and the reason is structural:
the player is EmulatorJS, EmulatorJS runs libretro cores, and `scummvm` has no
entry in RomM 4.9.2's `_EJS_CORES_MAP`. A game imported here appears in the
library, correctly filed and correctly described, and does nothing when
clicked. `rom-hub import` warns before the download starts; `--allow-unplayable`
silences it, and `rom-hub platforms` shows every platform in the same position.

To actually play one, point a real ScummVM at the extracted directory — which
is what the material is packaged for, and why it is worth having in a library
whether or not a browser can start it.

**`--platform dos` is the wrong fix and the plugin will not do it for you.**
`dos` does have a core (`dosbox_pure`), and DOSBox can run *some* of these
games from their original floppies — but what this plugin downloads is not
that. It is ScummVM game data: the `.scummvm` descriptor, the engine's own
resource layout, and in several cases assets that never existed in a DOS
release at all. Handing that to DOSBox produces a shelf entry that claims to
be playable and is not, which is strictly worse than one that says plainly
that it is a catalogue entry. The override exists for a row that genuinely is
a DOS drop; it is not a way to make this one look better.

## Install

    rom-hub plugin install https://github.com/BlizzHacker/rom-hub-scummvm-freeware --ref v0.1.0

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `max_games` | `int` | `16` | Hard bound on matched games per search (capped at 28, which is all of them). One *directory* is one round trip, so the fourteen SLUDGE games cost one between them |
| `collection` | `str` | `ScummVM freeware` | RomM collection imports are filed under |

## Notes for the next person

- **The filenames are read, the directories are not.** Holding a table of
  filenames as well would mean every ScummVM re-release silently stops
  importing. Reading the listing picks up a new build with no code change,
  while the directory allowlist still decides what may be looked at.
- **Dráscula's directory has a trailing underscore** —
  `Drascula_ The Vampire Strikes Back` — where the title has a colon. The
  server does not redirect a near miss; it 404s.
- **The size column is Apache's rounded `3.3M`.** It is carried as
  `extra.size_text` and never as `size_bytes`, so nothing downstream verifies
  a download against a rounded number.
- A document that is not a `/frs/extras` index **raises** rather than parsing
  to no rows, so a maintenance page cannot read as an empty game directory.
