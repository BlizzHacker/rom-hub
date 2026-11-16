"""Stream resolver for Thunderstore packages."""

from urllib.parse import urlsplit

from rom_hub_sdk import StreamProvider, StreamTarget
from rom_hub.types import SearchResult


class Stream(StreamProvider):
    def _parse_coordinates(self, result: SearchResult) -> tuple[str, str, str]:
        source_id = (result.source_id or "").strip()
        if ":" in source_id:
            community, _, rest = source_id.partition(":")
            if "/" in rest:
                owner, name = rest.split("/", 1)
                return community.strip(), owner.strip(), name.strip()

        extra = result.extra or {}
        if extra.get("community") and extra.get("owner") and extra.get("name"):
            return extra["community"].strip(), extra["owner"].strip(), extra["name"].strip()

        if "/" in source_id:
            community = str(self.ctx.config.get("community") or "valheim").strip()
            owner, name = source_id.split("/", 1)
            return community, owner.strip(), name.strip()

        if result.url:
            parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if len(parts) >= 5 and parts[0] == "c" and parts[2] == "p":
                return parts[1].strip(), parts[3].strip(), parts[4].strip()

        community = str(self.ctx.config.get("community") or "valheim").strip()
        return community, "unknown", source_id or "unknown"

    def resolve(self, result: SearchResult) -> StreamTarget:
        community, owner, name = self._parse_coordinates(result)
        target = f"https://thunderstore.io/c/{community}/p/{owner}/{name}/"
        return StreamTarget(
            kind="url",
            target=target,
            mime_type="text/html",
            title=result.title,
        )
