"""Stream resolver for GameBanana items."""

from urllib.parse import urlsplit

from rom_hub_sdk import StreamProvider, StreamTarget
from rom_hub.types import SearchResult


class Stream(StreamProvider):
    def _parse_id(self, result: SearchResult) -> str:
        source_id = (result.source_id or "").strip()
        if ":" in source_id:
            _, _, item_id = source_id.partition(":")
            return item_id.strip()

        extra = result.extra or {}
        if extra.get("id"):
            return extra["id"].strip()

        if result.url:
            parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if len(parts) >= 2:
                return parts[1].strip()

        return source_id or "0"

    def resolve(self, result: SearchResult) -> StreamTarget:
        item_id = self._parse_id(result)
        return StreamTarget(
            kind="url",
            target=f"https://gamebanana.com/mods/{item_id}",
            mime_type="text/html",
            title=result.title,
        )
