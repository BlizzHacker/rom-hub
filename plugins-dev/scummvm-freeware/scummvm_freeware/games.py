"""The twelve games, and who made each one free.

**This table is the whole safety model of the plugin**, and it is worth
being blunt about why it exists rather than a walk of the tree.

`https://downloads.scummvm.org/frs/extras/` is one directory per *title*,
and only some of those titles are free games. The rest are add-ons for
games that are still very much for sale: `Blade Runner/` holds subtitles,
`Toonstruck/` holds cutscene subtitles, `Elvira 2/` holds digital sound
samples, `Broken Sword I and II/` holds a subtitle pack. A plugin that
walked `extras/` and offered what it found would offer those too -- and
`Blade_Runner_Subtitles-v9.zip` filed in a library as a ROM named "Blade
Runner" is both wrong and the kind of wrong nobody notices for a year.

So the directories below are an **allowlist**, and a directory not in it
is unreachable: search never looks at it and the importer refuses a
`source_id` naming it. Adding a row is a deliberate act that requires
knowing who released the game and under what.

`freed_by` is not decoration either. Every entry here is freeware because
a specific rights holder said so, and this column is the plugin's answer
to "why may I have this?" -- it is surfaced on every search result, so
the claim travels with the game instead of living only in a README.

Read from `https://www.scummvm.org/games/` on 2026-07-29, which is the
ScummVM project's own published list of the games it distributes; that
page's own description calls them "freeware games". The download host
carries no robots.txt at all (HTTP 404 for `/robots.txt`, verified the
same day), and this plugin never requests anything from `www.scummvm.org`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    #: The directory under `/frs/extras/`. Exact, including the trailing
    #: underscore Dráscula's carries -- the server has no redirect for a
    #: near miss, it 404s.
    directory: str
    #: What a library should call it. The directory name is a file path,
    #: not a title: `Drascula_ The Vampire Strikes Back` is not a name.
    title: str
    #: The RomM platform slug. Stated per row rather than as one constant
    #: for the module, even though all twelve are `scummvm` today: the
    #: platform is a fact about the *release*, and a row added later for a
    #: DOS-only or Amiga-only freeware drop must be able to say so
    #: without anybody having to notice that a shared default stopped
    #: being true. RomM carries `scummvm` as its own platform (verified
    #: against RomM 4.9.2's `GET /api/platforms/supported`), which is
    #: what these downloads are: ScummVM-ready game data, not dumps of
    #: the original media.
    platform: str
    #: Who made it free, and when. Shown on every result.
    freed_by: str


#: Keyed by a slug an operator can type. Ordered as ScummVM lists them.
GAMES: dict[str, Game] = {
    "beneath-a-steel-sky": Game(
        directory="Beneath a Steel Sky",
        title="Beneath a Steel Sky",
        platform="scummvm",
        freed_by="Revolution Software, which holds the rights, released it as freeware in 2003",
    ),
    "broken-sword-2-5": Game(
        directory="Broken Sword 2.5",
        title="Broken Sword 2.5: The Return of the Templars",
        platform="scummvm",
        freed_by="a free fan-made game by Mindfactory, distributed at no charge by its own authors",
    ),
    "drascula": Game(
        directory="Drascula_ The Vampire Strikes Back",
        title="Dráscula: The Vampire Strikes Back",
        platform="scummvm",
        freed_by="Alcachofa Soft, which holds the rights, released it as freeware",
    ),
    "dreamweb": Game(
        directory="Dreamweb",
        title="DreamWeb",
        platform="scummvm",
        freed_by="Creative Reality's Neil Dodwell and David Dew, the authors, released it as freeware in 2012",
    ),
    "flight-of-the-amazon-queen": Game(
        directory="Flight of the Amazon Queen",
        title="Flight of the Amazon Queen",
        platform="scummvm",
        freed_by="John Passfield and Steve Stamatiadis, the authors, released it as freeware in 2004",
    ),
    "god-of-thunder": Game(
        directory="God of Thunder",
        title="God of Thunder",
        platform="scummvm",
        freed_by="Ron Davis, the author, released it as freeware",
    ),
    "griffon-legend": Game(
        directory="Griffon Legend",
        title="The Griffon Legend",
        platform="scummvm",
        freed_by="Daniel 'Syn9' Kennedy, the author, released it as freeware",
    ),
    "lure-of-the-temptress": Game(
        directory="Lure of the Temptress",
        title="Lure of the Temptress",
        platform="scummvm",
        freed_by="Revolution Software, which holds the rights, released it as freeware in 2003",
    ),
    "mystery-house": Game(
        directory="Mystery House",
        title="Hi-Res Adventure #1: Mystery House",
        platform="scummvm",
        freed_by="Ken and Roberta Williams placed it in the public domain in 1987",
    ),
    "nippon-safes": Game(
        directory="Nippon Safes",
        title="Nippon Safes, Inc.",
        platform="scummvm",
        freed_by="Dynabyte, which holds the rights, released it as freeware",
    ),
    "sfinx": Game(
        directory="Sfinx",
        title="Sfinx",
        platform="scummvm",
        freed_by="L.K. Avalon, which holds the rights, released it as freeware",
    ),
    "soltys": Game(
        directory="Soltys",
        title="Sołtys",
        platform="scummvm",
        freed_by="L.K. Avalon, which holds the rights, released it as freeware",
    ),
}

#: Directory -> slug, for resolving a `source_id`. A plain inversion, so
#: it cannot fall out of step when a row is added above.
BY_DIRECTORY: dict[str, str] = {game.directory: slug for slug, game in GAMES.items()}


def game_for(slug: str) -> Game | None:
    """The game with this slug, or None."""
    if not isinstance(slug, str):
        return None
    return GAMES.get(slug.strip().lower())


def slug_for_directory(directory: str) -> str | None:
    """The slug for a `/frs/extras/` directory, or None.

    None is what makes the allowlist an allowlist: a directory that is not
    one of the twelve has no slug, so no code path below can build a URL
    into it.
    """
    if not isinstance(directory, str):
        return None
    return BY_DIRECTORY.get(directory.strip())
