"""Stream resolver for GitHub repositories."""

from urllib.parse import urlsplit

from rom_hub_sdk import StreamProvider, StreamTarget
from rom_hub.types import SearchResult


class Stream(StreamProvider):
    def _parse_owner_repo(self, result: SearchResult) -> str:
        source_id = (result.source_id or "").strip()
        if "/" in source_id:
            parts = source_id.split("/")
            return f"{parts[0].strip()}/{parts[1].strip()}"

        if result.url:
            path_parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if len(path_parts) >= 2:
                return f"{path_parts[0]}/{path_parts[1]}"

        return source_id

    def resolve(self, result: SearchResult) -> StreamTarget:
        owner_repo = self._parse_owner_repo(result)
        return StreamTarget(
            kind="url",
            target=f"https://github.com/{owner_repo}",
            mime_type="text/html",
            title=result.title,
        )
