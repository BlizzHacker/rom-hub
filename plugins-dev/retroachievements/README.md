# RetroAchievements plugin for ROM Hub

Implements the RPP v1 `metadata` capability: identifies a ROM by its hash on
[RetroAchievements](https://retroachievements.org) and writes back the game's
`ra_id` and title.

| Capability | Endpoint | Does |
|---|---|---|
| `metadata` | `API_GetGameList.php?i=<console>&h=1` | matches your hash against RA's, exactly |

## Install

    rom-hub plugin install ./plugins-dev/retroachievements
    rom-hub enrich retroachievements 42 --source-id <md5>

## ⚠ The API key is stored in plain text

**Read this before you paste a key.**

RPP v1 reserves a `secret` config type for credentials. **This host does not
implement it** — `rom_hub/manifest.py` rejects any field declaring
`type = "secret"` with *"reserved in RPP v1 but not implemented in Phase 1"*.
So `api_key` is declared as a plain `str`, and the Hub stores it **in the clear**
in its plugin config on disk, alongside every other setting. Anything that can
read that file can read your key.

That is stated here rather than worked around, because an operator who believes
a credential is protected treats it differently from one who knows it is not.

What follows from it:

- A RetroAchievements web API key is **per-account, read-only, and resettable**.
  It is not your password and it cannot spend anything. Get it from your RA
  profile under **Settings → Keys**, where you can also reset it at any time.
- Treat the one you put here as disposable. Reset it if the machine changes
  hands, and do not reuse it anywhere that matters.
- A test in this repo (`test_the_secret_config_type_really_is_rejected_by_this_host`)
  pins the claim. If a later phase implements `secret`, that test fails and this
  warning stops being true at the same moment.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `api_key` | `str` | `""` | your RA web API key — **stored in the clear**, see above |
| `username` | `str` | `""` | your RA username, sent as `z`. Optional: RA's docs mark only `y` required, but RA's own client sends both |
| `set_name` | `bool` | `true` | write the matched game's title into RomM's `name` |
| `only_with_achievements` | `bool` | `true` | ask RA for `f=1`, the smaller list |

With no `api_key` the plugin refuses **before making any request**, with a
message naming the config key and where to get a value for it — not a 401, not
a `KeyError`.

## What it sets

- **`ra_id`** — the RetroAchievements game id. Coerced to an `int`: this
  endpoint returns `ID` as a JSON *string* (`"4247"`), which RA's own client
  corrects for, and `"4247"` is not the same value as `4247` in a column RomM
  parses as an integer.
- **`name`** — the matched game's RA title, unless `set_name = false`. Safe to
  write because the match is by hash, which is the strongest identification
  available; turn it off if you curate names yourself.

## What it does not set, and why

**No `raw_*_metadata`.** RPP v1 has exactly eight of those fields, belonging to
IGDB, ScreenScraper, LaunchBox, Hasheous, Flashpoint, HowLongToBeat, MobyGames
and manuals. **None of them is RetroAchievements.** Putting RA's payload into
`raw_hasheous_metadata` because it is the nearest neighbour would be a lie in
the database about where the data came from, so nothing is written. If RPP
gains a `raw_ra_metadata`, this becomes a two-line change.

**No artwork.** RA serves box art, but so does the `libretro-thumbnails` plugin,
which is what it is for. Adding RA's media host to this plugin's allowlist for a
field another plugin covers would widen the allowlist for nothing.

## Hashes: the part that surprises people

RetroAchievements does **not** identify games by "the md5 of the ROM file". It
identifies them by whatever `rc_hash_from_buffer()` in
[rcheevos](https://github.com/RetroAchievements/rcheevos) computes for that
console, and only some consoles hash the file as it sits on disk.

**Consoles where RomM's `md5_hash` *is* the RA hash** — Mega Drive, Game Boy /
Color / Advance, Master System, Game Gear, 32X, SG-1000, Atari 2600, Jaguar,
Virtual Boy, MSX, Intellivision, ColecoVision, Vectrex, WonderSwan, Neo Geo
Pocket, Pokémon Mini, Odyssey², Channel F, Supervision, Amstrad CPC, Apple II,
Arcadia 2001, and the fantasy consoles. For these, a miss genuinely means RA
does not carry the game.

**Consoles where it is not** — the NES and Famicom Disk System skip a 16-byte
header, the Atari 7800 skips 128 bytes, the Lynx skips 64, the SNES drops a
copier header when it finds one, the N64 byte-swaps, arcade uses the filename,
and every disc console (PlayStation, Saturn, Sega CD, Dreamcast, PSP, 3DO,
CD-i, PC-FX…) hashes an executable *inside* the image rather than the image.
For these, RomM's md5 will never match, however well known the game is.

The plugin knows which is which (`consoles.WHOLE_FILE_MD5`, taken from the
whole-file arm of rcheevos' own dispatcher) and **says which case you are in
when a lookup misses**. Telling you "not found" when the truth is "wrong kind
of hash" would send you looking for the wrong problem.

To get an RA-shaped hash for a console in the second group, hash the ROM with
rcheevos itself — RetroArch and every RA-enabled emulator print it, and
`rc_hash` is available as a standalone tool.

## Passing the hash

`RomRef.extra` is read for `ra_hash`, `md5`, `md5_hash`, `hash` and
`source_id`, in that order, so a future host that computes hashes for plugins
needs no change here. Today the route that works is the CLI:

    rom-hub enrich retroachievements 42 --source-id 32e1a15161ef1f070b023738353bde51

RomM already has the value: `GET /api/roms/42` returns `md5_hash`. Anything
that is not 32 hex characters is refused before a request is made.

## A miss is a miss

If no game on that console carries the hash, the plugin **raises**. It does not
fall back to matching the ROM's title against RA's game list, however close the
names look. An `ra_id` is not decoration — an achievements client will trust it
later — and a plausible wrong id is worse than no id at all.

## Platforms

`retroachievements/consoles.py` maps RomM platform slugs to RA console ids. It
is not a list from memory: it is RomM 4.9.2's own answer, the `slug` and
`ra_id` of each of the 66 platforms (out of 458) that carry one, read live from
`GET /api/platforms/supported`. A test asserts the table still equals that
capture. The ids agree with rcheevos' `include/rc_consoles.h`.

An unmapped platform raises **"needs mapping"** and names itself. A guessed
console id does not fail loudly — it fetches a different system's game list,
matches nothing, and looks exactly like "RA does not have this game".

## Bandwidth, and RA's request about it

One request per enrich, always. `API_GetGameList.php` carries `Title`, `ID` and
`Hashes` together, so calling `API_GetGame.php` afterwards would cost a second
request for fields RPP v1 has nowhere to put.

RetroAchievements' own documentation asks callers to cache this endpoint
aggressively and warns that some consoles' responses are large. The Hub caps a
single `ctx.http` response at 4 MiB; if a console's list exceeds that, the
plugin reports it and suggests `only_with_achievements = true`, which is the
default for exactly this reason. If you are enriching a whole library, expect
one full game-list fetch per ROM — the Hub has no cross-process cache — and
consider working one console at a time.

## Terms and licensing, in plain language

The RetroAchievements web API is public, documented, and key-authenticated; the
key is what makes you a known caller rather than an anonymous one, and this
plugin uses it exactly as documented. `retroachievements.org/robots.txt` allows
`User-agent: *`, and the plugin is not a crawler in any case: one keyed API call
against a documented endpoint, per ROM you asked about.

The game data (titles, ids, achievement sets) is RetroAchievements' community's
work. This plugin copies a title and a numeric id into your own library so your
ROMs line up with RA's records. Bulk-harvesting their catalogue is a different
activity, it is the thing their caching notice is about, and it is not what this
plugin does.

This plugin's own code is MIT (see `LICENSE`).

## Verification status

The offline tests run against RetroAchievements' **own published response
shapes**, taken from the two places the project publishes them openly on GitHub
— the sample in `RetroAchievements/api-docs` (`docs/v1/get-game-list.md`) and
the mock in `RetroAchievements/api-js` (`src/console/getGameList.test.ts`).
They are not a capture we made from the live API: the endpoint needs a key, and
no key was available when this plugin was written. **The live path is therefore
unverified.** The no-key refusal path *has* been exercised end to end through
the Hub's CLI.

## Notes

The plugin opens no sockets. `ctx.http` is an RPC back to the Hub, which checks
every URL against this plugin's declared allowlist (`retroachievements.org`,
and nothing else) before fetching anything.
