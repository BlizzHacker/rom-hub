# libretro Thumbnails plugin for ROM Hub

Implements the RPP v1 `metadata` capability: box art for a ROM that is already
in your RomM library, from libretro's public thumbnail repositories.

| Capability | Endpoint | Does |
|---|---|---|
| `metadata` | `thumbnails.libretro.com/<system>/Named_Boxarts/<name>.png` | proposes an `artwork_url`; the **Hub** fetches it |

## Install

    rom-hub plugin install ./plugins-dev/libretro-thumbnails
    rom-hub enrich libretro-thumbnails 1

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `art_kind` | `str` | `"boxart"` | which libretro set to use: `boxart`, `title`, `snap` or `logo` |
| `index_fallback` | `bool` | `true` | when no spelling matches, read the system's directory listing and match on the title alone |

No credentials. The service is unauthenticated and this plugin sends nothing
but a GET.

## What it sets, and what it deliberately does not

It sets **`artwork_url`** and nothing else.

- **Not `name`.** libretro's filenames are No-Intro DAT strings, not curated
  titles. `MetadataPatch` treats an absent field as "leave RomM alone" exactly
  so a plugin can do one thing, and overwriting your naming with another
  project's spelling is not an improvement you asked for.
- **Not `libretro_id`.** There is no such id. These repositories are keyed by
  *name*; the service has no numeric or opaque identifier anywhere in it. The
  only value on offer is the thumbnail's filename, which RomM's provider-id
  validator rejects anyway (a provider id may not contain spaces or
  parentheses). An invented id is worse than an absent one.

## The hard part: the filename

libretro serves `Named_Boxarts/<name>.png`, where `<name>` is the playlist
label RetroArch would use — a No-Intro DAT name run through one substitution
pass. This plugin reproduces that pass and then tries a short ladder of
spellings, stopping at the first that answers `200`.

**Character substitution.** RetroArch replaces `& * / : ` " < > ? \ |` with
`_` and touches nothing else — one line of
`gfx_thumbnail_fill_content_img()` in `gfx/gfx_thumbnail.c`. The
libretro-thumbnails repository agrees from the other end: its README's check
for badly-named files is a `find` over that same character class. Checked
against 30,903 filenames captured from the SNES, NES, PlayStation and DOS
listings:
ten of those eleven characters appear zero times, while `'` `!` `,` `.` `[` `]`
`+` `$` `%` `^` `~` `;` `#` `=` all appear freely. So `Pocky & Rocky (USA)` is
served as `Pocky _ Rocky (USA).png`.

**Article placement.** No-Intro moves a leading article to the end of the
title, in front of any ` - ` subtitle: `Legend of Zelda, The - A Link to the
Past`. Both spellings are generated, in both directions — 912 of those 30,903
names end in `, The` and 27 begin with `The`.

**RetroArch's own shortening.** When the full name misses, RetroArch retries
with everything from the first ` (` dropped. DOS really does carry both
`Prince of Persia (1990).png` and `Prince of Persia.png`.

**The directory listing, last.** If no spelling matches, the plugin reads the
system's listing once and matches on the title with tags, punctuation, case and
articles ignored. That is how a library that says `Prince of Persia 2 - The
Shadow and The Flame` finds the file that says `... (1993)`. It is an
*equality* test on the normalised title, never a prefix test: `Sonic the
Hedgehog` will not pick up `Sonic the Hedgehog 2`. Where one title has several
releases, the region your own name mentions wins, then USA / World / Europe /
Japan, and beta/proto/hack dumps sort last.

Set `index_fallback = false` to skip it. It costs one directory listing, which
is 0.6 MB for the Mega Drive and 4 MB for the NES — and the Hub caps a single
`ctx.http` response at 4 MiB, so on the very largest systems it can fail on
size. That failure is reported, not swallowed.

**A hit is fetched twice.** The plugin GETs the candidate to confirm it exists,
then the Hub GETs it again to store it. `MetadataPatch` has no way to say "try
this URL, never mind if it 404s" — an unverified URL turns a missing thumbnail
into a raw HTTP error from the Hub's fetcher. Probing first is what makes a
miss say *which spellings were tried*. The cost is bounded at 8 candidates.

## Platforms

`libretro_thumbnails/systems.py` maps RomM platform slugs to libretro system
directories. It is an exact-match lookup with **no fallback**: a slug that is
not in the table raises **"needs mapping"** and names itself. Guessing would
not fail — it would succeed, with another console's box art, and nothing about
your library afterwards would say so.

Both sides of that table are captured reality, and a test pins each: every
value is a directory `thumbnails.libretro.com` actually serves (127 of them,
read 2026-07-29), and every key is a slug from RomM 4.9.2's own
`GET /api/platforms/supported` (458 of them).

`c128`, `new-nintendo-3ds`, `msx-turbo` and `msx2plus` are deliberately absent:
each is a real machine whose software libretro does not carry separately.
`arcade` maps to `MAME`; if your library follows FBNeo's naming instead, name
the file yourself with `--source-id`.

## Naming a file yourself

    rom-hub enrich libretro-thumbnails 42 --source-id "Star Fox (USA)"

`--source-id` is the exact libretro filename, with or without `.png`. It is
still probed, so a typo comes back as a message about the typo rather than as
an HTTP error from the Hub. When it is given, the directory-listing fallback is
skipped — you said which file you wanted.

## Terms and licensing, in plain language

`thumbnails.libretro.com` is a public, unauthenticated static file server run
by the libretro project, and it exists to be read by RetroArch installations
doing exactly what this plugin does. It served no `robots.txt` at all when this
plugin was written (2026-07-29, HTTP 404), so there is no crawl directive to
observe; the plugin nonetheless issues at most nine requests per ROM and none
on a refusal.

The images come from the libretro-thumbnails project, which distributes them
publicly for RetroArch to use. libretro does not, however, claim to own them:
its own credits page lists MobyGames, Fandom and volunteer submissions as
sources, and says the game art originates with each game's developers and
publishers. So these are freely *available* covers, not public-domain ones.
The Hub stores what you point it at. Using them to illustrate your own library
is the use the service exists for; republishing a library built this way is
your call to make, and not one this plugin can make for you.

This plugin's own code is MIT (see `LICENSE`). It bundles no artwork.

## Notes

The plugin opens no sockets. `ctx.http` is an RPC back to the Hub, which checks
every URL against this plugin's declared allowlist (`thumbnails.libretro.com`,
and nothing else) before fetching anything — including the `artwork_url` this
plugin returns, which the **Hub**, not the plugin, fetches. There is no CDN hop
to declare: a real boxart URL answers `200` directly with zero redirects.
