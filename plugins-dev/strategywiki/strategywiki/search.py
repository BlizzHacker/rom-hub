"""StrategyWiki search capability."""

import json
from urllib.parse import quote

from rom_hub_sdk import SearchProvider, SearchResult

API_ENDPOINT = "https://strategywiki.org/w/api.php"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []

        params = {
            "action": "query",
            "list": "search",
            "srsearch": q,
            "format": "json",
            "srlimit": str(min(limit, 50)),
        }

        response = self.ctx.http.get(API_ENDPOINT, params=params)
        if response.status_code != 200:
            return []

        try:
            data = json.loads(response.text)
        except Exception:
            return []

        search_items = data.get("query", {}).get("search", [])
        results: list[SearchResult] = []

        for item in search_items:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            page_slug = quote(title.replace(" ", "_"))
            page_url = f"https://strategywiki.org/wiki/{page_slug}"
            pageid = str(item.get("pageid", ""))

            results.append(
                SearchResult(
                    source_id=title,
                    title=title,
                    platform=platform or "strategywiki",
                    url=page_url,
                    extra={"kind": "walkthrough", "pageid": pageid},
                )
            )
            if len(results) >= limit:
                break

        return results
