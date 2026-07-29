# Homebrew plugin for ROM Hub — gbdev's Homebrew Hub

Implements the RPP v1 `search`, `importer` and `metadata` capabilities against
[Homebrew Hub](https://hh.gbdev.io), the gbdev community's archive of Game Boy,
Game Boy Color, Game Boy Advance and NES **homebrew**.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `hh3.gbdev.io/api/search` | server-side search across all entries |
| `importer` | the same endpoint, then `/static/…` | plans the entry's default ROM file |
| `metadata` | the same endpoint, then `/static/…` | proposes the submitter's title and cover; the **Hub** fetches the image |

## Install

    rom-hub plugin install ./plugins-dev/homebrew
    rom-hub search "snake" --limit 5
    rom-hub enrich homebrew 1

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `typetag` | `str` | `""` | restrict to one kind of entry: `game`, `demo`, `music`, `tool`. Empty means no filter |
| `max_pages` | `int` | `3` | how many 10-entry result pages one query may walk |
| `collection` | `str` | `Homebrew` | RomM collection imported ROMs are grouped into |
| `set_name` | `bool` | `true` | `metadata` only: write the Hub's title over the library's |

## What `metadata` sets

**`name`**, from the Hub's own title, and **`artwork_url`**, pointing at the
entry's cover image. Nothing else — `MetadataPatch` reads an absent field as
"leave the library alone", and that is used here rather than worked around.

**Why this plugin may write a title when `libretro-thumbnails` may not.** That
plugin refuses because what it has is a No-Intro DAT string: a filename from a
different project, not a curated name. The Hub's title is the one the *author*
submitted with the game. For homebrew there is no publisher of record other
than the person who wrote it, so this is as close to authoritative as the
material gets. It is still config, defaulting on, because an operator who has
curated their library is entitled to keep their spelling.

**Only a file actually named `cover.*` becomes artwork.** Roughly half the
Hub's entries carry one; the rest have in-game screenshots. Promoting a
screenshot to box art would fill a library with pictures of gameplay that
somebody then has to undo one at a time, so no cover means no `artwork_url`.

**Resolution is exact or it is a refusal.** Give it `--source-id <slug>` and it
looks the entry up directly. Without one it searches on the rom's name — 
narrowed by platform when the rom's platform is one of the four the Hub
carries — and requires **exactly one** entry whose title matches once case and
punctuation are ignored. It is an equality test, not a prefix one, so `Snake`
cannot pick up `Snake GBDK`. Three live entries are titled exactly `Snake`; a
query landing on those refuses and names all three rather than choosing.

No description or release date is written. RPP v1 has nowhere to put them: its
`raw_*_metadata` fields each belong to a named provider, and putting the Hub's
payload in one belonging to IGDB or ScreenScraper would be a lie in the
database.

## Legal position — why this source is legitimate

**Homebrew is new software written by hobbyists for old hardware.** It is not a
dump of a commercial cartridge. The copyright in each entry belongs to the
person who wrote it, and Homebrew Hub exists because those authors wanted their
work archived and playable.

Concretely:

- Entries reach the Hub through **public submission**: an author or a fan files
  an issue or a pull request against
  [`gbdev/homebrewhub-submit`](https://github.com/gbdev/homebrewhub-submit),
  [`gbdev/database`](https://github.com/gbdev/database) (GB/GBC),
  `gbadev-org` (GBA) or
  [`nesdev-org/homebrew-db`](https://github.com/nesdev-org/homebrew-db) (NES).
  The databases are open repositories with a schema and CI; anything in them is
  there on the record.
- gbdev states the terms plainly: the Homebrew Hub project itself is GPLv3, and
  *"each game, homebrew, demo and their related asset, file, screenshot or
  source code is released under different license terms and copyright
  holders"*, with a standing takedown contact for rights holders.
- So the redistribution permission is **per entry and held by the Hub**, not
  asserted by this plugin. This plugin fetches from the Hub's own public
  `/static/` path — the same URL the Hub's own web player uses — and does not
  republish, re-host or mirror anything.

That is the honest boundary: **gbdev vouches for the licensing of what it
hosts; this plugin does not add a claim on top of it.** Individual entries carry
their own terms, which is why results link back to `hh.gbdev.io/g/<slug>` —
that page is where an entry's licence, author and source live. If you need a
specific licence before importing something, read the entry page.

What this plugin does not do:

- No authentication, no API key, nothing behind a login.
- No robots violation. `hh3.gbdev.io/robots.txt` is `User-agent: *` /
  `Allow: /`; the plugin identifies itself with the Hub's `rom-hub/0.1`
  User-Agent and makes at most `max_pages` requests per query, one at a time.
- No bulk mirroring. A query reads result pages; an import fetches exactly the
  one file you asked for.

## Why this source and not PDRoms

PDRoms was the first candidate — it is alive, actively updated, and its
robots.txt explicitly `Allow`s `/files/`. It was rejected on two counts, both
measured against the live site:

1. **Its file archive has no search a script may use.** `Disallow: /?s=` and
   `Disallow: /search/` cover PDRoms' own search, and its WordPress REST API
   (`/wp-json/wp/v2/search`) indexes only `post` and `page` — not the
   `pdr_file_post` type the ROMs live in. Working around a `Disallow` was not
   on the table, so search would have meant walking platform listings.
2. **Those listings are ten files per page and fixed** (`posts_per_page` is
   ignored), alphabetically ordered, ~36 pages for Game Boy alone at ~0.5 s
   each. A substring query cannot be answered without scanning, so recall
   would have been "whatever was in the first few pages" and the request cost
   would have been rude for a site giving bandwidth away.

Homebrew Hub answers the same question in one request, with an explicit
platform field and real filenames. The legal position is at least as good.

## Platform mapping

`homebrew/platforms.py` maps the Hub's `platform` field to RomM platform slugs:

| Hub | RomM | Entries |
|---|---|---|
| `GB` | `gb` | 913 |
| `GBC` | `gbc` | 447 |
| `GBA` | `gba` | 188 |
| `NES` | `nes` | 23 |

That is the entire vocabulary — the four counts sum exactly to the 1,571
entries live at the time of writing. Exact match, no fallback: an unknown
value raises **"needs mapping"** and names itself.

**`GBC` is not softened into `gb`.** The temptation is real — the Hub keeps
both in one `database-gb` repository, both run on the same emulators, and
merging them would never visibly break anything. It would still be wrong, and
a library that quietly merged them cannot be un-merged later, because nothing
records which entries were guessed.

The reverse direction is what `--platform` uses, so the filter runs on the
server. Two consequences worth knowing:

- The Hub's filter is **case-sensitive**: `platform=GBC` matches, `platform=gbc`
  returns zero. That lives in the mapping table, not in a caller.
- Asking for a platform this archive does not hold (`--platform dc`) returns an
  empty list **without a request**. It is a reasonable question with a boring
  answer.

### Entries with no platform at all

Live records exist with no `platform` field, and a few with `title: null`.
Untitled records are skipped at search; **platform-less records are imported
only with `--platform`**. Reading `basepath: database-gb` as "Game Boy" was
considered and rejected: that repository holds Game Boy Color titles too, so
the shortcut would misfile every one of them.

## Choosing the file

An entry can list several files. The Hub's own `default: true` flag decides —
that is the submitter stating which one is the ROM, and it is better evidence
than any heuristic. Where no file is flagged (older records), the first listed
wins, which is stable because the API's order is.

`filename` is sometimes a bare name and sometimes a path inside the entry
(`files/quokka-wokka_jam.gba`). The path stays in the download URL, because it
is where the file lives; `FetchFile.filename` gets the sanitised bare name,
because that is what the host opens for writing.

## Looking an entry up

The Hub has no fetch-one-entry endpoint, so the importer queries the slug and
then **matches it exactly** in the results. `?q=<slug>` is a text search and
can answer with a near miss; importing "the closest thing to what you asked
for" is exactly the failure this codebase refuses everywhere else. No exact
match, no plan.

## Network

Declared allowlist: `hh3.gbdev.io`. One host does both jobs — `/api/search`
answers the queries and `/static/` serves the ROMs, with no redirect off it.
`hh.gbdev.io`, the human site whose `/g/<slug>` pages results link to, is
deliberately **not** declared: nothing here ever fetches it. `SearchResult.url`
is shown to a person, never retrieved by the Hub.
