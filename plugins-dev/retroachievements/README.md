# RetroAchievements plugin for ROM Hub

Implements the RPP v1 `metadata` capability: identifies a ROM by its hash on
[RetroAchievements](https://retroachievements.org) and writes back the game's
`ra_id` and title.

| Capability | Endpoint | Does |
|---|---|---|
| `metadata` | `API_GetGameList.php?i=<console>&h=1` | matches your hash against RA's, exactly |

## Install

    rom-hub plugin install retroachievements
    rom-hub plugin secret set retroachievements api_key     # prompts; nothing echoed
    rom-hub enrich retroachievements 42 --source-id <md5>

## Where the API key is kept

`api_key` is declared `type = "secret"`, so the Hub does **not** put it in its
plain config. Concretely:

- it is not in `state.json`, the file that holds every other setting and the
  one people open, dump, screenshot and commit;
- it is redacted from `rom-hub plugin list`, `plugin config`, `plugin secret
  list`, `browse`, `backend info`, `jobs` and `--help`, and scrubbed out of any
  error message the Hub builds — including this plugin's own stderr if it ever
  prints the key while crashing;
- `rom-hub plugin secret set` prompts on a terminal, or reads stdin or an
  environment variable, so it need never enter your shell history. Passing
  `--value` still works and warns you that it just did.

**What that protects depends on your host, and the honest answer is printed by
`rom-hub plugin secret list`.** Read it once rather than assuming:

| Store | What it means |
|---|---|
| OS keyring | Whatever your OS gives. A locked login keychain is a real boundary; a desktop keyring unlocked at login is readable by anything running as you. |
| file + `ROM_HUB_SECRET_KEY` | Encrypted with a key supplied from outside the box (a Docker secret, a systemd credential). The file at rest is genuinely unreadable without it. |
| file, generated key (**the default**) | Encrypted, but the key sits in the same directory. That is **obfuscation, not secrecy** — whoever can read one file can read the other. It buys you that the key is not in `state.json` and not in any command's output. It does not survive somebody reading the directory. |

On a headless Docker box — this Hub's primary deployment — you get the third
row unless you set `ROM_HUB_SECRET_KEY`. So the advice that mattered before
still matters, for a smaller reason:

- A RetroAchievements web API key is **per-account, read-only, and resettable**.
  It is not your password and it cannot spend anything. Get it from your RA
  profile under **Settings → Keys**, where you can also reset it at any time.
- Treat the one you put here as rotatable. Reset it if the machine changes
  hands, and do not reuse it anywhere that matters.

**What is not claimed.** The plugin *receives* the key — it has to, to make its
request — and a plugin that chose to print its own credential into a search
result or POST it somewhere could. That is unchanged by any of the above and is
not what this protects against: a plugin already runs arbitrary code. What
changed is accidental disclosure, which is the way credentials actually escape.

### Upgrading from v0.1.0, which stored it in the clear

**You do not re-enter the key. The Hub moves it for you.** `v0.1.0` declared
`api_key` as a plain `str`, so anyone who configured this plugin before `v0.2.0`
has their key sitting in `state.json` in plaintext. Nothing about that breaks and
nothing is silently dropped: the first command that *runs* this plugin —
`enrich`, or anything else that starts its subprocess — reads the plaintext,
writes it to the secret store, removes it from the plain config, and prints one
line on stderr saying so, naming the field and never the value. The run itself
behaves identically. (`rom_hub.secrets.migrate_plaintext`, called from
`prepare_secrets` at every subprocess-start site, so an operator who never
reinstalls still gets moved over.)

**Installing `v0.2.0` is what arms this, and nothing happens before you do.**
Both the migration and the redaction key off the *installed* manifest's schema,
so while `v0.1.0` is the installed version the Hub has no way to know `api_key`
is a credential: it is not redacted from `rom-hub plugin config`, and
`rom-hub plugin secret list` does not mention it. Upgrade first. In the window
after upgrading but before the next run, the value is already redacted from every
command's output and `secret list` flags it `STILL IN PLAIN CONFIG`.

The one path that *does* cost you a re-entry is `rom-hub plugin secret clear`:
it deletes from the store **and** drops un-migrated plaintext from the config, so
running it before the migration has happened discards the key outright. That is
deliberate — otherwise "cleared" would be a lie for exactly the operator most
likely to be running it — but if you have not migrated yet, clear means gone.

**Rotate it anyway** if that `state.json` was ever committed, shared or backed
up. Moving a credential out of a file does not move it out of the copies.

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `api_key` | `secret` | *(none)* | your RA web API key — see [above](#where-the-api-key-is-kept). A `secret` may not declare a default: a manifest is a public file in a git repo |
| `username` | `str` | `""` | your RA username, sent as `z`. Optional: RA's docs mark only `y` required, but RA's own client sends both |
| `set_name` | `bool` | `true` | write the matched game's title into RomM's `name` |
| `only_with_achievements` | `bool` | `true` | ask RA for `f=1`, the smaller list |

With no `api_key` the plugin refuses **before making any request**, with a
message naming the config key, where to get a value for it, and the command
that stores one — not a 401, not a `KeyError`.

An unset secret arrives as the empty string rather than as a missing key, so
that refusal is this plugin's own sentence and not a `KeyError` raised from
inside it.

## What it sets

- **`ra_id`** — the RetroAchievements game id. Coerced to an `int`: this
  endpoint returns `ID` as a JSON *string* (`"4247"`), which RA's own client
  corrects for, and `"4247"` is not the same value as `4247` in a column RomM
  parses as an integer.
- **`name`** — the matched game's RA title, unless `set_name = false`. Safe to
  write because the match is by hash, which is the strongest identification
  available; turn it off if you curate names yourself.

### RomM needs its own RetroAchievements key to accept `ra_id`

**This is a precondition on your RomM server, not on this plugin, and without it
every successful match still fails at the write.** Measured live against RomM
4.9.2: `PUT /api/roms/{id}` does not simply store the `ra_id` it is handed. It
treats the field as a request to re-identify the ROM and calls RA itself —
`update_rom` → `meta_ra_handler.get_rom_by_id` → `get_game_extended_details` —
signing that call with **RomM's** `RETROACHIEVEMENTS_API_KEY`. When that
variable is unset the key is `None`, yarl refuses to build the query string, and
the request ends as:

    TypeError: Invalid variable type: value should be str, int or float,
    got None of type <class 'NoneType'>

which reaches the Hub as a bare `PUT /api/roms/2 failed (500): Internal Server
Error`. Nothing in that message names RetroAchievements, so the symptom looks
like a Hub fault and is not one — the hash matched, the patch was correct, and
the server rejected it for a reason of its own.

`ra_id` is the only provider id this plugin writes, so on a RomM with no RA key
of its own the plugin **cannot** succeed. Set `RETROACHIEVEMENTS_API_KEY` on the
RomM container (the same per-account key works; `GET /api/heartbeat` then
reports `METADATA_SOURCES.RA_API_ENABLED: true`) before enriching. With it set,
the same request succeeds and both fields land.

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

**The live path has been exercised end to end against the real API**, with a
real RetroAchievements web API key, on 2026-07-31. What that run establishes,
precisely:

- `rom-hub import libretro-content "Sega - Mega Drive - Genesis/aepd.bin"`
  filed the ROM into a disposable RomM 4.9.2, which computed
  `md5_hash = 30c01f5a82b51fd7e23315dd070d5818` for it;
- `API_GetGameList.php?i=1&h=1&f=1` returned 607 Mega Drive games carrying 950
  hashes, one of which is that exact digest, on game `17392`
  `~Homebrew~ Alter Ego`;
- `rom-hub enrich retroachievements 2 --source-id 30c01f5a…` reported
  `rom 2: updated name, ra_id`, and `GET /api/roms/2` afterwards returned
  `ra_id = 17392` and `name = "~Homebrew~ Alter Ego"` — RomM's own API, read
  back after the fact, not the Hub's report of its own work.

So `ID`-as-a-JSON-string really does arrive that way and really is coerced, the
hash comparison really does match on a live list, and the two fields really do
land. The `secret` handling was exercised on the same run: the key went in
through `rom-hub plugin secret set retroachievements api_key --env`, and
`state.json` does not contain it.

**What the run also established, by failing:** writing `ra_id` to a RomM with no
`RETROACHIEVEMENTS_API_KEY` of its own is a 500 every time — see [RomM needs its
own RetroAchievements key](#romm-needs-its-own-retroachievements-key-to-accept-ra_id).
That is the whole reason this section could not have been written from the
offline tests. Nothing in the fixtures could have surfaced it, because the
fixtures stop at the plugin's own output.

**Still unverified, and named rather than implied:** every console except Mega
Drive. The match is one code path, so a hit on console 1 is a hit on console 53,
but the *table* in `consoles.py` is only confirmed where a live lookup has
actually run. The 401 branch has not been exercised against a genuinely rejected
key, and neither has the >4 MiB refusal.

The offline tests continue to run against RetroAchievements' **own published
response shapes**, taken from the two places the project publishes them openly
on GitHub — the sample in `RetroAchievements/api-docs`
(`docs/v1/get-game-list.md`) and the mock in `RetroAchievements/api-js`
(`src/console/getGameList.test.ts`). The live run agrees with both.

### The NES is the trap this verification walked into

The obvious thing to reach for is the NES build of the same game —
`~Homebrew~ Alter Ego` is also RA game `8170` on console 7. **That chain cannot
close, and the failure is instructive rather than incidental.** Measured on the
same run:

| | |
|---|---|
| libretro's `Alter Ego.nes`, RomM's `md5_hash` | `1f21f648af663fbd3c864b7283d994c7` |
| the same file with the 16-byte iNES header skipped | `baf72625249378213dd2c0abafd1309a` |
| RA's only published hash for game `8170` | `32bff38b91f5eb2cb523d4266b74f7b2` |

Neither digest appears **anywhere** in the 1,116 NES games with achievements —
checked against every hash in the full list, not just that game's. Two separate
things are true at once: console 7 is not in `WHOLE_FILE_MD5`, so the whole-file
md5 was never going to match; and the header-skipped digest does not match
either, because libretro distributes a different build of Alter Ego from the one
RA registered. A hash match is a match on *one dump*, not on a game.

The plugin got this right without being told. Fed the NES md5 it refused before
writing anything, and named the correct one of its two reasons:

    no RetroAchievements game on console 7 carries the hash 1f21f648… — For
    this console RetroAchievements does NOT hash the whole file … so RomM's
    md5 will never match no matter how well known the game is.

That message is the reason the miss took minutes to diagnose instead of hours,
and it is now live-verified too.

## Notes

The plugin opens no sockets. `ctx.http` is an RPC back to the Hub, which checks
every URL against this plugin's declared allowlist (`retroachievements.org`,
and nothing else) before fetching anything.

---

## Seen working

The cover art and titles in this library were written by metadata plugins like this one. Where a tile still shows a placeholder, no art database carried that game — homebrew and interactive fiction mostly are not in one.

![RomM populated by ROM Hub plugins](https://raw.githubusercontent.com/BlizzHacker/rom-hub/master/docs/screenshots/romm.png)

Full showcase — all three backends (RomM, Gaseous, Retrom), every command transcript, and an honest account of what the pictures do *not* show: **[https://github.com/BlizzHacker/rom-hub/blob/master/docs/SHOWCASE.md](https://github.com/BlizzHacker/rom-hub/blob/master/docs/SHOWCASE.md)**

Part of [ROM Hub](https://github.com/BlizzHacker/rom-hub) — install with `rom-hub plugin install retroachievements`.
