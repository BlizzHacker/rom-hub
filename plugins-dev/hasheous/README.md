# Hasheous plugin for ROM Hub

Implements the RPP v1 `metadata` capability: turns a ROM's **hash** into a
game identity, using [hasheous.org](https://hasheous.org) — a free, key-free
public service that matches the signature DATs (No-Intro, Redump, TOSEC, MAME,
WHDLoad, FBNeo, RetroAchievements) to metadata providers.

| Capability | Endpoint | Does |
|---|---|---|
| `metadata` | `hasheous.org/api/v1/Lookup/ByHash/{md5,sha1,sha256,crc}/<hex>` | proposes a name, a summary from the signature row, `hasheous_id`, and every provider id hasheous has mapped |

## Install

    rom-hub plugin install ./plugins-dev/hasheous
    rom-hub enrich hasheous 1 --source-id md5:5d7550788a4d1b47ad81fbbbf5c615a9

**No API key.** The lookup endpoints are unauthenticated, and this plugin sends
nothing but a GET.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `verify_platform` | `bool` | `true` | refuse an answer about a console other than the one RomM has this ROM filed under |
| `allow_crc32` | `bool` | `false` | permit a CRC-32 lookup (see below — 32 bits collide) |
| `set_name` | `bool` | `true` | propose hasheous's game name |
| `summary` | `bool` | `true` | propose the signature row's publisher, year, region, language and corpus as RomM's `summary` |
| `raw_metadata` | `bool` | `false` | also send the whole answer as `raw_hasheous_metadata` (see below — RomM 4.9.2 discards it) |
| `provider_ids` | `list[str]` | `["igdb", "tgdb", "ra"]` | which sources' ids to offer (see below) |

`cross_provider_ids` is gone as of `0.2.0`. It was one boolean over two ids and
it defaulted to off; `provider_ids` replaces it with a per-source list that
defaults to all of them. Why that flipped is below.

## Where the hash comes from

Hasheous is keyed by hash and **only** by hash. Its GET surface has four
routes, one per algorithm, and no name search at all. (There *is* a name
search — `hasheous_search_games` on the hosted MCP endpoint — but that is a
`POST /api/v1/Mcp` and `ctx.http` offers `get()` and nothing else. A plugin
has no socket; its one route outward is the host's brokered GET.)

A plugin cannot hash the ROM either: it has no filesystem mount, and `RomRef`
carries a name, a filename, a platform and a size. So the hash is supplied:

    rom-hub enrich hasheous 42 --source-id md5:5d7550788a4d1b47ad81fbbbf5c615a9
    rom-hub enrich hasheous 42 --source-id sha1:274ed5c2ea2ddc855f67d4c4e61c9d9b7eb68403
    rom-hub enrich hasheous 42 --source-id 274ed5c2ea2ddc855f67d4c4e61c9d9b7eb68403

A bare digest is unambiguous: 8, 32, 40 and 64 hex characters are CRC-32, MD5,
SHA-1 and SHA-256 respectively, and no two collide. The plugin also reads
`md5` / `sha1` / `sha256` / `crc` out of `RomRef.extra` if a host ever puts
them there; nothing in RPP v1 obliges one to, so that is a door left open
rather than a dependency.

**A ROM with no hash is refused, never guessed at.** Searching for the title
and taking the top hit would write *a* game's identity rather than *this*
game's, and the library would afterwards say nothing about it.

## Why CRC-32 is off by default

CRC-32 is 32 bits. Across the millions of dumps hasheous indexes, two files
sharing one is an ordinary event, not a theoretical one — and the failure is
silent. Hasheous answers confidently about a different game, and the wrong
title and the wrong provider ids land in your library looking exactly like
right ones.

`allow_crc32 = true` turns it on. Even then the platform cross-check has to
agree, which is what catches the collision: a CRC that matches a Mega Drive
dump for a ROM filed under `gb` is refused, with both consoles named.

## Platforms

`hasheous/platforms.py` maps RomM platform slugs to the console names hasheous
answers with. It is an exact-match lookup with **no fallback**: a slug that is
not in the table raises **"needs mapping"** and names itself.

The check compares against `signature.game.system` first and only falls back to
`platform.name`. That is not arbitrary — `signature.game.system` is set by
hasheous's own signature parser straight from the DAT header
(`NoIntrosParser.cs`: `SystemName = noIntrosObject.Name`, then
`gameObject.System = SystemName`), so it is guaranteed to be in the DAT
vocabulary the table is built from. `platform.name` is a curated database
object that hasheous only seeds from the DAT header when it has to create one
(`HashLookup2.cs`: `Name = discoveredSignature.Game.System`), and an
administrator can rename it.

The table's values are real DAT header names, read from
`libretro/libretro-database` — `metadat/no-intro/` (92 files) and
`metadat/redump/` (22 files) on 2026-07-29, where each file's header `name`
is its filename. Comparison is normalised: everything that is not a letter or
a digit is stripped and the rest lowercased, so TOSEC's `Nintendo Game Boy`
and No-Intro's `Nintendo - Game Boy` agree without this module having to
guess at a second vocabulary.

Arcade (`arcade`, `neogeoaes`, `neogeomvs`), DOS, ScummVM, the 8-bit micros
that only TOSEC carries, and a handful of RomM slugs with no signature source
are **deliberately absent**. A wrong entry here fails silently in both
directions; an absent one says so. Set `verify_platform = false` to skip the
check entirely — that is the supported override, and it is an operator's
decision rather than this plugin's guess.

## What it sets

- **`name`** — hasheous's game name. This is the parsed *title*, not a
  filename: the No-Intro parser splits the DAT entry at the first `(` and
  keeps what is in front, so `Altered Beast (USA, Europe)` is stored as
  `Altered Beast`. Set `set_name = false` to keep your own naming.
- **`summary`** — the signature row's own facts, which every answer carried and
  this plugin used to read past on its way to `id` and `name`. For the Altered
  Beast fixture: `Published by Sega. Released 1988. Region: USA, Europe.
  Language: English. Matched against the No-Intro signature (verified dump).`

  That last sentence is the one no other plugin here can write — the difference
  between "a file called Altered Beast" and "the No-Intro verified dump of
  Altered Beast". `NoIntros` is spelled `No-Intro`, because hasheous's internal
  spelling reads as a typo; the languages are hasheous's own expansion of the
  DAT's `En` tags rather than a table this plugin would have to maintain.
- **`hasheous_id`** — the identity object's id. Always, and deliberately not in
  `provider_ids`: it is not another provider's reference, it is the identity of
  the thing that answered, and a patch that cannot be traced back to its lookup
  is worse than one that can.
- **`igdb_id`, `tgdb_id`, `ra_id`** — for the sources named in `provider_ids`,
  and only for entries hasheous itself marks `Mapped`. A `NotMapped` row is a
  search hasheous has *scheduled*, not an answer it has, and its empty id would
  look identical to a resolved one once written.
- **`raw_hasheous_metadata`** — only with `raw_metadata = true`. The whole
  answer, dropped rather than truncated when it exceeds RPP v1's 256 KiB
  per-field ceiling (`signatures` is shed first; it is the bulky part and not
  where the identity lives).

Everything else is left **absent**, which `MetadataPatch` defines as "leave
RomM alone". A lookup that maps to IGDB but not to TheGamesDB writes `igdb_id`
and does not blank `tgdb_id`.

Hasheous also proxies GiantBomb, Steam, GOG, the Epic Game Store, Wikipedia and
SteamGridDB. RomM's update endpoint has no field for any of those, so those
mappings ride along in the raw blob and nowhere else.

### Why every id is offered now, and who decides whether it lands

RomM does not *store* some of those ids — it **acts** on them. RomM 4.9.2's
`update_rom` re-fetches from the provider whenever a provider id changes
(`backend/endpoints/roms/__init__.py`, the "Fetch metadata from external
sources" block covers flashpoint, launchbox, ra, moby, ss and igdb), and that
fetch needs that provider's key.

Writing `ra_id` to a RomM with no RetroAchievements key does not degrade. It
raises `TypeError: Invalid variable type: value should be str, int or float,
got None` out of `yarl`, when RomM's auth middleware appends a `None` API key
to the query string, and the whole `PUT /api/roms/{id}` answers **500**.

That measurement is why `cross_provider_ids` existed and defaulted to off. It
was also, on its own, not enough to justify the default — because measuring the
other ten one at a time against the same keyless RomM gives a different answer:

| id | RomM with no credentials for it |
|---|---|
| `igdb_id`, `sgdb_id`, `moby_id`, `ss_id`, `launchbox_id`, `hasheous_id`, `tgdb_id`, `flashpoint_id`, `hltb_id`, `libretro_id` | **200**, stored, read back |
| `ra_id` | **500**, nothing stored |

Measured 2026-08-01. So the danger was one field, and the old default withheld
two thirds of what this plugin exists to produce.

**The Hub asks your server now.** `GET /api/heartbeat` reports one
`METADATA_SOURCES` flag per provider RomM holds credentials for — it is public,
so it works before authentication — and the host reads it before every write.
An id the server will not take is dropped, the rest of the patch is written
anyway, and the reason comes back in the command's output:

    ra_id (RomM has no credentials for this provider (RA_API_ENABLED is false
    in GET /api/heartbeat) and re-fetches from it whenever ra_id changes,
    which answers HTTP 500 rather than degrading. The id was withheld so the
    rest of the patch could be written; configure that provider in RomM and
    enrich again to keep it)

The same mechanism reports the good news. When your RomM *does* hold IGDB
credentials, writing `igdb_id` makes RomM go and fetch that game's genre,
summary, screenshots, release date and companies by itself — which is the
single most valuable thing this plugin can produce, and far more than any
`summary` it could compose. The output says so.

`provider_ids` remains, per source rather than one switch, because the reasons
differ per source: `ra_id` is what an achievements client will act on later, and
the other two are cross-references nothing acts on. An operator may reasonably
want one and not the other.

### What cannot reach RomM

**The raw blob does not arrive**, and that is why `raw_metadata` defaults to
false since `0.2.0`. Measured against a live RomM 4.9.2 on 2026-08-01: a marker
written into `raw_hasheous_metadata` answers 200 and appears nowhere in the rom
record afterwards. Repeated with `hasheous_id` written *and changed* in the same
request — which rules out the provider-id gate RomM applies to seven of its
eight raw fields — the id lands and the blob does not. The same is true of
`raw_manual_metadata`, which has no gate at all.

So the config key stays (a different RomM version, or a backend with a home for
it, would make the blob worth sending again) and the default does not, because a
plugin whose main output is discarded looks from the outside exactly like a
plugin that worked.

Hasheous also proxies GiantBomb, Steam, GOG, the Epic Game Store, Wikipedia and
SteamGridDB. RomM's update endpoint has no field for any of those and the raw
blob is a void, so **those mappings do not reach your library at all.** They are
one `GET https://hasheous.org/api/v1/Lookup/ByHash/md5/<hex>` away if you want
them; this plugin will not pretend to have delivered them.

**No artwork.** Hasheous carries logos and screenshots as attributes and
proxies IGDB and TheGamesDB images, but proxied cover art is those providers'
art served through a third party, and this plugin does not put it in your
library under hasheous's name. `libretro-thumbnails` is the artwork plugin.

## Terms and licensing, in plain language

Hasheous is free to use and says so on its own front page: the project's README
states "Is completely free to use", and its MCP documentation says the hosted
endpoint is "intentionally public for free database lookups". The lookup
endpoints take no key and this plugin sends no credential.

What hasheous distributes is **matching data, not ROMs**: hash → name → other
databases' identifiers. There is no game content in an answer, so nothing
here routes around anybody's copyright. The DAT projects it ingests (No-Intro,
Redump, TOSEC, MAME) publish their catalogues openly; the metadata ids it maps
to belong to IGDB, TheGamesDB, RetroAchievements and the rest, and hasheous
holds the keys for those so you do not have to.

`hasheous.org` publishes a `robots.txt` that allows `User-agent: *` and
disallows some named AI crawlers. This plugin is neither: it is a ROM manager
issuing one GET for one file the operator asked about, under the host's own
`rom-hub/…` user agent, and it makes at most one request per hash offered.

This plugin's own code is MIT (see `LICENSE`). It bundles no data.

## Notes

The plugin opens no sockets. `ctx.http` is an RPC back to the Hub, which checks
every URL against this plugin's declared allowlist (`hasheous.org`, and nothing
else) before fetching anything.

The API was derived from the project's own open source rather than by reading
the website: `gaseous-project/hasheous` (the server, and its
`LookupController.cs` and `HashLookup2.cs`), `sargunv/hasheous-cli` (whose
checked-in OpenAPI `schema.d.ts` is the response contract used here), and
`gaseous-project/gaseous-signature-parser` (which is where `game.system` comes
from). The test fixture is built from those published shapes and is labelled as
such in `tests/test_hasheous.py`.
