"""YouTube stream capability."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class StreamRefused(Exception):
    """The entry cannot be streamed."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        video_id = (result.source_id or "").strip()
        if not video_id:
            raise StreamRefused("search result carries no YouTube video ID")

        url = f"https://www.youtube.com/watch?v={video_id}"
        return StreamTarget(
            kind="url",
            target=url,
            mime_type="text/html",
            title=result.title,
            extra={"kind": "tutorial"},
        )
