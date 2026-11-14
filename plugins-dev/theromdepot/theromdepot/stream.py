"""Hand an authenticated download to the operator's own browser."""

from __future__ import annotations

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget

from .catalog import clean_path, load_catalog


class StreamRefused(RuntimeError):
    """The requested id is not present in the operator's captured tree."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        try:
            source_id = clean_path(getattr(result, "source_id", "") or "")
        except ValueError as exc:
            raise StreamRefused(f"invalid The ROM Depot path: {exc}") from exc

        entries = {entry.path: entry for entry in
                   load_catalog(self.ctx.config.get("catalog_path"))}
        entry = entries.get(source_id)
        if entry is None:
            raise StreamRefused(
                f"{source_id!r} is not a file in the configured /api/getContents export"
            )
        return StreamTarget(
            kind="url",
            target=entry.download_url,
            mime_type="application/octet-stream",
            title=entry.title,
            extra={
                "action": "download",
                "login_required": "true",
                "browser_authenticated": "true",
                "platform": entry.platform,
                "site_platform": entry.platform_label,
                "filename": entry.name,
                "catalogue_source": "/api/getContents",
            },
        )
