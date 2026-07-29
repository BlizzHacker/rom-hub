"""libretro-database `metadata`: the catalogue name for a dump.

    RomRef -> the DATs for its platform -> a game entry, by hash or by
              exact filename -> that entry's `game (name ...)`

`libretro/libretro-database` is the DAT corpus RetroArch's playlist
scanner is built on: `metadat/no-intro/` and `metadat/redump/` carry the
No-Intro and Redump catalogues as clrmamepro files, keyed by CRC-32, MD5
and SHA-1, and freely redistributable.

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

**One DAT is fetched per lookup, and they are large.** No caching is
possible: a plugin subprocess is started per command and dies with it,
and `PluginContext` offers `config` and `http` and no storage. The
biggest files this plugin will ask for are `Sony - PlayStation 2` at
4,060,828 bytes and `Nintendo - Nintendo Entertainment System` at
3,307,672 -- both under `ctx.http`'s 4 MiB ceiling, the first not by
much. If Redump's PS2 set grows past it the fetch fails loudly with the
host's own size message rather than silently truncating, and the refusal
below says so.
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
            return self._patch(matches[0])

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

    def _patch(self, game: Game) -> MetadataPatch:
        patch: dict = {}
        if bool(self.ctx.config.get("set_name", True)):
            patch["name"] = game.title
        # No artwork: these are catalogues, and they contain no images.
        # No `libretro_id`: RomM's own handler defines that field as the
        # SHA-1 of a libretro *thumbnail* filename, which a DAT entry is
        # not. See this module's docstring.
        return MetadataPatch(**patch)
