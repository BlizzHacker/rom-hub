"""ScummVM freeware `metadata`: give a rom named `atw.zip` its real title.

    RomRef -> a row in games.py -> MetadataPatch(name)

**One field, and no network at all.** That is the honest shape of this
capability and it is worth stating before anything else. The source is a
directory listing on a download server: `downloads.scummvm.org` publishes
archives, checksums and nothing else -- no cover art, no description, no
release date, no JSON of any kind. The one thing this plugin knows that a
library does not is what the archive is *called*, and it knows that
because `games.py` carries ScummVM's own published title for every row.

So there is no `artwork_url` here, ever, and there will not be one until
the source grows artwork. An `enrich` that fetched a cover from somewhere
else would be this plugin asserting something it has no standing to
assert.

**Why one field is still worth a capability.** An import from here lands
as `atw.zip`, `tgttpoacs.zip`, `nsc.zip`, `gotfree.zip` -- fourteen of
the twenty-eight games are on an engine shelf where the archive name is
an abbreviation nobody would recognise. `The Game That Takes Place on a
Cruise Ship` is a real title and `tgttpoacs.zip` is what a library shows
without this. Sixteen of the rows are new in 0.2.0 and every one of them
has that problem.

**Matching is by archive name first, then by title.** The archive name is
the strong signal: it is in the table, exactly, for the sixteen engine
games, so a rom whose filename is `frasse-2.03.zip` resolves to *Frasse
and the Peas of Kejick* with no guessing at all. Falling back to a title
match covers the twelve directory-per-title rows, whose archives
(`BASS-Floppy-1.3.zip`) are not enumerated because ScummVM re-releases
them and enumerating would break on the next version bump.
"""

import posixpath

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .games import GAMES, game_for

#: Where a game slug may arrive. `source_id` is what the CLI's
#: `--source-id` fills in, and is the route that skips matching entirely.
#: A search result's `source_id` is `<slug>/<filename>`, so the slug half
#: is taken when it carries one.
SLUG_KEYS = ("scummvm_game", "slug", "source_id")


class NoMatch(Exception):
    """No ScummVM freeware game could be identified for this rom."""


class Ambiguous(Exception):
    """Several games match, and choosing between them is not this
    plugin's call to make."""


def match_key(label: str) -> str:
    """A title reduced to what two spellings of it have in common.

    Case and punctuation are noise -- `Sołtys` and `soltys` are not the
    same string and are the same game only because the slug says so, so
    this stays an equality test on the scrubbed form rather than becoming
    a fuzzy one.
    """
    if not isinstance(label, str):
        return ""
    return "".join(c for c in label.lower() if c.isalnum())


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        game = self._resolve(rom)
        # `name` is the only thing this source has. An empty patch would
        # have the host report an enrich that succeeded and changed
        # nothing; `MetadataPatch` treats an absent field as "leave RomM
        # alone", which is exactly right for the artwork this plugin does
        # not have and cannot invent.
        return MetadataPatch(name=game.title)

    def _resolve(self, rom: RomRef):
        slug = self._slug(rom)
        if slug:
            game = game_for(slug)
            if game is None:
                raise NoMatch(
                    f"{slug!r} is not one of the ScummVM freeware games this "
                    f"plugin carries. It knows {sorted(GAMES)}."
                )
            return game

        filename = posixpath.basename((rom.filename or "").replace("\\", "/"))
        if filename:
            claimed = [g for g in GAMES.values() if filename in g.files]
            if len(claimed) == 1:
                return claimed[0]
            if len(claimed) > 1:
                # Cannot happen with the table as it stands and would be a
                # real question if it ever did, so it refuses rather than
                # taking the first row.
                titles = ", ".join(sorted(g.title for g in claimed))
                raise Ambiguous(
                    f"{filename!r} is listed for more than one game: {titles}."
                )

        title = (rom.name or "").strip() or filename
        wanted = match_key(_strip_extension(title))
        if not wanted:
            raise NoMatch(
                f"rom {rom.rom_id} has neither a name nor a filename in the "
                f"library, so there is nothing to look up. Pass --source-id "
                f"with the game slug instead."
            )
        matches = [g for g in GAMES.values() if match_key(g.title) == wanted]
        if not matches:
            raise NoMatch(
                f"no ScummVM freeware game is titled {title!r}. This plugin "
                f"carries twenty-eight games and matches their titles exactly "
                f"once case and punctuation are ignored -- pass --source-id "
                f"with the slug if the library spells it differently."
            )
        if len(matches) > 1:
            titles = ", ".join(sorted(g.title for g in matches))
            raise Ambiguous(
                f"{len(matches)} games are titled {title!r}: {titles}. Pass "
                f"--source-id with the slug you want."
            )
        return matches[0]

    @staticmethod
    def _slug(rom: RomRef) -> str:
        for key in SLUG_KEYS:
            value = (rom.extra.get(key) or "").strip()
            if not value:
                continue
            # A search result's id is `<slug>/<filename>`; an operator's
            # `--source-id` is usually just the slug. Both work.
            return value.partition("/")[0].strip().lower()
        return ""


def _strip_extension(name: str) -> str:
    """`atw.zip` -> `atw`; `Nippon Safes, Inc.` unchanged.

    RomM names a rom after the file it was uploaded as, so the library's
    `name` for one of these is very often an archive name. Only a
    two-to-five character alphanumeric last segment counts as an
    extension, which leaves a title ending in a dot alone.
    """
    stem, dot, extension = (name or "").rpartition(".")
    if not dot or not stem:
        return name or ""
    if not (2 <= len(extension) <= 5) or not extension.isalnum():
        return name
    return stem.strip()
