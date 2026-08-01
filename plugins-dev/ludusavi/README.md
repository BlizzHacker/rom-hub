# ludusavi

Where a game keeps its saves, from
[`mtkennerly/ludusavi-manifest`](https://github.com/mtkennerly/ludusavi-manifest)
— the community dataset behind
[Ludusavi](https://github.com/mtkennerly/ludusavi), compiled from
[PCGamingWiki](https://www.pcgamingwiki.com/).

`metadata`. RPP v1.

## Which RomM issue this answers

[**rommapp/romm#1908** — *[Feature] Ludusavi
Integration*](https://github.com/rommapp/romm/issues/1908) — open since
2025-05-23, eight ❤️, unbuilt.

The issue asks for "an integration to allow saving games/data to RomM
through Ludusavi", and suggests bundling the Linux binary in RomM's Docker
image. **This is not that**, and it is worth being clear about the gap
before the rest of the README: this plugin does not back anything up, does
not run Ludusavi, and does not move a single save file. It answers the
half that a plugin can answer — *where does this game keep its saves?* —
by attaching the manifest's own answer to the rom, so a backup tool, a
script, or a person has somewhere to read it from.

## What it does

For a rom on a PC platform, looks the title up in the ludusavi manifest and
writes what the manifest says about it:

```json
{
  "ludusavi": {
    "source": "https://github.com/mtkennerly/ludusavi-manifest",
    "source_license": "MIT",
    "matched_title": "Prince of Persia",
    "matched_key": "prince of persia",
    "matched_from": "name",
    "files": [
      {"path": "<base>/PRINCE.SAV", "tags": ["save"], "when": [{"os": "dos"}]},
      {"path": "<base>/CONFIG.DAT", "tags": ["config"], "when": [{"os": "dos"}]}
    ],
    "registry": [],
    "save_paths": ["<base>/PRINCE.SAV"],
    "note": "Paths use ludusavi's placeholders (<base>, <winAppData>, <home>, ...) and are globs."
  }
}
```

Every location the manifest records is reported with its own tags and `when`
conditions, unfiltered. `save_paths` is the one derived convenience — the
`files` entries ludusavi itself tags `save` — and it is derived in the open
rather than by dropping the rest.

## Where it writes, and why that is a compromise

**It writes `raw_manual_metadata`.** RPP's `MetadataPatch` has no
save-location field and neither does RomM, so there is no right answer
here — only a least-wrong one. The reason it is this field and not one of
the other seven is mechanical, not aesthetic. RomM 4.9.2's update endpoint
reads:

```python
if cleaned_data["hltb_id"] and raw_hltb_metadata is not None:
    cleaned_data["hltb_metadata"] = raw_hltb_metadata
...
if raw_manual_metadata is not None:
    cleaned_data["manual_metadata"] = raw_manual_metadata
```

*(`backend/endpoints/roms/__init__.py`, read out of a running 4.9.2.)*

Seven of the eight `raw_*_metadata` fields are **gated on a provider id**:
a blob sent to `raw_hltb_metadata` is silently discarded unless an
`hltb_id` is written alongside it. Writing a fabricated HowLongToBeat or
MobyGames id into somebody's library so that our own data would stick is
exactly the thing not to do. `raw_manual_metadata` is the only field with
no id gate, and it is also the only one of the eight that does not claim to
be a named third party's data.

**Two things about that are honestly bad.**

1. **RomM 4.9.2 will not show it.** `manual_metadata` is typed as a closed
   `TypedDict` of seven keys (`genres`, `franchises`, `companies`,
   `game_modes`, `age_ratings`, `first_release_date`, `youtube_video_id`),
   so `GET /api/roms/{id}` drops the `ludusavi` key on the way out.
   Verified against a live 4.9.2: the database column holds the whole blob,
   the API response holds only the schema's keys. The write lands and is
   durable; RomM's own API and UI do not surface it. Reading it back today
   means reading the column. That is a RomM-side gap, and it is the same
   gap issue #1908 is asking to have closed.
2. **It replaces, it does not merge.** RomM's update endpoint takes the
   whole blob for a field, and `RomRef` deliberately does not hand a plugin
   the library's existing metadata, so there is nothing to merge *with*.
   If you have hand-entered `manual_metadata` on a rom, enriching it here
   overwrites that. Nothing else on the rom is touched — the patch sets
   exactly one field and `MetadataPatch`'s absent-means-leave-alone rule
   covers the rest.

## The false positive this is built around

Ludusavi's manifest is a **PC** dataset — issue #1908 introduces the tool
in those words, "a save manager for a multitude of PC games and
storefronts". Its 52,886 entries say where a game writes save files on a
PC filesystem. That is true of a DOS game and meaningless for a cartridge
dump, where the save lives in the cartridge's SRAM or in a file the
emulator names.

**`Sonic the Hedgehog` is in the manifest.** So are `Prince of Persia`,
`Aladdin`, `Contra`, `Double Dragon`, `Golden Axe` and a long tail of other
names a console library is full of. Every one would match a console ROM on
the title alone, and every one of those matches would attach a Windows or
Steam path to a game that never had one. **A wrong save path is worse than
a missing one**, because nobody re-checks a field that is already filled
in.

Five guards, in the order they run:

| guard | what it stops |
|---|---|
| **platform first** | The rom's platform is checked before the title is even normalised. Only `dos`, `win`, `win3x`, `linux` and `mac` are looked up — the RomM slugs corresponding to ludusavi's own `when.os` vocabulary. A Mega Drive `Sonic the Hedgehog` is refused **by name**, never searched and missed. |
| **exact equality** | Matching is equality on a normalised title. No prefix, no substring, no edit distance. `Quake` cannot reach `Quake II`. |
| **a four-character floor** | 259 of the manifest's normalised keys are shorter than that — `1`, `21`, `3d`, `age`, `air`, `arc`, `aka`. A rom called `Arc` refuses rather than matching a PC game called `ARC`. |
| **ambiguity refuses** | 243 normalised keys are shared by two or more titles, covering 491 entries: `Accounting` / `Accounting+`, `Adam's Venture Origins` / `Adam's Venture: Origins`, `Klaus` / `-KLAUS-`. A key that resolves to more than one game names them all and writes nothing. |
| **decoration is an allowlist** | `(USA)`, `(Rev A)`, `(Disk 1 of 2)`, `[!]` are dropped; an unrecognised parenthesis is **kept**, so `Dungeons & Dragons (Chronicles of Mystara)` stays intact instead of collapsing onto a different game. |

Normalisation is unicode-aware (`str.isalnum`, not `[0-9a-z]`) for a
sharper reason than tidiness: an ASCII class folds every Japanese, Chinese,
Korean and Cyrillic title in the manifest to the **empty string**. Measured:
280 titles land in one bucket that way, so a rom with a non-Latin name
would match all 280 at once. With `isalnum` that bucket disappears.

**The posture is: miss rather than guess.** No roman-numeral folding, no
subtitle stripping, no year inference, no fuzzy fallback. Each of those
finds more games and each can pick the wrong one. When the library's
spelling differs from ludusavi's, `--source-id` takes ludusavi's own title
verbatim — and it can also resolve an ambiguity that normalising created
(`--source-id "Accounting+"`), by exact title.

## Configuration

| key | type | default | what it does |
|---|---|---|---|
| `platforms` | `list[str]` | `["dos", "win", "win3x", "linux", "mac"]` | which library platforms this dataset is treated as true about. |

Widening `platforms` is possible and is a deliberate decision with a known
cost: the table above explains what the default is protecting you from.
`scummvm` is deliberately not in the default — a ScummVM game's saves go
where ScummVM puts them, not where the DOS original did.

## How the data gets here

`[[data_assets]]`, and it is the case that mechanism exists for. The
manifest is a single 17,460,574-byte YAML file with no API in front of it,
so:

- `ctx.http` cannot carry it — 4 MiB cap, text rather than bytes, and no
  cache between per-command subprocesses;
- `[[data_assets]]` can — the host downloads it once, verifies it against
  the sha256 in `manifest.toml`, caches it, and hands the plugin a path.
  128 MiB bound, well clear of 17 MB.

**Pinned to a commit sha, not a branch.** The repository publishes no
releases and no tags at all, so a commit is the only immutable handle
there is — and a declared hash and a floating URL cannot both be right.
`master` would start failing verification the next time the manifest is
regenerated from PCGamingWiki, which is exactly when a human should look at
it. Updating the plugin means bumping the sha and the sha256 together, in a
diff a reviewer can read.

`size_bytes` is the **decoded** size. `raw.githubusercontent.com` serves
the file gzipped (Content-Length 2,367,377) and what lands on disk — the
file the plugin opens and the sha256 covers — is 17,460,574 bytes.

**There is a YAML parser in this plugin, and there is a reason.** ROM Hub
depends on pydantic, httpx and python-socketio; PyYAML is not among them. A
plugin cannot add a host dependency, and adding one to `rom-hub` so that
one plugin can read one file would put a YAML loader in every operator's
install for a source most of them do not use. So `manifest_data.py`
implements the small closed subset `serde_yaml` actually emits — two-space
block indentation, block mappings and sequences, quoted and bare scalars,
`{}`/`[]` — and **refuses** anything outside it, naming the line. Verified
by parsing all 52,886 entries of the real file: 0 refusals, 1.7 s. A reader
that quietly skips what it does not understand would turn an upstream
format change into wrong save paths instead of a visible failure.

One pass per enrich, ~0.25 s, a few kilobytes of memory. An index over all
52,886 titles would make `enrich` repeatedly cheaper and cost ~15 MB
resident; the tradeoff is available and deliberately not taken.

## The source's terms, in plain language

`mtkennerly/ludusavi-manifest` is **MIT-licensed** (LICENSE in the
repository root), which is about as unambiguous as a data source gets: you
may use, copy and redistribute it, including commercially, keeping the
copyright notice.

The data is compiled from **PCGamingWiki**, whose contributions are
licensed
[CC BY-NC-SA 3.0](https://www.pcgamingwiki.com/wiki/PCGamingWiki:Copyrights).
The manifest is a derived dataset of factual file paths and registry keys
rather than a copy of wiki prose, and its own repository publishes it under
MIT. If you plan to redistribute a large derived work, that is the chain to
check rather than take from a plugin README.

No robots.txt question arises: this plugin fetches one pinned blob URL from
`raw.githubusercontent.com` once, through the host's own downloader, and
never crawls anything.

## Install

    rom-hub plugin install ludusavi
    rom-hub plugin assets ludusavi --fetch     # 17.4 MiB, once
    rom-hub enrich ludusavi 42
    rom-hub enrich ludusavi 42 --source-id "Accounting+"

## Licence

MIT (this plugin's own code). The dataset is MIT and belongs to its
authors; see *The source's terms* above.
