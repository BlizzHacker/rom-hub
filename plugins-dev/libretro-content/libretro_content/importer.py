"""Turn one `<system>/<filename>` into a FetchPlan.

Three decisions, each the safe half of a choice that could have gone the
other way:

**The listing is re-read, and the name must match exactly.** The plugin
could build a URL straight from `source_id` and hand it over -- the host
would fetch whatever answers. It re-reads the directory instead, so a file
that has been renamed or withdrawn since the search fails as "not in the
listing" rather than as a 404 body written to disk and uploaded as a ROM.
No fuzzy matching: a near miss is the wrong game, not a close one.

**The platform comes from the directory, never from the extension.**
`Break An Egg.md` is a Mega Drive ROM and `Indivisible.zip` is a NES one;
the extension says nothing. The directory is libretro's own statement, and
an unmapped directory refuses by name -- see `platforms.py`, which
distinguishes "needs mapping" from "not a platform" from "ambiguous".

**No size is declared.** h5ai prints `40 KB`, rounded, and there is no
byte count in the document. `FetchFile.size_bytes` left unset means "the
plugin does not know", which is true; a rounded number there would be a
claim the download then fails to meet.
"""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .buildbot import BuildbotError, directory_url, file_url, parse_listing
from .filenames import safe_filename
from .platforms import platform_for, why_unmapped

DEFAULT_COLLECTION = "libretro content"


class ImportRefused(Exception):
    """This item cannot be imported, and the message says why."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        directory, filename = _split(result.source_id or "")

        platform = self._platform(result, directory)
        listed = self._listed_name(directory, filename)

        return FetchPlan(
            files=[
                FetchFile(
                    url=file_url(directory, listed),
                    filename=safe_filename(listed, fallback="content.bin"),
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    @staticmethod
    def _platform(result: SearchResult, directory: str) -> str:
        # An operator's --platform reaches the plugin on the SearchResult
        # and is authoritative -- it is the documented way past the
        # GameCube/Wii ambiguity.
        override = (result.platform or "").strip()
        if override:
            return override
        slug = platform_for(directory)
        if slug is None:
            raise ImportRefused(why_unmapped(directory))
        return slug

    def _listed_name(self, directory: str, filename: str) -> str:
        url = directory_url(directory)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise ImportRefused(
                f"the libretro buildbot returned HTTP {response.status_code} for "
                f"{url!r}, so {filename!r} could not be confirmed"
            )
        try:
            items = parse_listing(response.text)
        except BuildbotError as exc:
            raise ImportRefused(str(exc)) from exc

        for item in items:
            if not item.is_dir and item.name == filename:
                return item.name
        raise ImportRefused(
            f"the libretro buildbot's {directory!r} listing has no file named "
            f"{filename!r}. The buildbot rebuilds this tree, so a name from an "
            f"older search can go away; importing the nearest name instead "
            f"would file a game nobody asked for."
        )


def _split(source_id: str) -> tuple[str, str]:
    """`<system directory>/<filename>`, or a refusal.

    Split on the **last** separator: every mapped directory name is flat,
    but `Sega - Mega Drive - Genesis` shows how much punctuation these
    names carry, and a right split cannot be confused by any of it.
    """
    raw = (source_id or "").strip()
    if not raw:
        raise ImportRefused(
            "the search result carries no libretro content id; expected "
            "'<system directory>/<filename>', for example "
            "'Nintendo - Nintendo Entertainment System/Alter Ego.nes'"
        )
    directory, separator, filename = raw.rpartition("/")
    if not separator or not directory.strip() or not filename.strip():
        raise ImportRefused(
            f"{raw!r} is not a libretro content id: it must be "
            f"'<system directory>/<filename>', for example "
            f"'GCE - Vectrex/Berzerk (World).zip'"
        )
    return directory.strip(), filename.strip()
