"""RetroAchievements `metadata`: a game identified by its hash, or nothing.

    RomRef -> a hash + a console -> API_GetGameList.php -> MetadataPatch

One request, one exact comparison, and no second guess. That last part is
the design:

**A hash miss is a miss.** RetroAchievements identifies a game by a hash,
and a hash either matches or it does not. Falling back to "the RA game
whose title looks most like this rom's name" would attach another game's
`ra_id` to the operator's library -- and an `ra_id` is not decoration,
it is what an achievements client will trust later. So a miss raises, and
the message distinguishes the two reasons a miss happens (see
`consoles.WHOLE_FILE_MD5`: for most consoles RA's hash is *not* the file's
md5).

**The key is a `secret`, and the README says exactly what that buys.**
`api_key` is declared `type = "secret"`, so the Hub keeps it out of its
plain config, redacts it from every command's output, and hands it to
this process in the `init` frame. What the storage itself protects
depends on the host -- an OS keyring, or an encrypted file whose key may
be sitting next to it -- and the README states the weak case in those
words rather than implying otherwise. An operator who believes a
credential is protected treats it differently from one who knows how
much.

**A missing key fails before anything else happens.** Not as a 401 out of
RA, not as a KeyError -- as a sentence naming the config key and where to
get a value for it.

One call per enrich, deliberately. `API_GetGameList.php` carries `Title`,
`ID` and `Hashes` together, so `API_GetGame.php` would add a second
request for fields RPP v1 has nowhere to put -- there is no
`raw_ra_metadata` among its eight `raw_*_metadata` fields, and writing
RA's payload into one belonging to another provider would be a lie in the
database. RetroAchievements also asks in its own documentation that this
endpoint be cached rather than hammered; one request is the least this
plugin can do and still work.
"""

import json
import re

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .consoles import (  # noqa: F401  (NeedsMapping re-exported)
    NeedsMapping,
    console_for,
    hashes_whole_file,
)

API = "https://retroachievements.org/API/API_GetGameList.php"

# Where a hash may arrive. `RomRef.extra` is whatever the host put there;
# `source_id` is what the CLI's --source-id fills in, and is the route that
# works today.
HASH_KEYS = ("ra_hash", "md5", "md5_hash", "hash", "source_id")

_MD5_RE = re.compile(r"\A[0-9a-fA-F]{32}\Z")

# RA answers a bad key with 401, but a good key and a bad console with a
# 200 and an error object, so both shapes have to be handled.
_ERROR_KEYS = ("Error", "error", "message")


class NotConfigured(Exception):
    """The plugin cannot run until the operator sets something."""


class NoMatch(Exception):
    """No RetroAchievements game carries this hash."""


class ApiFailed(Exception):
    """RetroAchievements answered, but not with a game list."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        api_key = self._api_key()
        console_id = console_for(rom.platform)
        digest = self._hash(rom, console_id)

        game = self._lookup(console_id, digest, api_key)
        if game is None:
            raise NoMatch(self._miss(rom, console_id, digest))

        patch: dict = {"provider_ids": {"ra_id": game["id"]}}
        if self._set_name() and game["title"]:
            patch["name"] = game["title"]
        return MetadataPatch(**patch)

    # -- configuration ---------------------------------------------------

    def _api_key(self) -> str:
        key = str(self.ctx.config.get("api_key") or "").strip()
        if not key:
            raise NotConfigured(
                "retroachievements needs a RetroAchievements web API key and "
                "none is configured. Get one from your RA profile under "
                "Settings -> Keys (it is per-account, read-only and can be "
                "reset there at any time), then store it with `rom-hub plugin "
                "secret set retroachievements api_key`, which prompts rather "
                "than taking it as an argument. `api_key` is a `secret`, so "
                "the Hub keeps it out of its plain config and redacts it from "
                "command output; run `rom-hub plugin secret list` to see what "
                "the store on your host actually protects"
            )
        return key

    def _set_name(self) -> bool:
        return bool(self.ctx.config.get("set_name", True))

    def _only_with_achievements(self) -> bool:
        return bool(self.ctx.config.get("only_with_achievements", True))

    def _username(self) -> str:
        return str(self.ctx.config.get("username") or "").strip()

    # -- the hash --------------------------------------------------------

    @staticmethod
    def _hash(rom: RomRef, console_id: int) -> str:
        for key in HASH_KEYS:
            value = (rom.extra.get(key) or "").strip()
            if not value:
                continue
            if not _MD5_RE.match(value):
                raise NotConfigured(
                    f"{key}={value!r} is not a RetroAchievements hash. RA "
                    f"hashes are 32 hex characters; this is "
                    f"{len(value)} character(s). Nothing was looked up"
                )
            return value.lower()

        detail = (
            "RomM's own md5 is the right value for this console"
            if hashes_whole_file(console_id)
            else "note that for this console RA's hash is NOT the file's md5 "
            "-- see the plugin README"
        )
        raise NotConfigured(
            f"rom {rom.rom_id} ({rom.filename or rom.name!r}) carries no hash, "
            f"and RetroAchievements identifies games by hash alone. Read it "
            f"from RomM -- `GET /api/roms/{rom.rom_id}` returns `md5_hash` -- "
            f"and pass it with --source-id; {detail}"
        )

    # -- the request -----------------------------------------------------

    def _lookup(self, console_id: int, digest: str, api_key: str) -> dict | None:
        params = {
            "i": str(console_id),
            "h": "1",
            "f": "1" if self._only_with_achievements() else "0",
            "y": api_key,
        }
        username = self._username()
        if username:
            # RA's own client always sends `z` alongside `y`; the API docs
            # mark only `y` required. Sent when configured, omitted when not.
            params["z"] = username

        try:
            response = self.ctx.http.get(API, params=params)
        except RuntimeError as exc:
            # The host refuses a response over 4 MiB, and a console with
            # thousands of games and every hash is exactly what hits that.
            raise ApiFailed(
                f"the game list for console {console_id} could not be "
                f"retrieved: {exc}. If the Hub refused it for size, set "
                f"`only_with_achievements = true` to ask RA for the smaller "
                f"list"
            ) from exc

        if response.status_code == 401:
            raise NotConfigured(
                "RetroAchievements rejected the configured `api_key` (HTTP "
                "401). Check it against your RA profile's Settings -> Keys"
            )
        if response.status_code != 200:
            raise ApiFailed(
                f"RetroAchievements answered HTTP {response.status_code} for "
                f"the console {console_id} game list"
            )

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            # Rate limiting and maintenance both arrive as 200 + HTML.
            raise ApiFailed(
                f"RetroAchievements' console {console_id} game list was not "
                f"JSON: {exc}"
            ) from exc

        if isinstance(payload, dict):
            for key in _ERROR_KEYS:
                if payload.get(key):
                    raise ApiFailed(
                        f"RetroAchievements refused the console {console_id} "
                        f"game list: {payload[key]}"
                    )
            raise ApiFailed(
                f"RetroAchievements' console {console_id} game list was an "
                f"object, not a list of games"
            )
        if not isinstance(payload, list):
            raise ApiFailed(
                f"RetroAchievements' console {console_id} game list was "
                f"{type(payload).__name__}, not a list"
            )

        for entry in payload:
            if not isinstance(entry, dict):
                continue
            hashes = entry.get("Hashes")
            if not isinstance(hashes, list):
                continue
            if any(isinstance(h, str) and h.strip().lower() == digest for h in hashes):
                return self._game(entry)
        return None

    @staticmethod
    def _game(entry: dict) -> dict:
        """`ID` arrives as a JSON *string* on this endpoint.

        RA's own client casts it -- `serializeProperties(..., {
        shouldCastToNumbers: ["ID", "ConsoleID"] })` in
        `api-js/src/console/getGameList.ts` -- and a `ra_id` posted to RomM
        as `"4247"` rather than `4247` is a different value in a column
        RomM parses as an integer.
        """
        raw = entry.get("ID")
        try:
            game_id = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ApiFailed(
                f"a RetroAchievements game matched the hash but its ID was "
                f"{raw!r}, which is not an id"
            ) from None
        if game_id <= 0:
            raise ApiFailed(
                f"a RetroAchievements game matched the hash but its ID was "
                f"{game_id}"
            )
        title = entry.get("Title")
        return {
            "id": game_id,
            "title": title.strip() if isinstance(title, str) else "",
        }

    # -- the miss --------------------------------------------------------

    def _miss(self, rom: RomRef, console_id: int, digest: str) -> str:
        scope = (
            "games with achievements"
            if self._only_with_achievements()
            else "all games"
        )
        if hashes_whole_file(console_id):
            why = (
                "For this console RetroAchievements hashes the whole file, so "
                "RomM's md5 is the right value and this really is a game RA "
                "does not carry"
            )
            if self._only_with_achievements():
                why += (
                    " -- or one it carries without achievements; set "
                    "`only_with_achievements = false` to include those"
                )
            why += "."
        else:
            why = (
                "For this console RetroAchievements does NOT hash the whole "
                "file -- rcheevos skips a header, transforms the data, or "
                "hashes an executable inside a disc image -- so RomM's md5 "
                "will never match no matter how well known the game is. See "
                "the plugin README."
            )
        return (
            f"no RetroAchievements game on console {console_id} carries the "
            f"hash {digest} (searched {scope}). {why} Nothing was written to "
            f"RomM: this plugin will not fall back to matching rom "
            f"{rom.rom_id}'s title, because a wrong ra_id is a wrong id that "
            f"an achievements client will believe later"
        )
