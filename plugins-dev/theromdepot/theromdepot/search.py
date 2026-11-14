"""Search the complete directory tree captured by the operator's browser."""

from __future__ import annotations

from rom_hub_sdk import SearchProvider, SearchResult

from .catalog import Entry, load_catalog
from .platforms import normalise_label, platform_for


def _matches(entry: Entry, terms: tuple[str, ...]) -> bool:
    return all(term in entry.search_text for term in terms)


def _score(entry: Entry, query: str, terms: tuple[str, ...]) -> tuple[int, str]:
    title = entry.title.casefold()
    folded = query.casefold()
    score = 0
    if title == folded:
        score += 10_000
    elif title.startswith(folded):
        score += 5_000
    score += sum(100 for term in terms if term in title)
    return -score, entry.path.casefold()


class Search(SearchProvider):
    def search(self, query: str, platform: str | None, limit: int) -> list[SearchResult]:
        text = str(query or "").strip()
        limit = max(0, int(limit or 0))
        if not text or not limit:
            return []

        wanted_raw = str(platform or "").strip()
        wanted = platform_for(wanted_raw) or wanted_raw.casefold().replace("_", "-")
        terms = tuple(part for part in text.casefold().split() if part)
        hits = [
            entry for entry in load_catalog(self.ctx.config.get("catalog_path"))
            if _matches(entry, terms)
            and (not wanted_raw or entry.platform == wanted
                 or normalise_label(entry.platform_label) == normalise_label(wanted_raw))
        ]
        hits.sort(key=lambda entry: _score(entry, text, terms))

        results: list[SearchResult] = []
        for entry in hits[:limit]:
            extra = {
                "site_platform": entry.platform_label,
                "filename": entry.name,
                "login_required": "true",
                "browser_authenticated": "true",
                "action": "download",
                "catalogue_source": "/api/getContents",
                "downloadable": "browser",
                "playable": "false",
            }
            if entry.region:
                extra["region"] = entry.region
            if entry.mtime:
                extra["mtime"] = entry.mtime
            results.append(SearchResult(
                source_id=entry.path,
                title=entry.title,
                platform=entry.platform,
                size_bytes=entry.size_bytes,
                url=entry.download_url,
                extra=extra,
            ))
        return results
