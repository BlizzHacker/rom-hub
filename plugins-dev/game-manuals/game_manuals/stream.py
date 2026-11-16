"""Game Manuals stream capability."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class StreamRefused(Exception):
    """The entry cannot be streamed."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        identifier = (result.source_id or "").strip()
        if not identifier:
            raise StreamRefused("search result carries no Archive.org identifier")

        url = f"https://archive.org/details/{identifier}"
        return StreamTarget(
            kind="url",
            target=url,
            mime_type="text/html",
            title=result.title,
            extra={"kind": "manual"},
        )
