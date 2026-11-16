"""GameFAQs stream capability."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class StreamRefused(Exception):
    """The entry cannot be streamed."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        source_id = (result.source_id or "").strip()
        if not source_id:
            raise StreamRefused("search result carries no source_id")

        path = source_id.lstrip("/").split("?")[0]
        url = f"https://gamefaqs.gamespot.com/{path}?single=1&print=1"

        return StreamTarget(
            kind="url",
            target=url,
            mime_type="text/html",
            title=result.title,
            extra={"kind": "guide"},
        )
