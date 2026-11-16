"""Search Modrinth mods, modpacks, and shaders."""

import json

from rom_hub_sdk import SearchProvider, SearchResult

USER_AGENT = "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        headers = {
            "User-Agent": USER_AGENT,
        }

        params: dict[str, str | int] = {
            "query": (query or "").strip(),
            "limit": min(max(1, limit), 100),
        }

        project_type = str(self.ctx.config.get("project_type") or "").strip().lower()
        if project_type:
            params["facets"] = json.dumps([[f"project_type:{project_type}"]])

        resp = self.ctx.http.get(
            "https://api.modrinth.com/v2/search",
            params=params,
            headers=headers,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        hits = data.get("hits", []) if isinstance(data, dict) else []

        results: list[SearchResult] = []
        for hit in hits[:limit]:
            slug = hit.get("slug") or hit.get("project_id", "")
            if not slug:
                continue

            title = hit.get("title") or slug
            desc = hit.get("description", "")
            p_type = hit.get("project_type", "")
            author = hit.get("author", "")
            downloads = str(hit.get("downloads", 0))
            url = f"https://modrinth.com/project/{slug}"

            results.append(
                SearchResult(
                    source_id=slug,
                    title=title,
                    platform="pc",
                    url=url,
                    extra={
                        "project_type": p_type,
                        "author": author,
                        "downloads": downloads,
                        "description": desc[:200] if desc else "",
                    },
                )
            )
        return results
