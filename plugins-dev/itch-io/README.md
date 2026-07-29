# itch.io plugin for ROM Hub — **SEARCH-ONLY**

> ## ⚠ This plugin cannot import. It never will, as built.
>
> **It is a search plugin.** It finds free games on itch.io and shows you what
> and where they are. **Every import attempt is refused, by design**, and no
> file it names is ever placed in your RomM library.
>
> Why: itch.io hands out a download URL only in response to a **POST** carrying
> the game page's `csrf_token`, `/game/download/` is **`Disallow`ed in itch.io's
> robots.txt**, and this Hub's broker performs **GET only**. There is no public
> download route a plugin may lawfully use, so there is nothing to plan.
>
> **This is not a bug and not an unfinished feature.** If you see a refusal,
> the plugin is working. See
> [This plugin cannot import](#this-plugin-cannot-import-that-is-the-correct-answer).

Implements the RPP v1 `search` and `importer` capabilities against itch.io's
**free** games.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `itch.io/games/free[/<facet>]?format=json` | free games matching a query — **this is what the plugin is for** |
| `importer` | the game page on `<developer>.itch.io` | works out the file and platform, then **always refuses** — see below |

## Install

    rom-hub plugin install ./plugins-dev/itch-io
    rom-hub search "game boy" --limit 5

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `filters` | `list[str]` | `[]` | extra browse facets appended to `/games/free`, e.g. `["tag-gameboy"]` or `["genre-puzzle"]` |
| `max_pages` | `int` | `4` | how many 36-game browse pages one query may walk |

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
