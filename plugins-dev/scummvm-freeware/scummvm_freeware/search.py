"""Search the twelve ScummVM freeware games.

The catalogue is twelve rows in `games.py`, so matching a query costs no
request at all: the terms are matched against the title and the slug in
memory, and only the games that match have their directory listed. A
query nobody's title contains therefore does **zero** network work, and a
one-word query typically does one round trip.

`--platform` is answered the same way -- from the table -- so asking a
ScummVM source for `snes` returns an empty list without a request.

One result per *file*, not per game. Dráscula is six archives (the game,
then MP3, FLAC and uncompressed audio, then two international builds) and
DreamWeb is seven language editions; collapsing those to one row would
mean the plugin choosing a language for the operator. The title carries
the filename so the choice is visible in the result list.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .downloads import DownloadsError, directory_url, is_payload, parse_listing
from .games import GAMES, Game

DEFAULT_MAX_GAMES = 6
#: One listing is one round trip and the host kills a plugin at 30s.
MAX_GAMES_CAP = 12


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        wanted = (platform or "").strip().lower()
        terms = [t for t in (query or "").lower().split() if t]

        matched = [
            (slug, game)
            for slug, game in GAMES.items()
            if (not wanted or game.platform == wanted) and _matches(slug, game, terms)
        ]

        results: list[SearchResult] = []
        for slug, game in matched[: self._max_games()]:
            if len(results) >= limit:
                break
            for download in self._listing(game):
                if len(results) >= limit:
                    break
                if not is_payload(download.filename):
                    continue
                try:
                    results.append(
                        SearchResult(
                            source_id=f"{slug}/{download.filename}",
                            # The filename is part of the title because it
                            # is the only thing distinguishing six Dráscula
                            # rows from each other.
                            title=f"{game.title} ({download.filename})",
                            platform=game.platform,
                            url=directory_url(game.directory),
                            extra={
                                "game": slug,
                                "filename": download.filename,
                                # Why this is free to have, travelling with
                                # the result rather than living in a README.
                                "freed_by": game.freed_by,
                                "size_text": download.size_text,
                                "date": download.date_text,
                            },
                        )
                    )
                except (ValidationError, TypeError, ValueError):
                    continue
        return results

    def _max_games(self) -> int:
        raw = self.ctx.config.get("max_games", DEFAULT_MAX_GAMES)
        try:
            count = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_MAX_GAMES
        return max(1, min(count, MAX_GAMES_CAP))

    def _listing(self, game: Game):
        url = directory_url(game.directory)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise DownloadsError(
                f"the ScummVM download server returned HTTP "
                f"{response.status_code} for {url!r}"
            )
        return parse_listing(response.text)


def _matches(slug: str, game: Game, terms: list[str]) -> bool:
    """Every term appears in the title or the slug.

    The slug is searched as well as the title so that `steel sky` and
    `beneath-a-steel-sky` both work, and so that an operator who has seen
    a `source_id` can search with the half of it they remember.
    """
    haystack = f"{game.title} {slug}".lower()
    return all(term in haystack for term in terms)
