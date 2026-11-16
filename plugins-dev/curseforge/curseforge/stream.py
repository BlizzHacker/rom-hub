"""CurseForge Stream capability."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        url = result.url
        if not url or not url.startswith("https://www.curseforge.com"):
            slug = result.extra.get("slug") or result.source_id
            url = f"https://www.curseforge.com/mods/{slug}"

        return StreamTarget(
            kind="url",
            target=url,
            mime_type="text/html",
            title=result.title,
        )
