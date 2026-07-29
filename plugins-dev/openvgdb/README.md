# OpenVGDB plugin for ROM Hub

Implements the RPP v1 `metadata` capability: proposes a **curated title** and a
cover for a ROM already in your library, from the
[Open Video Game Database](https://github.com/OpenVGDB/OpenVGDB) — a freely
redistributable SQLite database of 51,742 ROMs and 53,871 releases across 43
systems.

| Capability | Source | Does |
|---|---|---|
| `metadata` | a local `openvgdb.sqlite` | proposes `name` from `RELEASES.releaseTitleName`, and `artwork_url` from `releaseCoverFront` |

**No API key**, and no account. The database is a file.

## Install

    rom-hub plugin install ./plugins-dev/openvgdb

Then get the database, once:

    curl -LO https://github.com/OpenVGDB/OpenVGDB/releases/latest/download/openvgdb.zip
    unzip openvgdb.zip

`openvgdb.zip` is 9,118,645 bytes and unpacks to a 40 MiB `openvgdb.sqlite`.
Put it anywhere and point the plugin at it:

    rom-hub enrich openvgdb 42     # after setting db_path in the plugin config

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `db_path` | `str` | `""` | path to `openvgdb.sqlite`. **Required** |
| `artwork` | `bool` | `true` | propose a cover as well as a title |
| `set_name` | `bool` | `true` | propose OpenVGDB's title |
| `region` | `str` | `""` | prefer this region's release (`USA`, `Europe`, `Japan`, `World`, …) |

## Why the database is not downloaded

This was the design question, and the answer is that a plugin **cannot**
download it. Four host facts, each independent and each fatal on its own:

1. **Size.** `openvgdb.zip` is 9,118,645 bytes. `ctx.http` refuses any response
   over `MAX_RESPONSE_BYTES`, which is 4 MiB, and it refuses it on
   `Content-Length` before a byte of body is pulled.
2. **Encoding.** `HttpResponse` carries `text`, decoded with
   `errors="replace"`. There is no byte channel. A ZIP that somehow fit under
   the cap would arrive irrecoverably mangled.
3. **Redirects.** The asset is served from
   `github.com/OpenVGDB/OpenVGDB/releases/download/v29.0/openvgdb.zip`, which
   answers `302` to `release-assets.githubusercontent.com` (checked
   2026-07-29 — note that is no longer `objects.githubusercontent.com`). The
   host fetcher runs `follow_redirects=False` deliberately, so a redirect
   cannot escape an allowlist, and `HttpResponse` exposes no headers. A plugin
   sees `302` and cannot learn where to.
4. **Nowhere to cache it.** `PluginContext` is `config` and `http`. A plugin
   subprocess is started per command and dies with it, so "download it once and
   cache it" has no storage to use — RPP v1 gives a plugin no writable path,
   by design.

**And there is nothing to query instead.** OpenVGDB publishes no API. Its
repository holds a `.gitignore` and a 28-byte `README.md`; the entire project
is one release asset, last published 2021-11-11. There is no endpoint to ask.

So the operator downloads it once. That is *announced* — a missing `db_path`
refuses with the URL, the download size and the unpacked size in the message —
and it is *bounded*, because it happens once, out of band, and never during an
enrich. Nothing here silently pulls 9 MB through a 4 MiB pipe and fails halfway.

The file is opened **read-only** through a `file:...?mode=ro` URI. That is not
decoration: `sqlite3.connect` on a plain path *creates* an empty database when
the path does not exist, so a typo in `db_path` would otherwise give a valid
connection to a zero-table file and a stream of "no match" answers that look
like real data. It also guarantees this plugin never writes to a database you
may be sharing with OpenEmu.

## How a ROM is matched

In order, and every one of them is scoped to a single system:

1. **`--source-id`**, read as a hash if it is 8, 32 or 40 hex characters
   (CRC-32, MD5, SHA-1 — OpenVGDB carries no SHA-256), otherwise as a
   cartridge/disc **serial**.
2. **A hash from `RomRef.extra`** (`sha1`, `md5`, `crc`), if a host supplied one.
3. **The filename**, matched **exactly**, ignoring case and extension. Exact,
   never fuzzy and never a prefix: `Tetris` will not pick up `Tetris 2`.

Two ROMs matching means the plugin **refuses and names both** rather than
choosing. Where one ROM has several regional releases, the region your own
filename mentions wins, then World / USA / Europe / Japan, then the lowest
release id, so the choice is stable across runs.

    rom-hub enrich openvgdb 42 --source-id 74591cc9501af93873f9a5d3eb12da12c0723bbc
    rom-hub enrich openvgdb 42 --source-id GW7D69      # a disc serial

## What it sets

- **`name`** — `RELEASES.releaseTitleName`. This is the point of the plugin.
  OpenVGDB is the only source in this directory carrying a *curated title*
  rather than a dump name: the Game Boy ROM it files as
  `Tetris (World) (Rev A).gb` is titled **`Tetris`**. That is the distinction
  `libretro-thumbnails` refused to blur when it declined to write a No-Intro
  filename into a library as a game name, and it is why this plugin may write
  one where that plugin may not.
- **`artwork_url`** — `releaseCoverFront`, or `releaseCoverBack` if the front
  is unusable. The **host** fetches it, after checking it against this
  plugin's allowlist.

**No provider ids.** OpenVGDB carries no IGDB, MobyGames or ScreenScraper
identifier. Its own `romID` and `releaseID` are row numbers in a file you
downloaded — meaningful to nothing else — and RomM has no field that means
"a row in somebody's local SQLite". An id written there would look like a
cross-reference and be a coincidence.

Everything else is left **absent**, which `MetadataPatch` defines as "leave
RomM alone".

## Artwork, and the host that is missing from the allowlist

Cover URLs in OpenVGDB point at three hosts, and this plugin declares two:

| Host | Covers | Declared |
|---|---|---|
| `gamefaqs.gamespot.com` | 31,235 | yes |
| `raw.githubusercontent.com` | 10,777 (all arcade) | yes |
| `art.gametdb.com` | 1,789 (GameCube, Wii) | **no** |

`art.gametdb.com` serves a `robots.txt` of `User-agent: *` / `Disallow: *.*`.
So those covers are never probed and never proposed, and GameCube and Wii ROMs
get a title and no cover. Working around that would mean ignoring a directive
the site went to the trouble of publishing.

`gamefaqs.gamespot.com` allows `User-agent: *` with `Allow: /` (it disallows a
list of named AI crawlers, of which the Hub is not one).
`raw.githubusercontent.com` serves no `robots.txt` at all (HTTP 404,
2026-07-29), so there is no directive to observe.

**Expect no cover from GameFAQs in practice.** Measured on 2026-07-29, from a
residential connection, with the Hub's own user agent
(`rom-hub/0.1 (+https://github.com/rommapp/romm)`):

    403   https://gamefaqs.gamespot.com/a/box/2/8/2/22282_front.jpg
    200   https://raw.githubusercontent.com/clobber/arcade-titles/master/005.png

GameFAQs sits behind Cloudflare and answers `403` with an interstitial to a
non-browser client. Robots *permits* the fetch; the site declines to serve it,
which is a different thing and not one to work around. The host is left in the
allowlist because the refusal is not universal — a different network or a
future policy may serve it — and because the probe makes being wrong free:
you get the title and no cover, rather than a failed enrich. In practice that
means **arcade ROMs get covers and most others get titles only.**

**A candidate is probed before it is proposed.** `MetadataPatch` has no way to
say "try this URL, never mind if it fails", and the host treats a failed
artwork fetch as a failed enrich — so a 403 on an image would throw away a
title that was already correct. The plugin GETs the cover first and proposes it
only on a `200`. The cost is that a hit is fetched twice, once to confirm and
once by the host to keep. The benefit is that artwork is the optional half of
this patch and behaves like it.

Redirects count as a miss, because `ctx.http` does not follow one and exposes
no `Location`: a cover that has moved is a cover this plugin cannot name.

## Platforms

`openvgdb/platforms.py` maps RomM platform slugs to `SYSTEMS.systemID`. It is
an exact-match lookup with **no fallback**: an unmapped slug raises
**"needs mapping"** and names itself.

The scoping is not a nicety. OpenVGDB is one flat `ROMs` table and `Tetris` is
not the only `Tetris` in it; an unscoped filename lookup would happily return
another console's game. The values are read from the database itself (43 rows
in `SYSTEMS`); the keys are RomM slugs from the set `libretro-thumbnails`
verified against RomM 4.9.2's `GET /api/platforms/supported`.

Most of RomM's platforms are simply **absent**, and that is the data's fault
rather than the table's: OpenVGDB was built for OpenEmu's console list and
stopped being updated in 2021. There is no DOS, no Amiga, no ZX Spectrum, no
PlayStation 2, and nothing later than the PSP.

## Terms and licensing, in plain language

OpenVGDB is published as a public GitHub release and is redistributed by a long
list of emulator frontends — OpenEmu, Provenance and others bundle or download
it — but **the repository declares no licence file and GitHub reports no
licence for it**. So "freely redistributable in practice" is an accurate
description of how the project is used and distributed, and "licensed for
redistribution" is not a claim this plugin is in a position to make for you.
This plugin bundles none of it: you download the file yourself, from the
project, and it stays on your disk.

The *contents* are two different things with two different positions. The hash
and filename tables are catalogue facts about dumps. The cover images are not
in the database at all — it stores **URLs** to third-party sites, and the
artwork at the other end is the publishers' copyright, hosted by GameFAQs and
by community projects rather than licensed to you. Fetching one to illustrate
your own library is what the database is for; republishing a library built this
way is your call to make, and not one this plugin can make for you.

This plugin's own code is MIT (see `LICENSE`). It bundles no data and no
artwork.

## Notes

The plugin opens no sockets — `ctx.http` is an RPC back to the Hub, which
checks every URL against the declared allowlist before fetching. Reading the
SQLite file is an ordinary file read; RPP v1's confinement closes network
egress and process spawn, and does not (and cannot, with seccomp alone) filter
file paths.

Every query is a full table scan: `SELECT name FROM sqlite_master WHERE
type='index'` returns nothing on release v29.0, so the database ships with no
indexes at all. Over 51,742 rows that is a few milliseconds, and adding an
index would mean writing to a file you may be sharing with another program.
