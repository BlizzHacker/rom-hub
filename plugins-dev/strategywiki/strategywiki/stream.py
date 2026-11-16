"""StrategyWiki stream capability."""

from urllib.parse import quote

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class StreamRefused(Exception):
    """The entry cannot be streamed."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        title = (result.source_id or result.title or "").strip()
        if not title:
            raise StreamRefused("search result carries no title or source_id")

        page_slug = quote(title.replace(" ", "_"))
        url = f"https://strategywiki.org/wiki/{page_slug}"

        return StreamTarget(
            kind="url",
            target=url,
            mime_type="text/html",
            title=result.title,
            extra={"kind": "walkthrough"},
        )
