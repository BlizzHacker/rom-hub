"""libretro-database `metadata`: the catalogue entry for a dump.

    RomRef -> the DATs for its platform -> a game entry, by hash or by
              exact filename -> that entry's `game (name ...)`, its region
              and serial, and one fact per `metadat/` annotation set,
              joined to it by CRC-32

`libretro/libretro-database` is the DAT corpus RetroArch's playlist
scanner is built on: `metadat/no-intro/` and `metadat/redump/` carry the
No-Intro and Redump catalogues as clrmamepro files, keyed by CRC-32, MD5
and SHA-1, and freely redistributable.

**`metadat/` holds far more than those two directories**, and this plugin
read only them. `genre/`, `developer/`, `publisher/`, `releaseyear/`,
`releasemonth/`, `franchise/`, `maxusers/`, `esrb/` and `serial/` are the
same dumps annotated one fact at a time and joined by CRC-32, and each is
a fraction of the size of the catalogue it annotates -- 81 KB to 333 KB
for the NES against a 3.3 MB No-Intro DAT. `details` names which of them
to read; four are read by default.

All of it lands in `summary`, because that is the only field on RomM's
update endpoint that will take any of it. A genre written this way is one
an operator can *read* and not one they can filter a library by: RomM
keeps genres and companies in a `metadatum` sub-object populated by its
own configured providers, with no form field that reaches it. README.md
says which facts stop there.

**It resolves a name, and the name it resolves is not a filename.** That
distinction is the entire design, and it is the mistake
`libretro-thumbnails` documented rather than made. A DAT entry has two
`name`s:

    game (
        name "14 Juillet (World) (Fr)"                      <- the title
        rom ( name "14 Juillet (World) (Fr) (Aftermarket) (Unl).gb" ... )
    )                                                       ^ the filename

The second is a dump's file on disk, complete with the dump-status tags
that belong to the *file* rather than to the game. This plugin writes the
first and never the second; `clrmamepro.Game` keeps them in separate
attributes so they cannot be confused, and a test pins it.

**Matching is by hash first, filename second, and never fuzzily.** A hash
identifies the dump outright. A filename is compared exactly, ignoring
case and extension only -- so `Tetris` cannot reach `Tetris 2`, and a
library whose spelling is its own gets no answer rather than somebody
else's game. Two entries matching is a refusal that names both.

**`libretro_id` is not set, and that is not an oversight.** RomM has such
a field, and it already means something specific: `libretro_id_for()` in
RomM's own `backend/handler/metadata/libretro_handler.py` defines it as
the SHA-1 of a libretro **thumbnail filename**, for its artwork-only
libretro source. A DAT entry is not a thumbnail and has no such id.
Putting a DAT-derived value there would collide with a field RomM
maintains for a different purpose, and would look like a cross-reference
while being a coincidence.

**One catalogue DAT is fetched per lookup, and they are large.** No
caching is possible: a plugin subprocess is started per command and dies
with it, and `PluginContext` offers `config` and `http` and no storage.
The biggest files this plugin will ask for are `Sony - PlayStation 2` at
4,060,828 bytes and `Nintendo - Nintendo Entertainment System` at
3,307,672 -- both under `ctx.http`'s 4 MiB ceiling, the first not by
much. If Redump's PS2 set grows past it the fetch fails loudly with the
host's own size message rather than silently truncating, and the refusal
below says so.

The annotation sets add to that and are the smaller half: four of them
against an NES catalogue is roughly a third more traffic, not a
doubling. They are fetched **after** a match, never before, so a rom the
catalogue does not carry costs exactly what it always did -- and any of
them failing costs that one fact rather than the enrich, because the name
is the valuable half and is already resolved by then. `details = []`
restores the single-request behaviour exactly.
"""

from urllib.parse import quote

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .clrmamepro import (
    DatError,
    Game,
    index_by_filename,
    index_by_hash,
    parse,
)
from .systems import KNOWN_SETS, NeedsMapping, dats_for  # noqa: F401

RAW = "https://raw.githubusercontent.com/libretro/libretro-database/"

# Hex length -> the DAT column that holds it. libretro's DATs carry no
# SHA-256, so a 64-character digest is not a key here.
HASH_BY_LENGTH: dict[int, str] = {8: "crc", 32: "md5", 40: "sha1"}

# Strongest first.
HASH_ORDER: tuple[str, ...] = ("sha1", "md5", "crc")

_HEX = frozenset("0123456789abcdefABCDEF")

# The annotation directories under `metadat/`, and which key each one's
# entries carry. Read from the live repository on 2026-08-01, not guessed:
# `metadat/maxusers/` writes `users` and `metadat/esrb/` writes
# `esrb_rating`, so a table derived from the directory names would be
# wrong for two of the eight.
#
# These are the *same* dumps as `no-intro/`, annotated one fact at a time
# and keyed by CRC-32 alone. They are also much smaller than the
# catalogues they annotate -- for the NES, 81 KB (franchise) to 333 KB
# (developer) against the 3.3 MB no-intro DAT -- which is what makes
# fetching several of them per lookup a 36% cost rather than a doubling.
DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "genre": ("genre",),
    "developer": ("developer",),
    "publisher": ("publisher",),
    "releaseyear": ("releaseyear",),
    "releasemonth": ("releasemonth",),
    "franchise": ("franchise",),
    "maxusers": ("users",),
    "esrb": ("esrb_rating",),
    "serial": ("serial",),
}

# Fetched when the operator has said nothing. Four, not nine: each is a
# separate request against a plugin with nowhere to cache, and these are
# the four that say something about the *game* rather than about the
# release. `franchise`, `maxusers`, `esrb`, `releasemonth` and `serial`
# are one word in `details` away.
DEFAULT_DETAILS = ("genre", "developer", "publisher", "releaseyear")


class NoMatch(Exception):
    """No DAT entry for this rom, and the message says what was tried."""


class FetchFailed(Exception):
    """A DAT could not be read."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        dats = dats_for(rom.platform, self._sets())
        wanted = self._keys(rom)
        if not wanted:
            raise NoMatch(
                f"rom {rom.rom_id} has neither a hash nor a filename, and "
                f"libretro's DATs are keyed by both and by nothing else"
            )

        tried = []
        for set_name, stem in dats:
            games = self._games(set_name, stem)
            matches = self._match(games, wanted)
            tried.append(f"{set_name}/{stem}")
            if not matches:
                continue
            titles = {game.title for game in matches}
            if len(titles) > 1:
                raise NoMatch(
                    f"{len(titles)} entries in {set_name}/{stem} match rom "
                    f"{rom.rom_id}: {sorted(titles)}. Nothing was written -- "
                    f"pass the dump's SHA-1 or MD5 with --source-id to say "
                    f"which one you mean."
                )
            return self._patch(matches[0], stem)

        raise NoMatch(
            f"no entry for rom {rom.rom_id} ({rom.name or rom.filename!r}) in "
            f"{', '.join(tried)}. Tried {self._describe(wanted)}. If the "
            f"library's filename is its own rather than the DAT's, pass the "
            f"dump's hash with --source-id."
        )

    # -- configuration ---------------------------------------------------

    def _sets(self) -> tuple[str, ...]:
        raw = self.ctx.config.get("sets") or ["no-intro", "redump"]
        if isinstance(raw, str):
            raw = [raw]
        chosen = tuple(str(name).strip() for name in raw if str(name).strip())
        unknown = sorted(set(chosen) - KNOWN_SETS)
        if unknown:
            raise NoMatch(
                f"`sets` names {unknown!r}, and this plugin reads "
                f"{sorted(KNOWN_SETS)}. Those are the two directories under "
                f"metadat/ whose files it has a platform table for."
            )
        return chosen or ("no-intro", "redump")

    def _ref(self) -> str:
        return str(self.ctx.config.get("ref") or "master").strip() or "master"

    # -- what identifies this rom ----------------------------------------

    def _keys(self, rom: RomRef):
        """`(hashes, filenames)` -- everything worth looking up."""
        hashes: list[tuple[str, str]] = []
        names: list[str] = []

        source_id = (rom.extra.get("source_id") or "").strip()
        if source_id:
            kind = HASH_BY_LENGTH.get(len(source_id))
            if kind and set(source_id) <= _HEX:
                hashes.append((kind, source_id.upper()))
            else:
                names.append(source_id)

        for kind in HASH_ORDER:
            digest = (rom.extra.get(kind) or "").strip()
            if digest and set(digest) <= _HEX and HASH_BY_LENGTH.get(len(digest)):
                pair = (kind, digest.upper())
                if pair not in hashes:
                    hashes.append(pair)

        if not names:
            for label in (rom.filename, rom.name):
                label = (label or "").strip()
                if label and label not in names:
                    names.append(label)

        return (hashes, names) if (hashes or names) else None

    @staticmethod
    def _describe(wanted) -> str:
        hashes, names = wanted
        parts = [f"{kind}:{digest}" for kind, digest in hashes]
        parts += [repr(name) for name in names]
        return ", ".join(parts)

    # -- the DAT ---------------------------------------------------------

    def _url(self, set_name: str, stem: str) -> str:
        return (
            RAW
            + quote(self._ref(), safe="")
            + "/metadat/"
            + quote(set_name, safe="")
            + "/"
            + quote(f"{stem}.dat")
        )

    def _games(self, set_name: str, stem: str) -> list[Game]:
        url = self._url(set_name, stem)
        try:
            response = self.ctx.http.get(url)
        except RuntimeError as exc:
            raise FetchFailed(
                f"the host could not fetch {url!r} on this plugin's behalf: "
                f"{exc}. The largest DATs are close to the Hub's 4 MiB "
                f"per-response ceiling, so a size refusal here is a real "
                f"possibility and not a bug in this plugin."
            ) from exc

        if response.status_code == 404:
            raise FetchFailed(
                f"libretro-database has no {set_name}/{stem}.dat at ref "
                f"{self._ref()!r}. The file may have been renamed upstream; "
                f"libretro_database/systems.py names it."
            )
        if response.status_code != 200:
            raise FetchFailed(
                f"raw.githubusercontent.com answered HTTP "
                f"{response.status_code} for {url!r}"
            )
        try:
            _header, games = parse(response.text)
        except DatError as exc:
            raise FetchFailed(f"{url!r} did not parse as a DAT: {exc}") from exc
        return games

    @staticmethod
    def _match(games: list[Game], wanted) -> list[Game]:
        hashes, names = wanted
        if hashes:
            by_hash = index_by_hash(games)
            for key in hashes:
                found = by_hash.get(key)
                if found:
                    return found
            # A hash was offered and this DAT does not have it. Falling
            # back to the filename here would answer a question nobody
            # asked: the operator named a specific dump.
            return []
        by_name = index_by_filename(games)
        for name in names:
            found = by_name.get(name.upper())
            if found:
                return found
            stem = name.rsplit(".", 1)[0] if "." in name else name
            found = by_name.get(stem.upper())
            if found:
                return found
        return []

    # -- the patch -------------------------------------------------------

    def _patch(self, game: Game, stem: str) -> MetadataPatch:
        patch: dict = {}
        if bool(self.ctx.config.get("set_name", True)):
            patch["name"] = game.title
        if bool(self.ctx.config.get("summary", True)):
            summary = _summary(game, self._annotations(game, stem))
            if summary:
                patch["summary"] = summary
        # No artwork: these are catalogues, and they contain no images.
        # No `libretro_id`: RomM's own handler defines that field as the
        # SHA-1 of a libretro *thumbnail* filename, which a DAT entry is
        # not. See this module's docstring.
        return MetadataPatch(**patch)

    # -- the annotation sets ---------------------------------------------

    def _annotations(self, game: Game, stem: str) -> dict[str, str]:
        """One fact per `metadat/` directory the operator asked for.

        Joined on **CRC-32**, because that is the only key the annotation
        sets carry: their `rom (...)` blocks hold `crc` and nothing else.
        That is a weaker join than the catalogue lookup, which prefers
        SHA-1 -- but it is a join within one console's file against a game
        already identified by a strong hash, not an identification, so a
        CRC collision here would have to be a collision between two games
        on the same machine whose stronger hashes already agreed.

        A directory that 404s or fails to parse contributes nothing rather
        than failing the enrich. The name is the valuable half and it is
        already resolved; losing it because `metadat/franchise/` has no
        file for this console would be absurd.
        """
        wanted = self._details()
        if not wanted:
            return {}
        crcs = {
            str(entry["crc"]).upper()
            for entry in game.roms
            if isinstance(entry.get("crc"), str) and entry["crc"]
        }
        if not crcs:
            return {}

        out: dict[str, str] = {}
        for detail in wanted:
            try:
                games = self._games(detail, stem)
            except (FetchFailed, NoMatch):
                continue
            for entry in games:
                if not any(
                    isinstance(r.get("crc"), str) and r["crc"].upper() in crcs
                    for r in entry.roms
                ):
                    continue
                for key in DETAIL_KEYS[detail]:
                    value = entry.attributes.get(key, "").strip()
                    if value:
                        out.setdefault(key, value)
                break
        return out

    def _details(self) -> tuple[str, ...]:
        raw = self.ctx.config.get("details")
        if raw is None:
            raw = DEFAULT_DETAILS
        if isinstance(raw, str):
            raw = [raw]
        chosen: list[str] = []
        for item in raw:
            name = str(item).strip()
            if not name:
                continue
            if name not in DETAIL_KEYS:
                raise NoMatch(
                    f"`details` names {name!r}, and this plugin reads "
                    f"{sorted(DETAIL_KEYS)}. Those are the directories under "
                    f"metadat/ that annotate a dump by CRC-32."
                )
            if name not in chosen:
                chosen.append(name)
        return tuple(chosen)


# -- the catalogue entry, as prose ---------------------------------------


def _summary(game: Game, details: dict[str, str]) -> str | None:
    """What the DAT knows, in the one field RomM will store it in.

    Two sources: the catalogue entry itself (region, serial) and the
    annotation sets joined to it by CRC-32 (genre, developer, publisher,
    year, and whatever else `details` asked for).

    None of it has a structured home in RomM. Its `metadatum` sub-object
    holds genres, companies, `first_release_date` and `player_count`, and
    `PUT /api/roms/{id}` has no form field that reaches any of them -- so
    a genre written here is one an operator can read on the rom page and
    not one they can filter a library by. README.md says so in those
    words.

    Absent when nothing was found, because a blank summary erases whatever
    RomM already had.
    """
    parts: list[str] = []

    developer = details.get("developer", "").strip()
    publisher = details.get("publisher", "").strip()
    if developer and publisher and developer != publisher:
        parts.append(f"Developed by {developer}, published by {publisher}.")
    elif developer:
        parts.append(f"Developed by {developer}.")
    elif publisher:
        parts.append(f"Published by {publisher}.")

    released = _released(details)
    if released:
        parts.append(f"Released {released}.")

    genre = details.get("genre", "").strip()
    if genre:
        parts.append(f"Genre: {genre}.")

    franchise = details.get("franchise", "").strip()
    if franchise:
        parts.append(f"Franchise: {franchise}.")

    users = details.get("users", "").strip()
    if users.isdigit() and int(users) > 0:
        count = int(users)
        parts.append(f"{count} player{'' if count == 1 else 's'}.")

    rating = details.get("esrb_rating", "").strip()
    if rating:
        parts.append(f"ESRB: {rating}.")

    if game.region:
        parts.append(f"Region: {game.region}.")

    # The catalogue's own serial wins over the annotation set's: they are
    # the same value, and the one on the entry that actually matched is
    # the one that belongs to this dump.
    serial = game.serial or details.get("serial", "").strip()
    if serial:
        parts.append(f"Serial: {serial}.")

    return " ".join(parts) or None


#: Month numbers as `metadat/releasemonth/` writes them -- `"8"`, not
#: `"08"` and not `"August"`.
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _released(details: dict[str, str]) -> str:
    """`1999`, or `August 1999` when the month set was asked for too.

    No day is invented. `metadat/releaseyear/` and `metadat/releasemonth/`
    are the whole of what libretro records, and a date rendered to a
    precision the source does not claim is a date somebody will later
    believe.
    """
    year = details.get("releaseyear", "").strip()
    if not year.isdigit():
        return ""
    month = details.get("releasemonth", "").strip()
    if month.isdigit() and 1 <= int(month) <= 12:
        return f"{_MONTHS[int(month) - 1]} {year}"
    return year
