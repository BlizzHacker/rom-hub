"""Nexus Mods Stream capability."""

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        source_id = result.source_id or ""
        if "/" in source_id:
            domain, _, mod_id = source_id.partition("/")
        else:
            domain = result.extra.get("domain") or self.ctx.config.get(
                "game_domain", "diablo2resurrected"
            )
            mod_id = result.extra.get("mod_id") or source_id

        target = f"https://www.nexusmods.com/{domain}/mods/{mod_id}?tab=files"
        return StreamTarget(
            kind="url",
            target=target,
            mime_type="text/html",
            title=result.title,
        )
