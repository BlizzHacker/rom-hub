"""Turn one `<game slug>/<filename>` into a FetchPlan.

The whole of the safety argument is one line: **the slug is resolved
through `games.py` before anything else happens.** A `source_id` naming a
directory that is not one of the fifteen cannot produce a URL, so the
subtitle packs and cutscene archives that sit beside these games under
`/frs/extras/` are unreachable from here -- not filtered out afterwards,
unreachable.

**And for three of those fifteen the directory is not enough.** `SLUDGE`,
`Wintermute` and `WAGE` are engine shelves holding many authors' games
and more archives than ScummVM's games page names -- 35 files where the
page names 30, in SLUDGE's case. Those rows carry an explicit file list
and `game.offers()` is checked here as well as at search, so a filename
an operator typed by hand gets the same answer as one they clicked.

After that:

* **The listing is re-read and the filename must match exactly.** ScummVM
  re-releases these archives (`drascula-int-1.0.zip` became
  `drascula-int-1.1.zip`), so a name from an older search can be gone.
  A near miss is a different build, not a close one.
* **A manual or a checksum sidecar is refused by name**, even when it is
  really in the directory, because `is_payload` is the same rule search
  applied and an operator who typed the name by hand should get the same
  answer as one who clicked a result.
* **The platform comes from the table.** Every row states its own, so a
  row added later for a DOS-only freeware drop says so rather than
  inheriting a default nobody re-checked.
"""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .downloads import (
    DownloadsError,
    directory_url,
    file_url,
    is_payload,
    parse_listing,
)
from .filenames import safe_filename
from .games import DIRECTORIES, GAMES, game_for

DEFAULT_COLLECTION = "ScummVM freeware"


class ImportRefused(Exception):
    """This item cannot be imported, and the message says why."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        slug, filename = _split(result.source_id or "")

        game = game_for(slug)
        if game is None:
            raise ImportRefused(
                f"{slug!r} is not one of the ScummVM freeware games this plugin "
                f"carries. It knows {sorted(GAMES)}. The list is an allowlist on "
                f"purpose: `/frs/extras/` also holds subtitle and cutscene packs "
                f"for games that are still on sale, and those are not free to "
                f"redistribute."
            )

        if not is_payload(filename):
            raise ImportRefused(
                f"{filename!r} is not a game file -- it is a checksum sidecar or "
                f"a manual. ScummVM ships those beside the archives; a library "
                f"that filed one as a game would have a game that does not start."
            )

        if not game.offers(filename):
            raise ImportRefused(
                f"{filename!r} is in the {game.directory!r} directory but it is "
                f"not one of {game.title}'s archives. That directory is an "
                f"engine shelf holding several authors' games and more files "
                f"than ScummVM's own freeware page names, so each game here "
                f"lists its archives explicitly. {game.title} is "
                f"{sorted(game.files)}."
            )

        listed = self._listed_name(game.directory, filename)
        platform = (result.platform or "").strip() or game.platform

        return FetchPlan(
            files=[
                FetchFile(
                    url=file_url(game.directory, listed),
                    filename=safe_filename(listed, fallback="game.zip"),
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    def _listed_name(self, directory: str, filename: str) -> str:
        url = directory_url(directory)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise ImportRefused(
                f"the ScummVM download server returned HTTP "
                f"{response.status_code} for {url!r}, so {filename!r} could not "
                f"be confirmed"
            )
        try:
            downloads = parse_listing(response.text)
        except DownloadsError as exc:
            raise ImportRefused(str(exc)) from exc

        for download in downloads:
            if download.filename == filename:
                return download.filename
        available = sorted(d.filename for d in downloads if is_payload(d.filename))
        raise ImportRefused(
            f"the ScummVM download server's {directory!r} listing has no file "
            f"named {filename!r}. ScummVM re-releases these archives, so a name "
            f"from an older search can be gone. It currently offers {available}."
        )


def _split(source_id: str) -> tuple[str, str]:
    """`<game slug>/<filename>`, or a refusal.

    A left split, because a slug never contains `/` and a filename might
    in a hostile input -- so everything after the first separator stays
    part of the name and is then rejected by the exact-match check rather
    than silently becoming a path.
    """
    raw = (source_id or "").strip()
    if not raw:
        raise ImportRefused(
            "the search result carries no ScummVM freeware id; expected "
            "'<game>/<filename>', for example "
            "'beneath-a-steel-sky/BASS-Floppy-1.3.zip'"
        )
    slug, separator, filename = raw.partition("/")
    if not separator or not slug.strip() or not filename.strip():
        raise ImportRefused(
            f"{raw!r} is not a ScummVM freeware id: it must be "
            f"'<game>/<filename>', for example 'soltys/soltys-en-v1.0.zip'. "
            f"The games are {sorted(GAMES)}."
        )
    return slug.strip().lower(), filename.strip()
