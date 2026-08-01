# itch.io plugin for ROM Hub — **NO IMPORT**

> ## ⚠ This plugin cannot import. It never will, as built.
>
> **It finds and describes; it does not fetch.** It searches free games on
> itch.io, and for a game you already have it proposes the developer's own
> title and cover art. **Every import attempt is refused, by design**, and no
> file it names is ever placed in your library.
>
> Why: itch.io hands out a download URL only in response to a **POST** carrying
> the game page's `csrf_token`, `/game/download/` is **`Disallow`ed in itch.io's
> robots.txt**, and this Hub's broker performs **GET only**. There is no public
> download route a plugin may lawfully use, so there is nothing to plan.
>
> **This is not a bug and not an unfinished feature.** If you see a refusal,
> the plugin is working. See
> [This plugin cannot import](#this-plugin-cannot-import-that-is-the-correct-answer).

Implements the RPP v1 `search`, `metadata`, `stream` and `importer` capabilities against
itch.io's **free** games.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `itch.io/games/free[/<facet>]?format=json` | free games matching a query |
| `metadata` | the game page on `<developer>.itch.io` | proposes the developer's title and cover; the **Hub** fetches the image |
| `importer` | the same game page | works out the file and platform, then **always refuses** — see below |
| `stream` | the same game page | resolves a **browser** game to the page that runs it |

## What changed in 0.4.0

| | before | after |
|---|---|---|
| `--platform` | applied here, to cells already fetched | a **browse facet**, so itch.io scopes the listing |
| games one search may walk | 144 (4 pages of 36) | **432** by default, **7,200** by config |
| capabilities | search, metadata, importer(refuses) | + **stream** |

**`--platform` used to make the search worse.** It was applied to cells
that had already come back, so a page of 36 games mostly without a Linux
build yielded two or three and the budget was spent the same. itch.io
scopes a browse itself: `/games/free/platform-linux` is 36 Linux games,
every one of them. All five facets were checked live on 2026-08-01 —
including that `platform-mac` answers 301 and the real spelling is
`platform-osx`, which is exactly the kind of detail that belongs in a
table rather than in a caller.

**`max_pages` was a 144-game ceiling** on a catalogue with hundreds of
thousands in it. The cap is now 200, which is how deep the listing itself
goes: page 200 answers a full 36-cell fragment and page 500 answers HTTP
404, both checked live.

## What `stream` does — and why it is the answer to this plugin's own complaint

This plugin cannot import and never will (see below). For the **browser**
half of itch.io's free catalogue that was always the wrong thing to want:
those games were never a file you keep, they run on the page. `stream`
resolves one to `https://<developer>.itch.io/<game>` and the host hands
that over.

**The gate is `html_embed_widget`, itch.io's own marker for a browser
build.** A page with one renders that widget wrapping a `game_frame`; a
download-only page renders none of it. Verified live on 2026-08-01 across
both shapes, and both are checked in as fixtures. The browse cell's
`web_flag` says the same thing one step earlier, which is why `search`
already reports `browser` in `extra.platforms` — but the listing is a
popularity-ordered slice that can be minutes stale and a developer can
remove a web build, so the page is read before an operator is sent to it.

**The target is the page and never the embed.** itch.io's robots.txt
carries `Disallow: /embed/` and `Disallow: /embed-upload/`, and the page's
markup hands its iframe an `html-classic.itch.zone` URL. Neither is
returned, and neither host is in the manifest — so a future version that
tried would fail the broker's gate rather than quietly work. Pulling the
inner URL out of the markup to look more direct would be reaching around
two robots directives to arrive somewhere worse than the page itch.io
means you to open.

## Install

    rom-hub plugin install ./plugins-dev/itch-io
    rom-hub search "game boy" --limit 5
    rom-hub enrich itch-io 1 --source-id izma/deadeus

## What `metadata` sets

**`name`**, from the game page's `Product` JSON-LD (falling back to the
`<h1 class="game_title">`), and **`artwork_url`**, from the page's `og:image`.
Nothing else — `MetadataPatch` reads an absent field as "leave the library
alone", and a page missing either one produces a patch without it rather than
a filled-in guess. A page with neither is a refusal, not an empty patch that
would report an enrich which changed nothing.

This is the capability that makes the plugin worth installing. It cannot fetch
the game, but the two things it can read off a game page — what the developer
called it and the cover they chose — are exactly what a library is missing for
a title it already has.

**The JSON-LD is parsed, not pattern-matched.** itch.io emits the `Product`
object's keys in a different order on every page: of the three captured in
`tests/fixtures/itch_io/`, one leads with `name`, one with `aggregateRating`
and one with `@type`. A regex for `"name":"…"` would work against whichever
page it was written for and silently rot on the rest — and a whole-page one is
worse still, because the first `"name"` in the document belongs to the
breadcrumb block and reads `Games` on all three.

**A game id is required, and there is no lookup by name.** That is not an
omission. itch.io's robots.txt `Disallow`s `/search`, and the browse listings
this plugin may read are a popularity-ordered slice of a catalogue with
hundreds of thousands of titles — hunting one specific game through them would
find the wrong one far more often than the right one, and attaching another
developer's cover to your rom is exactly the failure this project refuses
everywhere else. Run `rom-hub search itch-io "<name>"` to get the id, then
pass it with `--source-id`. A full game-page URL is accepted too.

**A cover that is not on `img.itch.zone` is dropped rather than proposed.**
The broker checks every plugin-supplied URL against the allowlist before
fetching, so an off-host cover would fail the enrich with a policy violation
that reads like a Hub fault. A patch carrying a name and no cover is a true
and more useful answer.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `filters` | `list[str]` | `[]` | extra browse facets appended to `/games/free`, e.g. `["tag-gameboy"]` or `["genre-puzzle"]` |
| `max_pages` | `int` | `12` | how many 36-game browse pages one query may walk (capped at 200, which is as deep as the listing goes) |

`filters` entries are validated as bare facets (lowercase letters, digits,
hyphens). A value containing `/` or `..` is refused rather than pasted into a
URL — it would address a different endpoint, and one of the endpoints next
door is disallowed by robots.txt.

## Why search browses instead of searching

**`itch.io/search` is `Disallow`ed for every user-agent** in
<https://itch.io/robots.txt>, and `api.itch.io/search/games` answers `401`
without an account API key. So this plugin does not use either. It asks for a
browse listing — `/games/...` is permitted by the same robots.txt — always
under the `free` scope, and matches your query against the titles, blurbs,
authors and genres it gets back.

That is honestly a weaker search than a server-side one: it can only find
games on the pages it walked. Two things make it usable:

- **Facets do the coarse filtering server-side.** `filters = ["tag-gameboy"]`
  turns the corpus from "every free game on itch.io" into "free Game Boy
  games", which is the corpus a ROM library actually wants.
- **The walk stops early.** Pages are fetched one at a time and the walk ends
  as soon as `limit` results exist, so the common query costs one request. The
  plugin adds no concurrency of its own — it has no sockets, and every request
  is an RPC the host serves serially.

Free scoping is not configurable. `/games/free` is always in the path, because
a config key able to drop it would make the scope a suggestion.

Note that itch.io's "free" filter includes **name-your-own-price** titles.
They appear in results; the importer refuses them (see below), which is where
that distinction gets enforced.

## This plugin cannot import. That is the correct answer.

**itch.io will not hand a download URL to a GET.** Verified against the live
site:

| What | Answer |
|---|---|
| `POST <game>/file/<upload_id>` with the page's `csrf_token` | `200` + a short-lived pre-signed object-store URL |
| `GET` the same endpoint, with or without the token in the query | `302` back to the game page |
| `GET itch.io/game/download/<upload_id>` | `404`, and `/game/download/` is `Disallow`ed in robots.txt |
| `GET api.itch.io/uploads/<id>/download` | `401 authentication required` |

ROM Hub's broker offers `http.get` and nothing else — a plugin has no
sockets — and the host fetches `FetchPlan` URLs with GET too. So the importer
does every piece of real routing work and then refuses. **All five outcomes
are refusals; there is no sixth branch that succeeds:**

1. **Checkout gate** → refused, naming what itch.io called it ("Name your own
   price", "$5 USD or more"). Answered before any file is named, so the
   refusal cannot double as instructions.
2. **Files listed with no download button** → refused as needing a download
   key or an account claim.
3. **No downloadable files** → refused; it is a play-in-browser title.
4. **Unmapped platform label** → refused, naming the label (see below).
5. **Free and downloadable** → refused *last*, after naming the exact upload,
   the sanitised filename and the RomM platform slug it resolved, plus why
   itch.io will not serve it.

The alternative was returning a plan whose URL answers `302` with an HTML
page. The host would then hash that page, upload it, and file it in RomM as a
ROM — an import that reports `DONE` with the wrong bytes. A visible refusal
beats that, which is the same call `archive-org` makes on `stream_only` items.

Every one of those messages names *why* it refused — robots.txt, the missing
public download route, a checkout, or a download key — so a refusal reads as
the source's terms and not as a defect in this plugin. **Do not file a refusal
as a bug.**

If the broker ever grows a POST verb, step 5 is the only place that changes,
and this plugin stops being search-only. Until then it is search-only.

## Platform mapping

itch.io publishes no platform *code*. What it publishes is the tooltip on the
icon in a game cell — literally `title="Download for Windows"` — plus a
`web_flag` span for browser builds. `itch_io/platforms.py` maps those strings:

| itch.io label | RomM slug |
|---|---|
| Windows | `win` |
| macOS, OS X | `mac` |
| Linux | `linux` |
| Android | `android` |
| Web | `browser` |

Exact match, no fallback. A label that is not in the table raises **"needs
mapping"** and names itself, because guessing files a game under the wrong
system and nothing downstream ever notices. Adding one is a one-line change.

Two related choices:

- **A search result reports a platform only when the cell names exactly
  one.** Several platforms is a choice, not a fact, so `platform` is left
  empty and every mapped slug goes in `extra.platforms` instead.
- **The importer refuses a multi-platform upload** rather than picking. Pass
  `--platform` to settle it; that override reaches the plugin and wins.

**Every one of those five slugs is catalogue-only**, and it is worth saying out
loud beside the table. `win`, `mac`, `linux`, `android` and `browser` all
describe desktop, phone or web software, and RomM's web player is EmulatorJS —
which runs console and home-computer cores and has no entry for any of the
five in its `_EJS_CORES_MAP`. So even in the world where itch.io offered a
download route this plugin could use, **nothing it fetched would be playable in
the library**; it would be a catalogue of PC games with an inert play button.

That is not an argument against the plugin. Its `search` and `metadata`
capabilities are the whole of what it usefully does, and neither is affected —
metadata proposes a title and a cover for a game you already have, and a cover
does not need a core. It *is* the reason nobody should read the import refusal
as the only thing standing between this and a working itch.io shelf. `rom-hub
platforms` puts all five in the catalogue-only group.

## Legal position

This plugin is scoped to itch.io's own **free** browse listing, and reads only
pages itch.io's robots.txt permits. Everything it surfaces is a game whose
developer chose to publish it for free on itch.io — itch.io is a storefront
where the rights holder is the uploader, so free-listed titles are
distributed with the rights holder's consent by construction.

It does **not**:

- touch `/search`, `/game/download/` or any other `Disallow`ed path;
- attempt paid titles, name-your-own-price titles, or anything behind a
  download key — all three are refused, by design, rather than worked around;
- authenticate as anybody, or use an API key.

The Hub sends a `rom-hub/0.1` User-Agent and one request at a time. There is
no scraping of the storefront at large: a query walks at most `max_pages`
browse pages.

## Network

Declared allowlist: `itch.io`, `*.itch.io`. The apex serves the browse
listings; every game page lives on a per-developer subdomain
(`csbrannan.itch.io`). No CDN host is declared because the plugin never gets
far enough to be handed a CDN URL. The Hub checks every URL — including any
in a `FetchPlan` — against this list before opening a socket, so widening the
plugin's reach means editing `manifest.toml` and reinstalling, not editing
config.
