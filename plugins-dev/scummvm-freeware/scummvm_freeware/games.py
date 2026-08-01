"""The twenty-eight games, and who made each one free.

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

Read from `https://www.scummvm.org/games/` — the ScummVM project's own
published list of the games it distributes, whose heading is literally
"Download freeware games". Re-read on 2026-08-01, and that re-reading is
what took this table from twelve games to twenty-eight. The download host
carries no robots.txt at all (HTTP 404 for `/robots.txt`); `/games/` on
`www.scummvm.org` is permitted by that site's own robots.txt, which
`Disallow`s only `/frs` and `/downloads`. This plugin never requests
anything from `www.scummvm.org` at runtime — the page was read by a human
and the result is checked in.


A directory is not always one game
----------------------------------

The first twelve rows are one game per directory: `Soltys/` holds
Sołtys and nothing else, so the directory allowlist is the whole rule
and every payload in it may be offered.

The sixteen added in 0.2.0 are not. They live in three **engine**
directories — `SLUDGE/`, `Wintermute/` and `WAGE/` — which hold the
freeware games of one engine each, by many different authors, and hold
more archives than the games page names. `SLUDGE/` lists 35 files where
the page names 30; `Wintermute/` lists 17 where the page names one.

So those rows carry an explicit `files` tuple, and for them **the file
list is the allowlist** rather than the directory. A row with an empty
`files` offers whatever the directory lists, which is what keeps a
ScummVM re-release (`drascula-int-1.0.zip` becoming `-1.1.zip`) working
with no code change; a row with a `files` offers exactly those names and
nothing else, which is what keeps an unlisted archive out.
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
    #: The exact archive names that are this game, when the directory
    #: holds more than one game. Empty means "the whole directory", which
    #: is right for a directory named after one title and wrong for an
    #: engine's shelf. See the module docstring.
    files: tuple[str, ...] = ()

    def offers(self, filename: str) -> bool:
        """Whether this game claims one file from its directory."""
        return not self.files or filename in self.files


#: The SLUDGE shelf's shared provenance. One sentence rather than
#: fourteen copies, and it says the same thing each of those rows would:
#: these are free games for a free engine, and the ScummVM project lists
#: every one of them on its own freeware games page.
SLUDGE_FREEWARE = (
    "a free SLUDGE-engine game, distributed at no charge by the ScummVM "
    "project on its own freeware games page"
)

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
    # --- SLUDGE engine games ------------------------------------------
    # One directory, fourteen games, thirty-five archives -- five of
    # which the games page does not name. Hence `files` per row.
    "above-the-waves": Game(
        directory="SLUDGE",
        title="Above The Waves",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("atw.zip",),
    ),
    "cubert-badbone": Game(
        directory="SLUDGE",
        title="Cubert Badbone, P.I.",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=(
            "cubert.zip",
            "cubert-04.zip",
            "cubert-1.1.zip",
            "cubert-1.1-1.zip",
            "cubert-1.2.zip",
            "cubert-1.25.zip",
        ),
    ),
    "frasse": Game(
        directory="SLUDGE",
        title="Frasse and the Peas of Kejick",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=(
            "frasse-1.03.zip",
            "frasse-1.04.zip",
            "frasse-2.02.zip",
            "frasse-2.03.zip",
        ),
    ),
    "full-moon": Game(
        directory="SLUDGE",
        title="Full Moon",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("fullmoon.zip",),
    ),
    "the-interview": Game(
        directory="SLUDGE",
        title="The Interview",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("interview.zip",),
    ),
    "leptons-quest": Game(
        directory="SLUDGE",
        title="Lepton's Quest",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=(
            "leptonsquest.zip",
            "leptonsquest-linux.zip",
            "leptonsquest-mac.zip",
        ),
    ),
    "life-flashes-by": Game(
        directory="SLUDGE",
        title="Life Flashes By",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("life.zip", "lifeflashesby.zip"),
    ),
    "mandy-christmas-adventure": Game(
        directory="SLUDGE",
        title="Mandy Christmas Adventure",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("mandy-1.2.zip", "mandy-1.3.zip", "mandy-1.4.zip"),
    ),
    "nathans-second-chance": Game(
        directory="SLUDGE",
        title="Nathan's Second Chance",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("nsc.zip",),
    ),
    "out-of-order": Game(
        directory="SLUDGE",
        title="Out Of Order",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("outoforder.zip", "ooo.zip"),
    ),
    "robins-rescue": Game(
        directory="SLUDGE",
        title="Robin's Rescue",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=(
            "robinsrescue.zip",
            "robinsrescue-alt.zip",
            "robinsrescue-linux.zip",
        ),
    ),
    "sam-and-max-flintlocked": Game(
        directory="SLUDGE",
        title="Sam and Max Flintlocked",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("samnmaxfl.zip",),
    ),
    "cruise-ship": Game(
        directory="SLUDGE",
        title="The Game That Takes Place on a Cruise Ship",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("tgttpoacs.zip",),
    ),
    "tremendous-corporation": Game(
        directory="SLUDGE",
        title="The Secret of Tremendous Corporation",
        platform="scummvm",
        freed_by=SLUDGE_FREEWARE,
        files=("tsotc-v6.zip",),
    ),
    # --- Wintermute engine ---------------------------------------------
    # The directory holds seventeen archives; the games page names one,
    # and the other sixteen are not on it. `files` is the difference
    # between offering a game and offering a shelf.
    "helga-deep-in-trouble": Game(
        directory="Wintermute",
        title="Helga Deep In Trouble",
        platform="scummvm",
        freed_by=(
            "a free Wintermute-engine game, distributed at no charge by the "
            "ScummVM project on its own freeware games page"
        ),
        files=("helga_deep_in_trouble.zip",),
    ),
    # --- WAGE ------------------------------------------------------------
    "wage": Game(
        directory="WAGE",
        title="WAGE",
        platform="scummvm",
        freed_by=(
            "the World Builder games collection, distributed at no charge by "
            "the ScummVM project on its own freeware games page"
        ),
        files=("wage-games-master-1.0.zip",),
    ),
}

#: Directory -> the slugs filed under it. A list rather than one slug,
#: because three of the directories are an *engine* shelf holding many
#: games -- `SLUDGE` alone is fourteen. Derived, so it cannot fall out of
#: step when a row is added above.
BY_DIRECTORY: dict[str, list[str]] = {}
for _slug, _game in GAMES.items():
    BY_DIRECTORY.setdefault(_game.directory, []).append(_slug)

#: Every directory this plugin may read. The allowlist proper: a
#: `source_id` naming anything else has no slug, so no code path can build
#: a URL into it.
DIRECTORIES: tuple[str, ...] = tuple(BY_DIRECTORY)


def game_for(slug: str) -> Game | None:
    """The game with this slug, or None."""
    if not isinstance(slug, str):
        return None
    return GAMES.get(slug.strip().lower())


def slugs_for_directory(directory: str) -> list[str]:
    """Every game filed under a `/frs/extras/` directory, in table order.

    Empty for a directory that is not one of the fifteen -- which is what
    makes this an allowlist: a directory with no slugs has no game, and
    no code path below can build a URL into it.
    """
    if not isinstance(directory, str):
        return []
    return list(BY_DIRECTORY.get(directory.strip(), ()))


def slug_for_directory(directory: str) -> str | None:
    """The slug for a `/frs/extras/` directory, or None.

    None for a directory this plugin does not carry, **and also for one
    holding more than one game** -- `SLUDGE` is fourteen of them, and
    answering with whichever happened to be first would be a guess. Use
    `slugs_for_directory` when the answer may be plural.
    """
    slugs = slugs_for_directory(directory)
    return slugs[0] if len(slugs) == 1 else None
