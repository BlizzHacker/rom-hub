"""A parser for the clrmamepro DAT dialect libretro's `metadat/` uses.

    clrmamepro (
        name "Nintendo - Game Boy"
        version "2026.05.02"
    )

    game (
        name "Tetris (World) (Rev 1)"
        region "World"
        rom ( name "Tetris (World) (Rev 1).gb" size 32768 crc 46DF91AD
              md5 982ED5D2B12A0377EB14BCDC4123744E sha1 74591CC9... )
    )

**The two `name`s are different things, and keeping them apart is the
whole reason this file exists.** `game.name` is a catalogue title;
`rom.name` is a filename. `libretro-thumbnails` documented at length why
writing the second into a library as a game's name is harmful, and this
parser makes the distinction structural rather than a matter of care --
`Game.title` and `Game.roms[i]["name"]` cannot be confused for one
another, and a test pins that the filename is never what gets written.

They really do differ. In the Game Boy set, the game titled
`14 Juillet (World) (Fr)` has a rom named
`14 Juillet (World) (Fr) (Aftermarket) (Unl).gb`.

**Tokenising rather than pattern-matching per line.** A `rom (...)` entry
can wrap, a value can contain parentheses (`Tetris (World) (Rev 1)`), and
a game can carry several roms. A regex per line gets the easy 95% and
silently drops the rest; the tokeniser handles all of it and is about
thirty lines.

**There is a second shape, and it is the same dialect.** `metadat/`
carries far more than `no-intro/` and `redump/`: `genre/`, `developer/`,
`publisher/`, `releaseyear/`, `franchise/` and `maxusers/` are the same
files annotated one fact at a time, and their entries look like

    game (
        comment "10-Pin Bowling (USA)"
        genre "Sports"
        rom ( crc 9A024415 )
    )

-- a title under `comment` rather than `name`, one fact, and a CRC-32 to
join on. `Game.attributes` keeps whatever scalar keys a block had, so
this parser reads all of those directories without a field per
possibility, and the title falls back to `comment` because requiring
`name` dropped every entry in every one of them.
"""

from dataclasses import dataclass, field

# Keys inside a `rom (...)` that this plugin reads. Others -- `size`,
# `status`, `flags` -- are kept as strings by the parser and simply not
# consulted here.
HASH_KEYS = ("crc", "md5", "sha1")


class DatError(Exception):
    """The text handed to the parser is not a DAT."""


@dataclass
class Game:
    """One `game (...)` block.

    `title` is the catalogue name. `roms` are the files it is made of,
    each a plain dict of the keys the DAT gave. Nothing here promotes a
    rom's `name` to the game's.

    `attributes` is every other scalar key the block carried, verbatim.
    The two named fields above are promoted because matching depends on
    them; the rest are kept as data because which keys exist depends
    entirely on which directory under `metadat/` the file came from --
    `genre/` files carry `genre`, `maxusers/` files carry `users`, and a
    parser with a field per possibility would need editing every time
    libretro adds a directory.
    """

    title: str
    region: str = ""
    serial: str = ""
    roms: list[dict] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)


def _tokens(text: str):
    """`(`, `)`, quoted strings and bare words. Comments start with `#`."""
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char in " \t\r\n":
            i += 1
        elif char == "#":
            end = text.find("\n", i)
            i = length if end < 0 else end + 1
        elif char in "()":
            yield char
            i += 1
        elif char == '"':
            end = text.find('"', i + 1)
            if end < 0:
                # An unterminated quote at EOF. Yield what there is rather
                # than losing the last entry to a truncated download.
                yield text[i + 1 :]
                return
            yield text[i + 1 : end]
            i = end + 1
        else:
            start = i
            while i < length and text[i] not in ' \t\r\n()"':
                i += 1
            yield text[start:i]


def _block(tokens) -> dict:
    """The body of one `( ... )`, as `{key: value-or-list-of-dicts}`."""
    out: dict = {}
    while True:
        try:
            token = next(tokens)
        except StopIteration:
            return out
        if token == ")":
            return out
        if token == "(":
            # A bare parenthesised group with no key. Not valid in these
            # DATs; consumed rather than crashing on a malformed file.
            _block(tokens)
            continue
        key = token
        try:
            value = next(tokens)
        except StopIteration:
            return out
        if value == "(":
            out.setdefault(key + "s" if key == "rom" else key, [])
            nested = _block(tokens)
            if key == "rom":
                out.setdefault("roms", []).append(nested)
            else:
                out[key] = nested
        elif value == ")":
            out[key] = ""
            return out
        else:
            out.setdefault(key, value)
    return out


def parse(text: str) -> tuple[dict, list[Game]]:
    """`(header, games)` for one DAT.

    The header is the `clrmamepro (...)` block, whose `name` is the
    system as the DAT itself spells it -- the same string hasheous stores
    as a signature's `system`.
    """
    tokens = _tokens(text)
    header: dict = {}
    games: list[Game] = []

    while True:
        try:
            token = next(tokens)
        except StopIteration:
            break
        if token in ("(", ")"):
            continue
        try:
            following = next(tokens)
        except StopIteration:
            break
        if following != "(":
            continue
        body = _block(tokens)
        if token == "clrmamepro":
            header = body
        elif token == "game":
            # `name` in the catalogue sets; `comment` in the annotation
            # sets under `metadat/genre/`, `metadat/developer/` and the
            # rest, whose entries look like
            #
            #     game ( comment "10-Pin Bowling (USA)"
            #            genre "Sports"
            #            rom ( crc 9A024415 ) )
            #
            # -- a title, one fact, and a CRC-32 to join on, with no
            # `name` anywhere. Requiring `name` silently dropped every
            # entry in every one of those files.
            title = body.get("name") or body.get("comment")
            if isinstance(title, str) and title:
                games.append(
                    Game(
                        title=title,
                        region=body.get("region") or "",
                        serial=body.get("serial") or "",
                        roms=[r for r in body.get("roms", []) if isinstance(r, dict)],
                        attributes={
                            key: value
                            for key, value in body.items()
                            if isinstance(value, str) and value
                        },
                    )
                )

    if not header and not games:
        raise DatError(
            "this is not a clrmamepro DAT: it has neither a clrmamepro "
            "header nor a single game entry"
        )
    return header, games


def index_by_hash(games: list[Game]) -> dict[tuple[str, str], list[Game]]:
    """`(kind, UPPERCASE hex) -> games`. A dump can appear in two entries."""
    out: dict[tuple[str, str], list[Game]] = {}
    for game in games:
        for rom in game.roms:
            for kind in HASH_KEYS:
                digest = rom.get(kind)
                if isinstance(digest, str) and digest:
                    out.setdefault((kind, digest.upper()), []).append(game)
    return out


def index_by_filename(games: list[Game]) -> dict[str, list[Game]]:
    """`UPPERCASE filename -> games`, with and without the extension.

    Exact keys only. Nothing is normalised beyond case and the trailing
    extension, so `Tetris` cannot reach `Tetris 2`.
    """
    out: dict[str, list[Game]] = {}
    for game in games:
        for rom in game.roms:
            name = rom.get("name")
            if not isinstance(name, str) or not name:
                continue
            out.setdefault(name.upper(), []).append(game)
            stem = name.rsplit(".", 1)[0] if "." in name else name
            if stem and stem != name:
                out.setdefault(stem.upper(), []).append(game)
        # The catalogue title is a key too: a library that already carries
        # the DAT's own game name should meet it.
        out.setdefault(game.title.upper(), []).append(game)
    return out
