# Aminet plugin for ROM Hub

Implements the RPP v1 `search` and `importer` capabilities against
`https://aminet.net` — the Amiga world's software archive, 85,453 packages
deep, of which the `game/` tree holds **7,670**, and its fourteen
game-holding shelves **5,737**.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `/search?query=…&page=N` | server-side search across every package; the game scope is applied client-side |
| `search` (browse) | `/game/<shelf>?page=N` | one shelf's own listing, scoped by the server, pageable |
| `importer` | `/<path>.readme` | reads the package's own header, then plans the archive |

## What changed in 0.2.0, and why it matters

**`dir=` was never a filter.** `?query=tetris`, `&dir=game`, `&dir=demo`
and `&dir=zzz` return the identical 134 packages — verified live
2026-08-01, four spellings, one answer. Aminet's search form emits exactly
one field (`<input name="query">`); there is no directory parameter, and
`dir` was an invented one that HTTP 200 made look like it worked. This
README used to claim a server-side game scope on the strength of it. It
never happened: every `comm/dlg` row arrived like any other and was
dropped client-side.

**So an empty query did not browse — it failed.** `/search?dir=game` with
no `query` returns Aminet's search *form*: no count line, no result table.
The parser refuses that document (correctly — this host answers a missing
path with HTTP 200 and a themed error body), so `rom-hub search aminet ""`
raised rather than listing anything.

Both are fixed by using the endpoint that does scope: **a shelf listing**.
`https://aminet.net/game/think` is a real page carrying the same result
table, the same `Found 910 matching packages` count line and its own
`?page=N`. An empty query now walks the fourteen game shelves through it.

| | before | after |
|---|---|---|
| packages a browse can reach | **0** (it raised) | **5,737** — every package on the fourteen game shelves |
| pages one search may walk | 2 (100 rows, unscoped, then filtered) | 4 by default, 20 by config |
| scope of a searched page | claimed server-side; was client-side | stated as client-side, and the count line ends the walk exactly |

## Why this material is legitimate

**Aminet's admission rule is the licence check.** Its uploading
instructions open with "This site is intended for the distribution of any
type of freely distributable software" and then list what is refused —
"Unlicensed copies of commercial software" and "Software with a license in
conflict with Aminet's nature" first among them. Freely distributable is not
a description of the archive, it is the condition of being in it, enforced by
moderators since 1992.

So what this plugin reaches is public-domain, freeware, shareware and
open-source Amiga software that its authors uploaded themselves or licensed
for redistribution. Individual packages carry their own terms in their
`.readme` — that file is fetched on every import and its header is what the
platform is read from, so it is one command away when you want it.

`https://aminet.net/robots.txt` returns Aminet's themed **404 page**: the site
publishes no crawl directives at all. Verified 2026-07-29. (That 404-as-200
behaviour is itself worth knowing about — see below.)

The one shelf worth naming explicitly is `game/demo`, which Aminet describes
as "Demos of commercial games". That sounds like the wrong side of the line
and is not: a publisher's playable demo was published *for* free
distribution, which is what made it admissible here. It is a real,
freely-redistributable game — just a short one.

## Aminet is not an Amiga archive

It is the archive for the whole family of systems that grew out of the Amiga,
and this is the single most important thing about the plugin.

One `game/think` directory holds `abrick.lha` (AmigaOS 4 on PowerPC),
`abrick-ix48.lha`, `abandoned_bricks-mos.lha` (MorphOS) and
`alleytris_68k.lha` (AmigaOS on 68k). **Four different computers, same
shelf, near-identical names.** Live counts over one search page on
2026-07-29: `m68k-amigaos` 98, `generic` 28, `ppc-amigaos` 16,
`ppc-morphos` 15, `i386-aros` 9, `ppc-warpup` 3, `ppc-powerup` 3,
`i386-amithlon` 3.

RomM has `amiga`, and `amiga` means the Commodore Amiga. `platforms.py`
therefore maps exactly three architectures and refuses the rest **by name**:

| Architecture | RomM | Why |
|---|---|---|
| `m68k-amigaos` | `amiga` | the Commodore Amiga |
| `ppc-warpup` | `amiga` | PowerPC accelerator card *in* a Commodore Amiga, under AmigaOS 3.x |
| `ppc-powerup` | `amiga` | same |
| `ppc-amigaos` | — | AmigaOS 4 on AmigaOne/Sam hardware |
| `ppc-morphos` | — | MorphOS on Pegasos or PowerPC Macs |
| `i386-aros`, `x86_64-aros`, … | — | AROS on PC hardware |
| `i386-amithlon` | — | Amithlon on PC hardware |
| `generic` | — | no machine at all: source, data, documents |

Mapping WarpUP and PowerUP to `amiga` is the one judgement call, and it goes
that way because those binaries physically require an Amiga with an
accelerator card in it — filing them anywhere else would be the misfiling.
Everything else refuses with a sentence naming the actual machine, so the
answer reads "MorphOS is not a Commodore Amiga", not "unknown".

Unmapped packages still **appear in search**, with `platform` unset. Hiding
them would mean someone searching for a game they can see on Aminet's own
site getting nothing back and no reason. `--platform` overrides if you keep a
shelf for MorphOS.

## The `game/` tree is 18 shelves and four hold no games

Measured 2026-08-01 from each shelf's own count line:

| shelf | packages | shelf | packages |
|---|---:|---|---:|
| `game/think` | 910 | `game/role` | 507 |
| `game/misc` | 834 | `game/wb` | 427 |
| `game/shoot` | 756 | `game/actio` | 406 |
| `game/demo` | 558 | `game/2play` | 304 |
| `game/board` | 291 | `game/jump` | 255 |
| `game/gag` | 178 | `game/text` | 123 |
| `game/strat` | 113 | `game/race` | 75 |

**5,737 across the fourteen.** The four excluded ones — `game/data` 896,
`game/hint` 502, `game/patch` 412, `game/edit` 123 — bring the tree
to 7,670.

`game/data` (data files), `game/edit` (level editors), `game/hint`
(walkthrough documents) and `game/patch` (patches) are excluded from search
by default and **always** refuse to import. Aminet's search does not
distinguish them and a library entry made from a walkthrough will never
start. `include_support = true` makes them visible; it does not make them
importable.

The other fourteen — `2play actio board demo gag jump misc race role shoot
strat text think wb` — are games, with Aminet's own descriptions carried in
`extra.shelf` on every result.

## Search

    rom-hub search aminet tetris                    # /search, all 85,453
    rom-hub search aminet "" --limit 40             # browse the shelves
    rom-hub search aminet "" --platform amiga       # browse, 68k only
    rom-hub search aminet quake --platform amiga

**A query goes to the server.** One request searches all 85,453 packages
and a match five thousand entries deep comes back in it. The *game* scope
is then applied here, because Aminet has no directory parameter — so a
page of 50 that is mostly `util/` yields a handful, and the honest
mitigation is to page further rather than to claim a scope that does not
exist.

**An empty query browses instead.** It walks the fourteen game shelves
through their own listings, which *are* server-scoped, in order, paging
each one until `limit` or `max_pages` runs out. `shelves` picks which ones
and in what order:

    shelves = ["game/role", "game/text"]

`--platform` is applied client-side to the architecture, because Aminet
has no architecture filter of any kind — but a RomM platform this source
has nothing for returns an empty list **without a request**.

Aminet answers 50 rows a page and the page size is not negotiable
(`pagesize`, `limit` and `rows` were all tried live and all ignored), so
`max_pages` bounds the walk. The walk also stops on the `Found N matching
packages` count line, so it never asks for a page past the end.

## Importing

    rom-hub import aminet game/think/alleytris_68k.lha

The importer fetches the package's `.readme`, which does three jobs at once:

1. **Proof it still exists.** Aminet answers a missing path with HTTP **200**
   and a themed error page — its own `/robots.txt` is one — so a status code
   proves nothing. A readable header does.
2. **The architecture**, from `Architecture:`. The search row carries an
   *icon*, which is a rendering; the readme is the uploader's own statement.
3. **The shelf**, from `Type:`, checked against the path. A hand-assembled
   `source_id` pointing a game path at something else disagrees here.

Everything lands in the `Aminet` RomM collection by default.

## Install

    rom-hub plugin install https://github.com/BlizzHacker/rom-hub-aminet --ref v0.2.0

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `max_pages` | `int` | `4` | Pages to walk, 50 rows each, across every shelf one call touches (capped at 20) |
| `shelves` | `list[str]` | `[]` | Which game shelves a browse walks, in order. Empty means all fourteen. An unknown shelf is refused by name before any request |
| `include_support` | `bool` | `false` | List `game/data`, `edit`, `hint`, `patch` (they still refuse to import) |
| `collection` | `str` | `Aminet` | RomM collection imports are filed under |

## Notes for the next person

- **The light and dark result rows are not the same markup.**
  `<tr class="lightrow pkg_row">` against
  `<tr class="darkrow pkg_row" bgcolor="#e0e0e0">`. A regex anchored on
  `pkg_row">` finds exactly half the results, which looks like a thin source
  rather than a broken parser. Rows are found by their `name_col` cell.
- **HTTP 200 is not evidence on this host.** `/robots.txt` is a 200 whose
  body is a "Directory '/' not found" page. Both parsers here refuse a
  document that is not the shape they expect, rather than reading zero rows
  out of it.
- **The readme is a stem swap, not an append.**
  `game/think/abrick.lha` → `game/think/abrick.readme`;
  `abrick.lha.readme` does not exist.
- **`Architecture:` carries an OS floor** — `ppc-amigaos >= 4.0.0` — and the
  qualifier is about the OS, not the machine. It is split off before lookup,
  which is also what makes the readme's value and the icon's agree.
- **The size column is Aminet's rounded `2.0M`.** Carried as
  `extra.size_text`, never as `size_bytes`.
- **A shelf listing and a search page are the same document.** Same table,
  same `Found N matching packages` line, same `?page=N`. One parser serves
  both, which is why the browse cost no second parser.
- **There is no `metadata` capability and there should not be.** The
  `.readme` carries `Short:`, `Author:`, `Uploader:` and `Version:`, and
  none of those is something RomM 4.9.2 will store: its update endpoint
  takes a `name`, provider ids and a cover, Aminet publishes no title
  distinct from the filename, and its package pages carry no artwork at
  all — checked live, every `<img>` on one is the site's own furniture. An
  `enrich` here would have to invent a title or return nothing, and an
  empty capability is worse than an absent one.
- **The mirrors are not in the allowlist.** `m68k.aminet.net`,
  `mos.aminet.net` and the rest are hostnames an operator picks on the
  website, not redirect targets. Downloads from `aminet.net` answer 200
  directly with zero redirects.
