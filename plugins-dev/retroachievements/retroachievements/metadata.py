"""RetroAchievements `metadata`: a game identified by its hash, or nothing.

    RomRef -> a hash + a console -> API_GetGameList.php -> id, title,
              achievement count, points, leaderboards
                                -> API_GetGame.php     -> publisher,
              developer, genre, release year, box art

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

**This plugin made one call per enrich, deliberately, and now makes two.**
The old reasoning was sound and it expired. `API_GetGame.php` returns a
publisher, a developer, a genre, a release year and a box art URL, and
none of the five had anywhere to go: RPP v1 has no `raw_ra_metadata`
among its eight `raw_*_metadata` fields, and writing RA's payload into
one belonging to another provider would be a lie in the database. A
second request that buys nothing storable is a second request for
nothing.

`MetadataPatch.summary` is what changed. RomM stores it -- measured, where
the raw blobs are accepted and dropped -- so those four facts now reach a
library, and the fifth turns this from a plugin that proposes no artwork
into one that proposes real box art. `details = false` puts it back to one
request for an operator enriching a whole library, and says in the README
what that costs.

The big response is still fetched once. `API_GetGameList.php` is every
game on a console with every hash, and it is the endpoint RA's own
documentation asks callers to cache rather than hammer; `API_GetGame` is
one game. A test pins that the game list is requested exactly once per
enrich whatever else happens.
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

# The optional second call. See `Metadata._details` for why this plugin
# refused to make one until `MetadataPatch` grew a field to hold what it
# returns.
GAME_API = "https://retroachievements.org/API/API_GetGame.php"

# Where RA's images live. `API_GetGame` returns `ImageBoxArt` as a path --
# `/Images/026365.png` -- and it has to be joined to a host.
#
# **`retroachievements.org` and not `media.retroachievements.org`,
# deliberately.** Both serve it: measured 2026-08-01, both answer 200 with
# `image/png` and identical 130,898 bytes for `/Images/026365.png`, and
# `curl -w %{num_redirects}` reports 0 for each. The main host is already
# in this plugin's `network` allowlist for the API, so using it means real
# box art arrives without widening the permission an operator approved.
# A second allowlist entry that buys the same bytes is a second allowlist
# entry for nothing.
IMAGE_BASE = "https://retroachievements.org"

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

        details = self._details(game["id"], api_key) if self._want_details() else {}

        if self._want_summary():
            summary = _summary(game, details)
            if summary:
                patch["summary"] = summary

        if self._want_artwork():
            cover = _box_art(details)
            if cover is not None:
                patch["artwork_url"] = cover
                patch["artwork_filename"] = "cover.png"

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

    def _want_summary(self) -> bool:
        return bool(self.ctx.config.get("summary", True))

    def _want_details(self) -> bool:
        """Whether to make the second call.

        Off means the summary is built from the game-list response alone
        -- achievement count, points, leaderboards and console -- which is
        still four facts more than a title. An operator running this over
        a whole library who would rather not double the request count has
        a switch, and turning it off costs the four `API_GetGame` fields
        and the box art, which is stated in the README.
        """
        return bool(self.ctx.config.get("details", True))

    def _want_artwork(self) -> bool:
        return bool(self.ctx.config.get("artwork", True))

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

        The counts alongside it were always in this response and were
        always discarded, because until `MetadataPatch` grew `summary`
        there was nowhere in RomM to put them. `NumAchievements`,
        `Points`, `NumLeaderboards` and `ConsoleName` are the four that
        say something a library reader wants: how much there is to do in
        this game and on what.
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
            "achievements": _count(entry.get("NumAchievements")),
            "points": _count(entry.get("Points")),
            "leaderboards": _count(entry.get("NumLeaderboards")),
            "console": _text(entry.get("ConsoleName")),
        }

    # -- the second call, and what it is for -----------------------------

    def _details(self, game_id: int, api_key: str) -> dict:
        """`API_GetGame.php` -- publisher, developer, genre, release year.

        **A second request, which this plugin used to refuse to make.** The
        reason it refused was specific and it has expired: those fields had
        nowhere to go. RPP v1 has no `raw_ra_metadata` among its eight raw
        blobs, writing RA's payload into another provider's would be a lie
        in the database, and RomM's `metadatum` -- where a genre belongs --
        has no form field at all. So a second call bought four values that
        would be read and dropped, against an API whose own documentation
        asks to be cached rather than hammered.

        `summary` changed that. It is one field, it is prose, and RomM
        stores it, so `Publisher`, `Developer`, `Genre` and `Released` now
        reach the library. One extra GET per enrich is a fair price for
        four facts that arrive; it was not a fair price for four that did
        not.

        The response shape is RA's own, not inferred: `api-js/src/game/
        models/game.model.ts` names every key, and `getGame.ts` documents
        a full example response. Raw JSON is PascalCase; the camelCase in
        that model is what RA's client renames it to.

        Failure here is not failure of the enrich. The hash already
        matched, the `ra_id` is already known, and losing a correct id to
        a rate limit on an optional second call would be absurd -- so this
        returns `{}` and the summary is built from what the first call
        gave.
        """
        try:
            response = self.ctx.http.get(
                GAME_API, params={"i": str(game_id), "y": api_key}
            )
        except RuntimeError:
            return {}
        if response.status_code != 200:
            return {}
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        for key in _ERROR_KEYS:
            if payload.get(key):
                return {}
        return payload

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


# -- turning two responses into one summary ------------------------------


def _summary(game: dict, details: dict) -> str | None:
    """What RA knows, in the one field RomM will store it in.

    Two sources, and the order is deliberate. The achievement counts come
    first because they are the reason somebody installs this plugin: an
    operator scanning a library wants to know which roms have a set worth
    playing and how big it is. The catalogue facts follow.

    Absent when neither source said anything, because a blank summary
    would erase whatever RomM already had.
    """
    parts: list[str] = []

    achievements = game.get("achievements") or 0
    points = game.get("points") or 0
    if achievements:
        line = f"{achievements} achievement{'' if achievements == 1 else 's'}"
        if points:
            line += f" worth {points} point{'' if points == 1 else 's'}"
        parts.append(line + " on RetroAchievements.")
    leaderboards = game.get("leaderboards") or 0
    if leaderboards:
        parts.append(
            f"{leaderboards} leaderboard{'' if leaderboards == 1 else 's'}."
        )

    developer = _text(details.get("Developer"))
    publisher = _text(details.get("Publisher"))
    if developer and publisher and developer != publisher:
        parts.append(f"Developed by {developer}, published by {publisher}.")
    elif developer:
        parts.append(f"Developed by {developer}.")
    elif publisher:
        parts.append(f"Published by {publisher}.")

    released = _text(details.get("Released"))
    if released:
        # RA stores this as free text -- "1980", "1989-06-14", "October
        # 1991" -- so it is quoted rather than parsed. Parsing it would
        # mean inventing a precision RA does not claim.
        parts.append(f"Released {released}.")

    genre = _text(details.get("Genre"))
    if genre:
        parts.append(f"Genre: {genre}.")

    console = _text(details.get("ConsoleName")) or _text(game.get("console"))
    if console:
        parts.append(f"Console: {console}.")

    return " ".join(parts) or None


def _box_art(details: dict) -> str | None:
    """The `ImageBoxArt` URL, or None.

    RA returns a path, and returns `/Images/000002.png` -- its placeholder
    -- for a game with no box art on file. That placeholder is a grey
    "no image" tile, and writing it over a library's covers would be worse
    than leaving them alone, so it is refused by name.

    Nothing is probed. Unlike libretro's thumbnails, this URL was not
    guessed from the rom's name: RA handed it back for the game whose hash
    already matched, so a 404 here would be RA contradicting itself rather
    than a spelling this plugin got wrong. The host fetches it, and a
    failure there is a real failure worth seeing.
    """
    raw = _text(details.get("ImageBoxArt"))
    if not raw.startswith("/Images/") or not raw.lower().endswith(".png"):
        return None
    if raw in _PLACEHOLDER_IMAGES:
        return None
    return IMAGE_BASE + raw


#: RA's "this game has no image" tiles, which are real 200s and real PNGs
#: and are not covers.
_PLACEHOLDER_IMAGES = frozenset({"/Images/000001.png", "/Images/000002.png"})


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value) -> int:
    """A non-negative integer, or 0.

    RA is inconsistent about whether a number arrives as a number or as a
    string -- the game-list response carries `"ID": "4247"` beside
    `"ConsoleID": 1` in the same array -- so both are accepted and
    anything else is 0 rather than an exception. A missing count is not
    worth failing an enrich whose id and title are both correct.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return 0
        return parsed if parsed >= 0 else 0
    return 0
