"""ModDB Stream capability."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        slug = result.extra.get("slug") or result.source_id
        target = f"https://www.moddb.com/mods/{slug}"
        return StreamTarget(
            kind="url",
            target=target,
            mime_type="text/html",
            title=result.title,
        )
