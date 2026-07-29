"""hasheous `metadata`: one hash in, one identity out.

    RomRef.extra -> a hash -> GET /api/v1/Lookup/ByHash/<kind>/<hex>
                           -> name + hasheous_id + whatever else is mapped

Hasheous exists to say "this exact dump is this game", by matching the
signature DATs (No-Intro, Redump, TOSEC, MAME, WHDLoad, FBNeo,
RetroAchievements) against metadata providers. That makes it the one
free, key-free source in this directory that can hand back **other
providers' ids** -- an IGDB id, a TheGamesDB id, a RetroAchievements id --
without the operator holding a key for any of them.

Three decisions are worth stating, because each could have gone the other
way and the other way would have been quietly worse.

**The lookup is a GET, and that is not a stylistic preference.** Hasheous
publishes a `POST /api/v1/Lookup/ByHash` that takes several hashes at
once and a hosted MCP endpoint (`POST /api/v1/Mcp`) with a
`hasheous_search_games` tool that can find a game by *name*. Both are
POSTs, and `ctx.http` is `get()` and nothing else -- a plugin has no
socket, and the brokered API it does have offers one verb. So this plugin
uses the four single-hash GET routes, and the consequence is stated
rather than hidden: **there is no name search here.** A rom with no hash
is refused, not guessed at.

**A CRC-32 match is refused by default.** CRC-32 is 32 bits. Over the
millions of dumps hasheous indexes, two files sharing one are not a
curiosity, and the failure is silent: hasheous answers confidently about
a different game and the wrong title, the wrong ids and the wrong
identity land in the library. `allow_crc32` turns it on, and even then
the platform cross-check has to agree.

**Nothing is written that was not resolved.** `MetadataPatch` treats an
absent field as "leave RomM alone", so a lookup that maps to IGDB but not
to TheGamesDB sets `igdb_id` and leaves `tgdb_id` untouched -- it does not
write an empty one. Only metadata entries hasheous itself marks `Mapped`
are used: a `NotMapped` row is a search hasheous has *scheduled*, not an
answer it has, and copying its empty id into a library would look exactly
like a resolved one afterwards.

**And another provider's id is not free to write.** Handing RomM an
`igdb_id` or an `ra_id` does not store a number -- it makes RomM go and
fetch from that provider, which needs that provider's key. Writing
`ra_id` to a RomM with no RetroAchievements key answers **500**, not a
degraded write. So the two ids that do that are behind
`cross_provider_ids`, off by default: this plugin's whole point is that it
needs no key, and a default that only works if you have somebody else's
would undo it. See `ALWAYS_SAFE_IDS`.
"""

import json
from urllib.parse import quote

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .hashes import BadHash, offered  # noqa: F401  (BadHash re-exported)
from .platforms import NeedsMapping, expected_keys, key  # noqa: F401

BASE = "https://hasheous.org/api/v1/Lookup/ByHash/"

# Hasheous metadata source -> the RomM provider-id field that holds it.
# An allowlist: hasheous also proxies GiantBomb, Steam, GOG, the Epic Game
# Store, Wikipedia and SteamGridDB, and RomM's update endpoint has a field
# for none of those. A source not named here is carried in the raw blob
# and nowhere else, which is the honest place for it.
SOURCE_FIELDS: dict[str, str] = {
    "IGDB": "igdb_id",
    "TheGamesDb": "tgdb_id",
    "RetroAchievements": "ra_id",
}

# Fields RomM stores and fields RomM *acts on*, which is not the same
# thing and cost this plugin a 500 to learn.
#
# RomM 4.9.2's `update_rom` re-fetches from a provider whenever that
# provider's id **changes** (`backend/endpoints/roms/__init__.py`, the
# "Fetch metadata from external sources" block): flashpoint, launchbox,
# ra, moby, ss and igdb each trigger a `get_rom_by_id` against the real
# service. Writing `ra_id` to a RomM with no RetroAchievements key does
# not degrade -- it raises `TypeError: Invalid variable type: value
# should be str, int or float, got None` out of `yarl` when the auth
# middleware appends a `None` key to the query, and the whole PUT answers
# 500. Verified against RomM 4.9.2 on 2026-07-29.
#
# `hasheous_id` and `tgdb_id` are in neither the fetch block nor any
# handler: RomM stores them and stops. So they are always safe, and the
# two that make RomM go out to a keyed service are behind
# `cross_provider_ids`.
ALWAYS_SAFE_IDS = frozenset({"hasheous_id", "tgdb_id"})

# The only mapping status whose id means anything. `NotMapped` rows carry
# a scheduled `nextSearch` and no id; `MappedWithErrors` means hasheous
# matched something and then failed to fetch it, so the id is real but the
# record behind it is not known to be.
MAPPED = "Mapped"

# RomM parses most provider ids as integers and its validator permits only
# `[A-Za-z0-9._-]`, at most 64 characters. Hasheous returns ids as JSON
# strings; anything that is not a plain identifier is dropped rather than
# posted and refused downstream.
_MAX_ID_CHARS = 64
_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


class NoMatch(Exception):
    """Hasheous does not know this dump, and the message says what was asked."""


class LookupFailed(Exception):
    """The service answered, but not with an identification."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        candidates = offered(dict(rom.extra))
        if not candidates:
            raise NoMatch(
                f"rom {rom.rom_id} ({rom.filename or rom.name!r}) carries no "
                f"hash, and hasheous is keyed by hash alone -- its GET API has "
                f"no name search, and this plugin will not invent one by "
                f"searching for the title and taking the top hit. Pass the "
                f"dump's hash with --source-id md5:<hex> (or sha1:, sha256:, "
                f"crc:, or a bare digest)."
            )

        usable = [(k, h) for k, h in candidates if k != "crc" or self._allow_crc32()]
        if not usable:
            raise NoMatch(
                f"rom {rom.rom_id} offers only a CRC-32 ({candidates[0][1]}), "
                f"and a 32-bit checksum over hasheous's corpus collides often "
                f"enough that a match cannot be trusted on its own. Set "
                f"allow_crc32 = true to accept it -- the platform cross-check "
                f"still has to agree -- or pass an MD5, SHA-1 or SHA-256."
            )

        # Strongest first. A 404 on the strongest hash is a real "not
        # known", but a weaker hash may still be indexed for the same
        # dump, so the ladder is walked rather than stopped at the first
        # miss.
        misses: list[str] = []
        for kind, digest in usable:
            payload = self._lookup(kind, digest)
            if payload is None:
                misses.append(f"{kind}:{digest}")
                continue
            return self._patch(rom, payload, kind, digest)

        raise NoMatch(
            f"hasheous has no signature for rom {rom.rom_id} "
            f"({rom.name or rom.filename!r}). Tried: {', '.join(misses)}."
        )

    # -- configuration ---------------------------------------------------

    def _allow_crc32(self) -> bool:
        return bool(self.ctx.config.get("allow_crc32", False))

    def _verify_platform(self) -> bool:
        return bool(self.ctx.config.get("verify_platform", True))

    def _set_name(self) -> bool:
        return bool(self.ctx.config.get("set_name", True))

    def _raw_metadata(self) -> bool:
        return bool(self.ctx.config.get("raw_metadata", True))

    def _cross_provider_ids(self) -> bool:
        return bool(self.ctx.config.get("cross_provider_ids", False))

    # -- the network -----------------------------------------------------

    def _lookup(self, kind: str, digest: str) -> dict | None:
        """The hash lookup. `None` means "hasheous does not know this one"."""
        url = f"{BASE}{kind}/{quote(digest, safe='')}"
        try:
            response = self.ctx.http.get(url)
        except RuntimeError as exc:
            # The broker reports its own refusals -- an allowlist block, a
            # response over the host's 4 MiB ceiling, a timeout -- as a
            # RuntimeError carrying the host's message. Passing it through
            # named is more use than a bare traceback.
            raise LookupFailed(
                f"the host could not fetch {url!r} on this plugin's behalf: {exc}"
            ) from exc

        if response.status_code == 404:
            return None
        if response.status_code == 400:
            raise LookupFailed(
                f"hasheous rejected the {kind} lookup for {digest!r} as invalid "
                f"(HTTP 400). Zero-byte hashes are refused by the service."
            )
        if response.status_code != 200:
            raise LookupFailed(
                f"hasheous answered HTTP {response.status_code} for the {kind} "
                f"lookup of {digest!r}; nothing was proposed for this rom"
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise LookupFailed(
                f"hasheous's answer for {kind}:{digest} was not JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LookupFailed(
                f"hasheous's answer for {kind}:{digest} was a "
                f"{type(payload).__name__}, not an object"
            )
        return payload

    # -- turning the answer into a patch ---------------------------------

    def _patch(self, rom: RomRef, payload: dict, kind: str, digest: str):
        if self._verify_platform():
            self._check_platform(rom, payload, kind, digest)

        patch: dict = {}

        if self._set_name():
            name = payload.get("name")
            if isinstance(name, str) and name.strip():
                patch["name"] = name.strip()

        provider_ids: dict[str, int | str] = {}

        object_id = payload.get("id")
        if isinstance(object_id, int) and not isinstance(object_id, bool):
            provider_ids["hasheous_id"] = object_id
        elif isinstance(object_id, str) and _is_identifier(object_id):
            provider_ids["hasheous_id"] = object_id

        provider_ids.update(self._mapped_ids(payload.get("metadata")))
        if provider_ids:
            patch["provider_ids"] = provider_ids

        if self._raw_metadata():
            blob = self._raw(payload)
            if blob is not None:
                patch["raw_metadata"] = {"raw_hasheous_metadata": blob}

        # A signature with no name, no id and no mapping is a real answer
        # about a dump hasheous has seen but not identified. An empty
        # patch is how RPP v1 says "I know nothing about this rom", and
        # the host then leaves RomM alone -- which is right.
        return MetadataPatch(**patch)

    def _check_platform(self, rom: RomRef, payload: dict, kind: str, digest: str):
        """Refuse an answer about a different console.

        This is what makes a CRC-32 collision survivable. `expected_keys`
        raises "needs mapping" for a slug the table does not cover rather
        than waving it through.

        **The signature's own system name is preferred over the platform
        object's.** `signature.game.system` is set by hasheous's signature
        parser straight from the DAT header -- `NoIntrosParser.cs` does
        `gameObject.System = noIntrosObject.Name` -- so it is guaranteed
        to be in the DAT vocabulary this module's table is built from.
        `platform.name` is a curated DataObject that an administrator can
        rename, and hasheous only seeds it from the DAT header
        (`HashLookup2.cs`, `Name = discoveredSignature.Game.System`) when
        it has to create one. Checking the derived value against the
        derived vocabulary, and falling back to the curated one only when
        there is no signature, keeps the comparison on solid ground.
        """
        wanted = expected_keys(rom.platform)
        names = [n for n in self._platform_names(payload) if n.strip()]
        if not names:
            raise LookupFailed(
                f"hasheous identified {kind}:{digest} but named no platform for "
                f"it, so the match cannot be checked against RomM's "
                f"{rom.platform!r}. Set verify_platform = false to accept it."
            )
        if not any(key(name) in wanted for name in names):
            raise LookupFailed(
                f"hasheous says {kind}:{digest} is "
                f"{', '.join(repr(n) for n in names)}, but rom {rom.rom_id} is "
                f"filed in RomM under {rom.platform!r}. Nothing was written: on "
                f"a CRC-32 this is what a collision looks like, and on a strong "
                f"hash it means the rom or the hash is not the one you meant. "
                f"Set verify_platform = false to override."
            )

    @staticmethod
    def _platform_names(payload: dict) -> list[str]:
        """Every console name in one answer, DAT-derived first."""
        names: list[str] = []
        signature = payload.get("signature")
        if isinstance(signature, dict):
            game = signature.get("game")
            if isinstance(game, dict) and isinstance(game.get("system"), str):
                names.append(game["system"])
        for results in (payload.get("signatures") or {}).values():
            for result in results or []:
                if not isinstance(result, dict):
                    continue
                game = result.get("game")
                if isinstance(game, dict) and isinstance(game.get("system"), str):
                    names.append(game["system"])
        platform = payload.get("platform")
        if isinstance(platform, dict) and isinstance(platform.get("name"), str):
            names.append(platform["name"])
        # Order kept, duplicates dropped: the message names each console
        # once even when six DATs agree about it.
        return list(dict.fromkeys(names))

    def _mapped_ids(self, metadata) -> dict[str, int | str]:
        """Other providers' ids, but only the ones hasheous calls `Mapped`.

        And only the ones RomM merely *stores*, unless the operator has
        set `cross_provider_ids`. See `ALWAYS_SAFE_IDS`.
        """
        cross = self._cross_provider_ids()
        out: dict[str, int | str] = {}
        for entry in metadata or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("status") != MAPPED:
                continue
            field = SOURCE_FIELDS.get(entry.get("source"))
            if field is None or field in out:
                continue
            if field not in ALWAYS_SAFE_IDS and not cross:
                continue
            value = entry.get("id")
            if not isinstance(value, str) or not _is_identifier(value):
                continue
            out[field] = value
        return out

    @staticmethod
    def _raw(payload: dict) -> dict | None:
        """The whole answer, kept as `raw_hasheous_metadata`.

        Dropped rather than truncated when it does not fit: RPP v1 caps a
        raw blob at 256 KiB and a half-serialised JSON document is worse
        than none. The signature lists are the bulky part -- a popular
        game carries every DAT's spelling of every one of its dumps -- and
        `signatures` is dropped first, because the identity this plugin
        was asked for lives in `id`, `name`, `platform` and `metadata`.
        """
        try:
            if len(json.dumps(payload)) <= _MAX_RAW_CHARS:
                return payload
        except (TypeError, ValueError):
            return None
        trimmed = {k: v for k, v in payload.items() if k not in ("signatures",)}
        try:
            if len(json.dumps(trimmed)) <= _MAX_RAW_CHARS:
                return trimmed
        except (TypeError, ValueError):
            return None
        return None


# RPP v1's per-field ceiling, restated here so the plugin refuses before
# the host does and can say which field was too big.
_MAX_RAW_CHARS = 256 * 1024


def _is_identifier(value: str) -> bool:
    return bool(value) and len(value) <= _MAX_ID_CHARS and not (set(value) - _ID_CHARS)
