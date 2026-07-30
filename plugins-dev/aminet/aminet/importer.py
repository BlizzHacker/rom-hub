"""Turn one Aminet path into a FetchPlan.

The `.readme` beside every package does three jobs at once, which is why
it is fetched rather than the search row being trusted:

1. **It proves the package is still there.** Aminet answers a missing
   path with HTTP 200 and a themed error page, so a status code proves
   nothing; a readable header does.
2. **It states the architecture.** The search row carries an *icon*,
   which is a rendering; `Architecture: m68k-amigaos` is the uploader's
   own declaration, and it is the field the platform comes from.
3. **It states the shelf.** `Type: game/think` is checked against the
   path, so a `source_id` naming `util/` cannot be dressed up as a game.

Everything after that is one decision each:

* **The platform is never guessed.** MorphOS, AmigaOS 4, AROS and
  Amithlon are four different computers that are not a Commodore Amiga,
  and each refuses by name. See `platforms.py`.
* **Support shelves refuse.** `game/data`, `game/edit`, `game/hint` and
  `game/patch` hold data files, level editors, walkthroughs and patches.
  Importing a walkthrough as a game is not a small mistake -- it is a
  library entry that will never start.
* **The path is the URL, the basename is the filename.**
  `game/think/abrick.lha` is where the file lives; `FetchFile.filename`
  is what the host opens for writing and must be a bare name, so the two
  come apart here rather than somewhere the host has to defend against.
"""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .archive import AminetError, download_url, parse_readme, readme_url
from .filenames import safe_filename
from .platforms import describe, holds_games, platform_for, why_unmapped

DEFAULT_COLLECTION = "Aminet"


class ImportRefused(Exception):
    """This package cannot be imported, and the message says why."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        path = _clean(result.source_id or "")
        directory = path.rsplit("/", 1)[0] if "/" in path else ""

        self._check_shelf(path, directory)
        header = self._readme(path)
        self._check_declared_type(path, header)
        platform = self._platform(result, path, header)

        return FetchPlan(
            files=[
                FetchFile(
                    url=download_url(path),
                    filename=safe_filename(
                        path.rsplit("/", 1)[-1], fallback="package.lha"
                    ),
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    # -- checks ----------------------------------------------------------

    @staticmethod
    def _check_shelf(path: str, directory: str) -> None:
        holds = holds_games(directory)
        if holds is None:
            raise ImportRefused(
                f"{path!r} is not in Aminet's game tree. Aminet has fourteen "
                f"top-level trees -- util, mods, pix, docs and the rest -- and "
                f"this plugin imports from `game/` only, because a ROM library "
                f"has no shelf for a font or a MOD."
            )
        if holds is False:
            raise ImportRefused(
                f"Aminet shelf {directory!r} is {describe(directory)!r}, not "
                f"games. Importing one as a game would put a library entry in "
                f"place that can never start. Set include_support if you want "
                f"these listed, but they still will not import."
            )

    @staticmethod
    def _check_declared_type(path: str, header: dict[str, str]) -> None:
        """The readme's own `Type:` must agree with where the file sits.

        Cheap, and it catches the one case the shelf check cannot: a
        `source_id` assembled by hand that points a game path at a file
        which is really something else.
        """
        declared = (header.get("type") or "").strip().strip("/").lower()
        directory = path.rsplit("/", 1)[0].lower() if "/" in path else ""
        if declared and directory and declared != directory:
            raise ImportRefused(
                f"Aminet package {path!r} sits in {directory!r} but its own "
                f".readme declares Type: {declared!r}. Those disagree, and the "
                f"readme is the uploader's statement -- so which shelf this "
                f"belongs on is not something to decide here."
            )

    def _platform(
        self, result: SearchResult, path: str, header: dict[str, str]
    ) -> str:
        # An operator's --platform reaches the plugin on the SearchResult
        # and is authoritative -- it is the documented way to file a
        # MorphOS or AROS build on a shelf they keep for it.
        override = (result.platform or "").strip()
        if override:
            return override
        architecture = header.get("architecture", "")
        slug = platform_for(architecture)
        if slug is None:
            raise ImportRefused(f"{path}: {why_unmapped(architecture)}")
        return slug

    # -- transport -------------------------------------------------------

    def _readme(self, path: str) -> dict[str, str]:
        stem = path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
        url = readme_url(f"{stem}.readme")
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise ImportRefused(
                f"Aminet returned HTTP {response.status_code} for {url!r}, so "
                f"{path!r} could not be confirmed"
            )
        try:
            return parse_readme(response.text)
        except AminetError as exc:
            raise ImportRefused(
                f"Aminet package {path!r} has no readable .readme header: {exc} "
                f"Aminet answers a missing path with HTTP 200 and an error "
                f"page, so this is what 'the package is gone' looks like."
            ) from exc


def _clean(source_id: str) -> str:
    """An Aminet archive path, or a refusal.

    Rejects anything that is not a plain relative path. The value reaches
    `download_url`, which quotes it, and the host re-checks the resulting
    URL against the allowlist -- but a `..` segment or a scheme here is a
    caller mistake worth naming rather than a string to sanitise quietly.
    """
    raw = (source_id or "").strip()
    if not raw:
        raise ImportRefused(
            "the search result carries no Aminet path; expected something like "
            "'game/think/abrick.lha'"
        )
    raw = raw.lstrip("/")
    if "://" in raw or raw.startswith("//"):
        raise ImportRefused(
            f"{source_id!r} is a URL, not an Aminet path. Pass the archive path, "
            f"for example 'game/think/abrick.lha'."
        )
    parts = raw.split("/")
    if len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
        raise ImportRefused(
            f"{source_id!r} is not an Aminet path: it must be "
            f"'<tree>/<shelf>/<file>', for example 'game/think/abrick.lha'"
        )
    return raw
