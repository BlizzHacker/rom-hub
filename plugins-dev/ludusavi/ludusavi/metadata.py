"""ludusavi `metadata`: where a game keeps its saves.

    RomRef -> is this a PC platform? -> normalised title keys
           -> exactly one manifest entry -> a summary a library will show

Nothing is fetched. The manifest arrives as a `[[data_assets]]` file the
host has already downloaded and hash-verified, so this capability opens no
socket and makes no `ctx.http` call at all.

**This plugin used to write only a `raw_manual_metadata` blob, and that
blob does not arrive.** The reasoning behind it was careful and the
conclusion was wrong, so both halves are worth keeping written down.

RomM 4.9.2 accepts eight `raw_*_metadata` form fields, and **seven of
them are gated on a provider id**::

    if cleaned_data["hltb_id"] and raw_hltb_metadata is not None:
        cleaned_data["hltb_metadata"] = raw_hltb_metadata
    ...
    if raw_manual_metadata is not None:
        cleaned_data["manual_metadata"] = raw_manual_metadata

(`backend/endpoints/roms/__init__.py`, read out of a running 4.9.2.)
`raw_manual_metadata` is the only one with no id gate and the only one
that does not claim to be a named third party's data, so it was the
least-wrong home — and inventing an `hltb_id` to unlock a different field
would have been putting a fabricated provider id in somebody's library.

What was not established is whether the write *lands*. It does not.
Measured on 2026-08-01 against a live 4.9.2: `PUT` with a marker inside
`raw_manual_metadata` answers 200, and the marker appears nowhere in the
rom record afterwards -- not under `manual_metadata`, not anywhere in the
response at all. Repeated for `raw_hasheous_metadata` and
`raw_igdb_metadata` paired with a *changed* provider id in the same
request, to rule out the id gate: the id lands, the blob does not. So
`raw_metadata` is **off by default** now. Writing into a void is one
thing; letting an operator believe their save paths are in their library
is worse.

`summary` is where this goes instead, because RomM stores that -- the
same measurement, opposite result. It is one paragraph rather than a
structured document, which means the blob is still the richer artefact
and `raw_metadata = true` still produces it for a backend that grows a
home for it.

One thing about the blob remains honestly bad and is in the README:
RomM's update endpoint takes the whole value for a field and `RomRef`
deliberately does not hand a plugin the library's existing metadata, so
enabling it **replaces** any hand-entered `manual_metadata` on that rom
rather than merging with it.

**Matching refuses far more often than it guesses.** See `titles.py`. The
platform is checked before anything else, a key has to be at least four
characters, matching is exact equality on a normalised title, and a key
that resolves to two manifest entries is a refusal naming both rather than
a coin toss. `--source-id` takes ludusavi's own title verbatim for when
the operator knows the answer and the library's name does not say it.
"""

import json

from rom_hub_sdk import MAX_SUMMARY_CHARS, MetadataPatch, MetadataProvider, RomRef

from .manifest_data import CONFIG_TAG, SAVE_TAG, ManifestUnreadable, find
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
                patch: dict = {}
                if self._summary():
                    patch["summary"] = _summary(game)
                if self._raw_metadata():
                    patch["raw_metadata"] = {
                        "raw_manual_metadata": {
                            BLOB_KEY: self._blob(game, key, origin)
                        }
                    }
                if not patch:
                    raise NoSaveData(
                        f"the ludusavi manifest has an entry for "
                        f"{game.title!r}, and both `summary` and "
                        f"`raw_metadata` are switched off, so there is "
                        f"nowhere for it to go. Nothing was written"
                    )
                return MetadataPatch(**patch)

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

    def _summary(self) -> bool:
        return bool(self.ctx.config.get("summary", True))

    def _raw_metadata(self) -> bool:
        """Whether to send the full blob as well. Off by default now.

        Not because the blob is wrong -- it is the richest thing this
        plugin has -- but because it does not arrive. See the module
        docstring: `raw_manual_metadata` is accepted with a 200 and stored
        nowhere RomM will show or return, measured twice on 2026-08-01,
        including paired with a *changed* provider id in the same request
        to rule out the id gate. Sending it by default is spending bytes
        to write into a void and, worse, letting an operator believe their
        save paths are in their library.

        Left switchable rather than deleted: a different RomM version, or
        a backend that grows a home for it, would make it worth having
        again, and the blob-building code is the part that took the work.
        """
        return bool(self.ctx.config.get("raw_metadata", False))

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


# -- the one form a library will actually show ---------------------------

#: How many paths a summary names before it says how many more there are.
#: A summary is read by a person standing in front of a rom page; a
#: fourteen-line list of globs is a blob with worse formatting.
SUMMARY_PATHS = 3


def _summary(game) -> str:
    """Where this game keeps its saves, in the field RomM stores.

    The blob is richer and the blob does not arrive. This is the whole of
    what a library will show, so it is written for someone reading a rom
    page rather than for a consumer parsing JSON: the save paths first,
    the config paths only if there are no save paths, a count when there
    are more than `SUMMARY_PATHS`, and the cloud-sync note last because
    "Steam Cloud syncs this" is often the entire answer somebody wanted.

    Ludusavi's placeholders are left exactly as the manifest writes them.
    `<winAppData>` expanded to a path on *this* machine would be a claim
    about the operator's filesystem that the manifest does not make.
    """
    saves = [location for location in game.files if SAVE_TAG in location.tags]
    kind = "Saves"
    if not saves:
        saves = [location for location in game.files if CONFIG_TAG in location.tags]
        kind = "Config"
    if not saves:
        saves = list(game.files)
        kind = "Files"

    parts: list[str] = []
    if saves:
        shown = [location.where for location in saves[:SUMMARY_PATHS]]
        line = f"{kind}: {'; '.join(shown)}"
        remaining = len(saves) - len(shown)
        if remaining > 0:
            line += f" (+{remaining} more)"
        parts.append(line + ".")

    if game.registry:
        keys = [location.where for location in game.registry[:SUMMARY_PATHS]]
        line = f"Registry: {'; '.join(keys)}"
        remaining = len(game.registry) - len(keys)
        if remaining > 0:
            line += f" (+{remaining} more)"
        parts.append(line + ".")

    syncing = sorted(store for store, syncs in game.cloud.items() if syncs)
    if syncing:
        parts.append(f"Cloud saves: {', '.join(syncing)}.")

    parts.append(
        "Paths are ludusavi's placeholders and globs; see "
        f"{SOURCE_URL} for what each expands to."
    )
    return _fit(" ".join(parts))


def _fit(text: str) -> str:
    """Trimmed to the patch's ceiling, at a word, with a visible mark."""
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    cut = text[: MAX_SUMMARY_CHARS - 1]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if len(spaced) > MAX_SUMMARY_CHARS // 2 else cut).rstrip(" ;,.") + "…"


__all__ = ["Metadata", "ManifestUnreadable", "NoSaveData"]
