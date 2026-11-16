"""Game Manuals search capability."""

import json

from rom_hub_sdk import SearchProvider, SearchResult

SEARCH_ENDPOINT = "https://archive.org/advancedsearch.php"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []

        search_query = f'mediatype:texts AND title:({q}) AND (manual OR "player\'s guide" OR "strategy guide")'
        params = {
            "q": search_query,
            "fl[]": "identifier,title,year,item_size",
            "output": "json",
            "rows": str(limit),
        }

        response = self.ctx.http.get(SEARCH_ENDPOINT, params=params)
        if response.status_code != 200:
            return []

        try:
            data = json.loads(response.text)
        except Exception:
            return []

        docs = data.get("response", {}).get("docs", [])
        results: list[SearchResult] = []

        for doc in docs:
            identifier = str(doc.get("identifier", "")).strip()
            if not identifier:
                continue

            title = str(doc.get("title") or identifier).strip()
            item_size = doc.get("item_size")
            size_bytes: int | None = None
            if item_size is not None:
                try:
                    size_bytes = int(item_size)
                except (ValueError, TypeError):
                    pass

            extra = {"kind": "manual"}
            if "year" in doc and doc["year"] is not None:
                extra["year"] = str(doc["year"])

            url = f"https://archive.org/details/{identifier}"
            results.append(
                SearchResult(
                    source_id=identifier,
                    title=title,
                    platform=platform or "manuals",
                    size_bytes=size_bytes,
                    url=url,
                    extra=extra,
                )
            )
            if len(results) >= limit:
                break

        return results
