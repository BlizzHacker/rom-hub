"""Stream resolver for Modrinth projects."""

from urllib.parse import urlsplit

from rom_hub_sdk import StreamProvider, StreamTarget
from rom_hub.types import SearchResult


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        slug = (result.source_id or "").strip()
        if not slug and result.url:
            path_parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if path_parts:
                slug = path_parts[-1]

        target_url = f"https://modrinth.com/project/{slug}"
        return StreamTarget(
            kind="url",
            target=target_url,
            mime_type="text/html",
            title=result.title,
        )
