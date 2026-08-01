# libretro Database plugin for ROM Hub

Implements the RPP v1 `metadata` capability: resolves a ROM to its **catalogue
entry** in [libretro/libretro-database](https://github.com/libretro/libretro-database)
— the DAT corpus RetroArch's playlist scanner is built on — and proposes that
entry's name.

| Capability | Endpoint | Does |
|---|---|---|
| `metadata` | `raw.githubusercontent.com/libretro/libretro-database/<ref>/metadat/<set>/<System>.dat` | proposes `name` from the DAT's `game (name …)` |

**No API key.** The files are plain text in a public repository.

## Install

    rom-hub plugin install ./plugins-dev/libretro-database
    rom-hub enrich libretro-database 1

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `sets` | `list[str]` | `["no-intro", "redump"]` | which `metadat/` directories to read |
| `ref` | `str` | `"master"` | git ref to read the DATs at; pin a tag or a commit for a fixed corpus |
| `set_name` | `bool` | `true` | propose the catalogue name |
| `summary` | `bool` | `true` | propose the entry's region and serial, plus whatever `details` finds, as RomM's `summary` |
| `details` | `list[str]` | `["genre", "developer", "publisher", "releaseyear"]` | which `metadat/` annotation directories to read |

`details` accepts `genre`, `developer`, `publisher`, `releaseyear`,
`releasemonth`, `franchise`, `maxusers`, `esrb` and `serial`. Each is one extra
request. `details = []` restores the single-request behaviour of `0.1.0`
exactly.

## The name it writes is a title, not a filename

This is the whole design, and it is the mistake `libretro-thumbnails`
documented rather than made. A DAT entry has two `name`s:

    game (
        name "14 Juillet (World) (Fr)"                       <- the title
        rom ( name "14 Juillet (World) (Fr) (Aftermarket) (Unl).gb" … )
    )                                                        ^ the filename

The second is a *file*, carrying dump-status tags that describe the dump and
not the game. This plugin writes the first and never the second. The parser
keeps them in separate attributes of separate objects so the two cannot be
confused, and a test pins it using that real Game Boy entry.

That is also why this plugin may write a name where `libretro-thumbnails`
may not: that one had only a scrubbed thumbnail *filename*, matched by
spelling. This one has a catalogue title, reached by hash.

## How a ROM is matched

In order, always inside one console's DAT:

1. **A hash** — from `--source-id` if it is 8, 32 or 40 hex characters
   (CRC-32, MD5, SHA-1; libretro's DATs carry no SHA-256), or from
   `RomRef.extra` if a host supplied one.
2. **The filename**, matched **exactly**, ignoring case and extension only.
   The DAT's own `game` title is a key too, so a library already using DAT
   names meets them.

**A hash that misses does not fall back to the filename.** You named a
specific dump; answering about a different one because its name happens to
match is answering a question nobody asked.

Two entries matching means the plugin **refuses and names both**.

    rom-hub enrich libretro-database 42 --source-id 74591cc9501af93873f9a5d3eb12da12c0723bbc

## Platforms

`libretro_database/systems.py` maps RomM platform slugs to DAT files. It is an
exact-match lookup with **no fallback**: an unmapped slug raises
**"needs mapping"** and names itself. `Tetris` is in a dozen of these files;
an unscoped lookup would find another console's.

A slug may name several DATs, tried in order, because the two sets divide by
*dump project* rather than by machine — cartridges are No-Intro's, discs are
Redump's, and `psp`, `xbox360`, `wii`, `ps3` and `3ds` have both.

Values are filenames read from the repository (`metadat/no-intro/`, 92 files;
`metadat/redump/`, 22 files; 2026-07-29), and each file's `clrmamepro` header
`name` is its own filename — which is why the same strings appear in this
repo's `hasheous` platform table. Keys are RomM slugs from the set
`libretro-thumbnails` verified against RomM 4.9.2's
`GET /api/platforms/supported`.

Arcade (`arcade`, `neogeoaes`, `neogeomvs`), DOS, ScummVM and the 8-bit micros
are **absent**: MAME's DATs are named per MAME release and keyed by short set
names, and TOSEC's live under different directories. That is a different
matching problem, not a missing row. `zxs` maps to `Sinclair - ZX Spectrum +3`,
the disk library only — a tape dump finds nothing rather than the wrong entry.

## The annotation sets

`metadat/` holds thirty directories and this plugin read two of them. The
catalogues (`no-intro/`, `redump/`) are the ones with the hashes; the rest are
the **same dumps annotated one fact at a time**, joined by CRC-32:

    game (
        comment "Tetris (World) (Rev 1)"
        genre "Puzzle"
        rom ( crc 46DF91AD )
    )

Nine of those directories carry something a library wants, and four are read by
default. They are also much smaller than the catalogues they annotate — for the
NES, 81 KB (`franchise`) to 333 KB (`developer`) against a 3.3 MB No-Intro DAT
— so four of them is roughly a third more traffic, not a doubling.

Three things about the order are deliberate:

- They are fetched **after** a match, never before. A ROM the catalogue does not
  carry costs exactly what it always did.
- Any of them failing costs that one fact and never the enrich. The name is the
  valuable half and it is already resolved by the time these are asked for;
  losing it because `metadat/franchise/` has no file for this console would be
  absurd.
- An annotation row's `comment` **never** becomes the ROM's name. The
  catalogue's `game (name ...)` is still the only naming authority here, because
  a fact about a dump is not a title.

The catalogue entry's own `region` and `serial` were already being parsed and
already being discarded; they cost nothing extra and are in the summary too.

### What cannot reach RomM

All of it lands in `summary`, because that is the only field on `PUT
/api/roms/{id}` that will take any of it. **A genre written this way is one you
can read, not one you can filter by.** RomM keeps genres, companies,
`first_release_date` and `player_count` in a `metadatum` sub-object populated by
its own configured providers, and the update endpoint has no form field that
reaches it — a part named `genres` is accepted with a 200 and discarded,
measured against a live 4.9.2. The eight `raw_*_metadata` fields are no help
either: they answer 200 and store nothing.

## What it does not set

**`libretro_id`.** RomM has that field and it already means something else:
`libretro_id_for()` in RomM's own
`backend/handler/metadata/libretro_handler.py` defines it as the SHA-1 of a
libretro **thumbnail filename**, for RomM's artwork-only libretro source. A DAT
entry is not a thumbnail and has no such id. Writing a DAT-derived value there
would collide with a field RomM maintains for another purpose, and would look
like a cross-reference while being a coincidence.

**Artwork.** These are catalogues. They contain no images.

**Anything else.** `MetadataPatch` treats an absent field as "leave RomM
alone", and this plugin resolves one thing.

## Cost, and the ceiling it runs close to

One DAT is fetched per lookup, and no caching is possible: a plugin subprocess
is started per command and dies with it, and `PluginContext` offers `config`
and `http` and no storage.

These files are large. The two biggest this plugin will ask for are
`redump/Sony - PlayStation 2.dat` at 4,060,828 bytes and
`no-intro/Nintendo - Nintendo Entertainment System.dat` at 3,307,672 — against
the Hub's 4 MiB (4,194,304-byte) per-response ceiling. The first has about 3%
of headroom. If Redump's PS2 set outgrows it, the fetch fails with the host's
own size message, and this plugin's error says that is what happened rather
than reporting a miss.

`raw.githubusercontent.com` is the only declared host, and it is the only one
needed: verified 2026-07-29, a real DAT answers `200` with **zero** redirects,
so there is no CDN hop. The GitHub *API* is deliberately not used — it returns
base64 inside JSON, roughly a third larger, and the headroom above is already
thin.

`ref` defaults to `master`, which is a moving reference: the DATs are updated
and that is the point of reading them live. Pin it if you want a corpus that
does not change under you.

## Terms and licensing, in plain language

`libretro/libretro-database` is a public repository, and the DAT files are
distributed precisely so that programs like this one can read them — it is what
RetroArch itself does to build playlists. Nothing here is a ROM: a DAT is a
list of names, sizes and checksums, so there is no game content and no
copyright being routed around.

The underlying catalogues are No-Intro's and Redump's, published openly by
those projects and republished here by libretro. `raw.githubusercontent.com`
serves no `robots.txt` at all (HTTP 404, 2026-07-29), so there is no crawl
directive to observe; this plugin nonetheless fetches at most one file per
configured set per ROM, and none on a refusal.

This plugin's own code is MIT (see `LICENSE`). It bundles no DATs — the test
fixtures are two small captured excerpts, kept so the suite can run offline.

## Notes

The plugin opens no sockets. `ctx.http` is an RPC back to the Hub, which checks
every URL against this plugin's declared allowlist before fetching.

The parser is a tokeniser rather than a line-matcher, because a value can
contain parentheses (`Tetris (World) (Rev 1)`), a `rom ( … )` can wrap, and a
game can carry several roms. A regex per line gets the easy 95% and silently
drops the rest.
