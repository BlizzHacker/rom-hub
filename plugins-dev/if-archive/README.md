# if-archive

Interactive fiction from the [IF Archive](https://ifarchive.org/) — the
community's own archive of text adventures, running since 1992 and
maintained by the [Interactive Fiction Technology
Foundation](https://iftechfoundation.org/).

`search` + `importer`. RPP v1.

## Which RomM issue this answers

[**rommapp/romm#2140** — *[Feature] Support for Interactive Fiction (Z-Code,
Glulx, etc.) via Parchment Web Player*](https://github.com/rommapp/romm/issues/2140)
— open since 2025-07-23, six 👍, unbuilt.

The issue asks for three things: a platform, file-extension recognition,
and a source of games and metadata. This plugin does the third and half of
the first, from outside RomM:

| The issue asks for | What this plugin does |
|---|---|
| a new `interactive-fiction` platform | **not needed** — RomM 4.9.2 already ships `z-machine`, `glulx`, `tads` and `hugo` as supported platform slugs. Four runtimes, not one. |
| recognise `.z3 .z4 .z5 .z8 .zlb .zblorb .ulx .glb .gblorb .gam .t3 .hex .taf` | every one of those is in `formats.py`. Twelve map to a RomM platform; `.taf` (ADRIFT) is known and **refuses**, because RomM has no ADRIFT platform to file it under. |
| IFDB as a metadata source, Parchment as a player | **not done.** See *ifdb.org*, below, and *What this does not do*. |

Checked against a live RomM 4.9.2 rather than assumed: `GET
/api/platforms/supported` returns 458 slugs and exactly four of them are IF
runtimes.

## How much of the archive this reaches

**Thirty directories, 1,410 mapped story files** — counted by parsing
every index on 2026-08-01, not estimated.

| | before (0.1.0) | after (0.2.0) |
|---|---:|---:|
| directories read | 4 | **30** |
| mapped story files | 1,158 | **1,410** |

The four runtime roots were never the whole shelf. The archive nests
translations and superseded releases one level down, and that is where
the missing quarter lives: `zcode/old` holds 59 mapped story files,
`glulx/old` 46, `zcode/spanish` 42, `zcode/italian` 20, `zcode/french`
19. Twenty-six such subdirectories exist across the four runtimes and
none of them was in the default list.

They were also the *cheap* part. Thirty indexes is 1,251 KB and 4.4
seconds over one keep-alive connection, against the host's 30-second
ceiling — and **914 KB of that is the four roots**, which were being read
already. The twenty-six subdirectories are 338 KB between them.

Not every one pays off, and the fixtures say so both ways:
`zcode/german` is 19 files of which 16 are mapped Z-code, and `hugo/old`
is two files of which **neither** is a story file. Thirty directories are
not thirty wins.

**`--platform` deliberately does not narrow the directory list.** That is
the obvious optimisation here and it would be wrong: the extension
decides the runtime, never the directory. `The Cruel Count's Castle.gblorb`
sits in `games/zcode/` and is a **Glulx** game; `zenspeak.blb` sits there
and is not a game at all. A `--platform glulx` search that skipped
`zcode/` on the strength of its name would silently lose exactly the
files this plugin already goes to some trouble not to misfile.

## What it does

- Reads the IF Archive's own directory indexes — the `zcode`, `glulx`,
  `tads` and `hugo` runtimes and their twenty-six subdirectories by
  default — and searches them by title.
- Imports one bare story file. The URL, the platform and the filename all
  come from the archive path, so an import makes **no HTTP request of its
  own**.
- Files everything under one library collection (`IF Archive`) so you can
  see what came from here.

A result carries the archive's own description, its date, the runtime in
plain words (`Z-machine`, `Glulx`, `TADS 2`, `Hugo`), and whether the query
matched the filename or fell back to the description.

## This plugin is catalogue-only

**Nothing it imports can be played in the library's web player, and that is
permanent as things stand.** RomM's player is EmulatorJS, EmulatorJS runs
libretro cores, and there is no libretro core for a Z-machine, Glulx, TADS or
Hugo interpreter — those want Parchment or a `.z5` interpreter, which is the
other half of #2140 and RomM's to build. All four platforms this plugin files
under are absent from RomM 4.9.2's `_EJS_CORES_MAP`.

So a story file imported here lands in the library, gets a shelf and a title,
and does **nothing at all** when clicked. `rom-hub import` says so before it
starts:

    warning: platform 'z-machine' cannot be played in the library's web
    player: RomM 4.9.2 has no EmulatorJS core for it ...

That is not a reason to avoid this plugin — a catalogue of interactive fiction
is a real thing to keep, the archive has been curated since 1992, and every
file it lists is playable in any of a dozen interpreters you already have. It
*is* a reason to know what you are getting before you import 400 of them. Pass
`--allow-unplayable` to stop being told; run `rom-hub platforms` to see the
whole picture.

Filing these under something playable would be worse than leaving them:
`dos` has a core, and a Z-code file handed to DOSBox is still not a game
anybody can start.

## What this does not do

- **It does not play anything.** Getting a `.z5` into RomM is not the same
  as RomM having a Parchment tab. That half of #2140 is RomM's to build, and
  until it exists every import here is catalogue-only — see above.
- **It does not fetch metadata.** No `metadata` capability, no covers, no
  IFDB. A story file and a platform, nothing more.
- **It does not unpack archives.** Roughly a fifth of the files in these
  directories are `.zip` bundles containing a story file plus feelies, and
  the Hub's import path writes files, it does not extract them. A `.zip`
  refuses by name.
- **It does not collapse Z-machine and Glulx.** They are different virtual
  machines needing different interpreters. A library that filed both under
  one platform would be a library where half the entries will not start.

## Configuration

| key | type | default | what it does |
|---|---|---|---|
| `directories` | `list[str]` | `[]` — meaning the thirty below | which of the archive's game directories to search. Each one is a separate index page per search (the `zcode` one is 491 KB), so the list is capped at 40. |
| `collection` | `str` | `"IF Archive"` | the library collection imports are filed under. |

The default thirty are the four runtime roots plus every subdirectory the
archive nests under them:

    zcode  zcode/old  zcode/danish  zcode/dutch  zcode/french
    zcode/german  zcode/italian  zcode/lojban  zcode/russian
    zcode/slovenian  zcode/spanish  zcode/swedish
    glulx  glulx/old  glulx/french  glulx/german  glulx/italian
    glulx/russian  glulx/spanish  glulx/swedish
    tads  tads/old  tads/czech  tads/german  tads/italian
    tads/russian  tads/spanish  tads/swedish
    hugo  hugo/old

That list is **checked in rather than discovered**. Discovering it means
parsing each root's `subdirlist` and then fetching whatever it named,
which is a plugin choosing at runtime which URLs to request; a new
language directory is one line in `if_archive/index.py` instead.

Widening further is supported and honest. There are 96 directories under
`if-archive/games/`, and pointing this at `adrift`, `alan`, `quest` or
`aas` will find the games — they appear in search with no platform set,
and refuse at import with a message naming the format and saying RomM has
no slug for it.

## The source's terms, in plain language

The IF Archive publishes its
[Terms of Use](https://ifarchive.org/misc/license.html). The parts that
decide what this plugin may do:

- **The games belong to their authors.** The archive's words: the contents
  "are the intellectual property of their original creators", and are
  "where possible, archived and distributed with permission".
- **Some carry a licence, and then that licence governs.** Plenty of IF is
  released under Creative Commons or an open-source licence, and a good
  deal of it is explicitly placed in the public domain by its author.
- **Anything with no attached licence is presumed to be for personal use
  only.** That is the archive's own default, stated in those words, and it
  is why this README does not describe the IF Archive as "freely
  distributable". Most of it is free to download and play. Not all of it is
  free to redistribute, and the archive does not claim otherwise.
- **It may not be compiled for commercial distribution.** Redistributing a
  subset commercially requires obeying each file's licence and getting
  permission for every file that has none — permission the archive
  maintainers say they cannot give.
- **Material with no clear terms is hosted deliberately**, as "part of the
  common heritage of interactive fiction", with a DMCA takedown route for
  rights holders who object.

Downloading a game to play it is what the archive is for. Building a
redistributable collection out of it is not, and this plugin will not help
you tell those apart — only the individual game's own licence text can.

**The descriptions this plugin surfaces are not the games.** The archive
licenses its "ancillary content (metadata, indexes, and file
descriptions)" under **CC BY 4.0**, attributed to the Interactive Fiction
Technology Foundation (www.iftechfoundation.org). `extra.description` on a
search result is that content.

**robots.txt:** `ifarchive.org` serves none at all — `/robots.txt` is a
hard 404 (verified 2026-07-31), so there is no crawl directive to observe.
The plugin reads at most a handful of index pages per search regardless.

## ifdb.org: checked, and not used

Issue #2140 proposes IFDB as the metadata source, and every index entry
here links to an IFDB page. This plugin does not touch ifdb.org and does
not declare it in the allowlist.

What `https://ifdb.org/robots.txt` actually returns (verified 2026-07-31)
is 1,248 bytes that are **entirely comments**: Cloudflare's "content
signals" preamble explaining what `search`, `ai-input` and `ai-train` mean,
followed by nothing. No `User-agent` group, no `Disallow`, and — the part
that matters — no `Content-Signal` line either. So ClaudeBot is not
disallowed; nor is anything else, and nor is anything permitted. The
preamble's own clause (c) covers this case: where no signal is given, the
operator "neither grants nor restricts permission".

That is an absence of an answer rather than a yes. Given that, and given
that the IF Archive alone is a complete source for what this plugin does,
ifdb.org is left alone. If somebody later wants IFDB metadata, that is a
separate `metadata` plugin, a separate allowlist, and a conversation with
IFTF first.

## Two things that will bite the next person

**`ifarchive.org` 302s most downloads to `ukrestrict.ifarchive.org`.**
Sampled 2026-07-31: 8 of 8 files in `games/glulx`, `games/tads` and
`games/hugo` redirect there; 8 of 8 in `games/zcode` do not. Both hosts are
in `permissions.network` for that reason. The Hub re-validates every
redirect hop against the allowlist rather than following it, so a manifest
declaring only `ifarchive.org` would import Z-code games perfectly and fail
every Glulx, TADS and Hugo import — with a message about an undeclared
host, which is nowhere near the cause.

**The `<dt id="...">` is not the filename.** Each index entry carries the
name twice, escaped two different ways:

    <dt id="Apollo18=2B=20.zip"><a href="/if-archive/games/zcode/Apollo18%2B20.zip">

The `id` is a fragment identifier using `=XX=`; the `href` is
percent-encoded. Read the `id` as a filename and you get `Apollo18+20` for
a file called `Apollo18+20.zip`, and `The=20=Cruel=20=Count=27=s=20=Castle`
round-trips through nothing at all. This plugin reads the `href` and
percent-decodes it once.

Related: 40 of the 1,509 files in the four runtime roots have a
percent-encoded `href`, and **decoded, every one of them is a filename the
Hub already accepts** — `Escape!.zblorb`, `Ancient Treasure, Secret
Spider.zblorb`, `Apollo18+20.zip`. Refusing names containing `%`, or
stripping the punctuation, would silently drop all forty. Decode, then let
the host's validator have the last word.

## Install

    rom-hub plugin install if-archive
    rom-hub search if-archive "colossal cave"
    rom-hub import if-archive "if-archive/games/zcode/905.z5"

`source_id` is the archive path. It is stable, it is what the archive's own
URLs use, and you can paste one straight off the website.

## Licence

MIT (this plugin's own code). The games are the archive's business and
their authors'; see *The source's terms* above.
