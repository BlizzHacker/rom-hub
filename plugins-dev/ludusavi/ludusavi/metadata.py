"""ludusavi `metadata`: where a game keeps its saves.

    RomRef -> is this a PC platform? -> normalised title keys
           -> exactly one manifest entry -> a raw_manual_metadata blob

Nothing is fetched. The manifest arrives as a `[[data_assets]]` file the
host has already downloaded and hash-verified, so this capability opens no
socket and makes no `ctx.http` call at all.

Three decisions here are the careful half of a choice that could have gone
the other way, and one of them is a compromise this module is not going to
pretend otherwise about.

**Where the data goes: `raw_manual_metadata`, and the reason is not
aesthetic.** RPP's `MetadataPatch` has no save-location field, and neither
does RomM — there is no right answer here, only a least-wrong one. RomM
4.9.2 accepts eight `raw_*_metadata` form fields, and **seven of them are
gated on a provider id**::

    if cleaned_data["hltb_id"] and raw_hltb_metadata is not None:
        cleaned_data["hltb_metadata"] = raw_hltb_metadata
    ...
    if raw_manual_metadata is not None:
        cleaned_data["manual_metadata"] = raw_manual_metadata

(`backend/endpoints/roms/__init__.py`, read out of a running 4.9.2.) So a
blob written to `raw_hltb_metadata` is silently dropped unless an
`hltb_id` is written with it — and writing a fabricated HowLongToBeat id
into somebody's library to make our own data stick would be exactly the
"stuff it somewhere structurally wrong to make it appear" that must not
happen. `raw_manual_metadata` is the only field with no id gate, and it is
also the only one of the eight that does not claim to be a named
third-party provider's data. It is where this goes.

**Two things about that are honestly bad, and are in the README as well as
here.** First, RomM's response schema for `manual_metadata` is a closed
`TypedDict` of seven keys, so `GET /api/roms/{id}` does **not** echo the
`ludusavi` key back — verified against a live 4.9.2, where the database
column holds the whole blob and the API response holds only the keys the
schema knows. The write lands; RomM 4.9.2 does not show it. Second, RomM's
update endpoint takes the whole blob for a field and `RomRef` deliberately
does not hand a plugin the library's existing metadata, so this
**replaces** any hand-entered `manual_metadata` on that rom rather than
merging with it.

**Matching refuses far more often than it guesses.** See `titles.py`. The
platform is checked before anything else, a key has to be at least four
characters, matching is exact equality on a normalised title, and a key
that resolves to two manifest entries is a refusal naming both rather than
a coin toss. `--source-id` takes ludusavi's own title verbatim for when
the operator knows the answer and the library's name does not say it.
"""

import json

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .manifest_data import ManifestUnreadable, find
from .platforms import PC_PLATFORMS, describe, is_pc_platform
from .titles import MIN_KEY_CHARS, candidates, normalise

#: The name declared in `[[data_assets]]`. The host hands over a path to
#: bytes that already match the sha256 in the same manifest.
ASSET = "manifest.yaml"

#: Where the data came from, recorded in every blob this plugin writes so
#: the answer is attributable years later without reading this code.
SOURCE_URL = "https://github.com/mtkennerly/ludusavi-manifest"
SOURCE_LICENSE = "MIT"

#: The key this plugin owns inside `manual_metadata`. Namespaced, because
#: the field is shared with RomM's own hand-entered values.
BLOB_KEY = "ludusavi"

#: A description of one game's save locations, not a filesystem listing.
#: `MetadataPatch` already caps a raw field at 256 KiB; this is the bound
#: that produces a legible refusal instead of a validation error.
MAX_LOCATIONS = 200


class NoSaveData(Exception):
    """Nothing could be said about this rom, and the message says why."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        allowed = self._platforms()
        if not is_pc_platform(rom.platform, allowed):
            raise NoSaveData(
                f"rom {rom.rom_id} is on platform {rom.platform or '(unset)'!r}, "
                f"and the ludusavi manifest describes where **PC** games keep "
                f"their saves ({describe(allowed)}). On a console the save "
                f"lives in the cartridge or in a file the emulator names, so "
                f"the manifest has nothing true to say about this rom -- and "
                f"plenty of console titles share a name with a PC game, so "
                f"looking one up anyway would attach a Windows path to a "
                f"cartridge dump. Nothing was written"
            )

        override = (rom.extra.get("source_id") or "").strip()
        if override:
            labels = [(override, "source_id")]
        else:
            labels = [(rom.name, "name"), (rom.filename, "filename")]
        labels = [(text, origin) for text, origin in labels if (text or "").strip()]
        if not labels:
            raise NoSaveData(
                f"rom {rom.rom_id} has neither a name nor a filename in the "
                f"library, and the ludusavi manifest is keyed by title alone"
            )

        tried: list[str] = []
        for text, origin in labels:
            keys = candidates([text])
            if not keys:
                continue
            tried.extend(keys)
            found = self._lookup(keys)
            for key in keys:
                games = found.get(key)
                if not games:
                    continue
                if len(games) > 1 and override:
                    # `--source-id` is the escape hatch from an ambiguity,
                    # so it has to be able to escape one that normalising
                    # created: `Accounting` and `Accounting+` share a key,
                    # and an operator who typed the second one has already
                    # said which they mean. Exact title, case-insensitively
                    # -- still equality, never a prefix.
                    exact = [
                        game
                        for game in games
                        if game.title.casefold() == override.casefold()
                    ]
                    if len(exact) == 1:
                        games = exact
                if len(games) > 1:
                    raise NoSaveData(
                        f"{key!r} matches {len(games)} entries in the ludusavi "
                        f"manifest -- {', '.join(repr(g.title) for g in games)} "
                        f"-- and a save path attached to the wrong one of those "
                        f"is worse than none. Re-run with --source-id set to "
                        f"one of those titles exactly as spelled above. "
                        f"Nothing was written"
                    )
                game = games[0]
                if not game.has_locations():
                    raise NoSaveData(
                        f"the ludusavi manifest has an entry for "
                        f"{game.title!r} but records no save or config "
                        f"locations for it -- 30,789 of its 52,886 entries are "
                        f"a store id and nothing else. There is nothing to "
                        f"write. If you know where this game saves, "
                        f"PCGamingWiki is where the manifest is compiled from"
                    )
                return MetadataPatch(
                    raw_metadata={
                        "raw_manual_metadata": {
                            BLOB_KEY: self._blob(game, key, origin)
                        }
                    }
                )

        if not tried:
            shown = ", ".join(repr(text) for text, _ in labels)
            raise NoSaveData(
                f"rom {rom.rom_id} ({shown}) gives no title key of at least "
                f"{MIN_KEY_CHARS} characters once tags and punctuation are "
                f"removed. A shorter key is not evidence of anything: 259 of "
                f"the manifest's own keys are that short and they are '1', "
                f"'21', '3d', 'age', 'arc'. Nothing was written"
            )
        raise NoSaveData(
            f"the ludusavi manifest has no entry matching rom {rom.rom_id}. "
            f"Tried: {', '.join(repr(key) for key in tried)}. Matching is "
            f"exact on a normalised title and deliberately does no fuzzy "
            f"matching, so a title the library spells differently will miss "
            f"-- pass ludusavi's own spelling with --source-id"
        )

    # -- configuration ---------------------------------------------------

    def _platforms(self) -> frozenset[str]:
        configured = self.ctx.config.get("platforms")
        if not configured:
            return frozenset(PC_PLATFORMS)
        if isinstance(configured, str):
            configured = [configured]
        return frozenset(
            str(value).strip().lower() for value in configured if str(value).strip()
        )

    # -- the data --------------------------------------------------------

    def _lookup(self, keys: list[str]) -> dict:
        path = self.ctx.data_asset(ASSET)
        return find(path, set(keys), normalise)

    def _blob(self, game, key: str, origin: str) -> dict:
        files = game.files[:MAX_LOCATIONS]
        registry = game.registry[: MAX_LOCATIONS - len(files)]
        blob = {
            "source": SOURCE_URL,
            "source_license": SOURCE_LICENSE,
            "matched_title": game.title,
            "matched_key": key,
            "matched_from": origin,
            # Every location the manifest gives, with its own tags and
            # conditions attached and nothing filtered out here. A
            # consumer decides what a `config` entry is worth; this plugin
            # does not make that decision inside a blob where it would be
            # invisible.
            "files": [location.as_dict() for location in files],
            "registry": [location.as_dict() for location in registry],
            # The one derived convenience, and it is derived in the open:
            # the paths ludusavi itself tags `save`.
            "save_paths": [
                location.where
                for location in files
                if "save" in location.tags
            ],
            "note": (
                "Paths use ludusavi's placeholders (<base>, <winAppData>, "
                "<home>, ...) and are globs. See "
                f"{SOURCE_URL}#format for what each one expands to."
            ),
        }
        if game.steam_id is not None:
            blob["steam_id"] = game.steam_id
        if game.cloud:
            blob["cloud"] = dict(game.cloud)
        encoded = len(json.dumps(blob))
        if encoded > 200_000:
            raise NoSaveData(
                f"the ludusavi entry for {game.title!r} serialises to "
                f"{encoded} characters, which is not the shape of a save-path "
                f"description; nothing was written"
            )
        return blob


__all__ = ["Metadata", "ManifestUnreadable", "NoSaveData"]
