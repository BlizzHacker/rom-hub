"""Search GitHub open-source repositories."""

from rom_hub_sdk import SearchProvider, SearchResult

ENRICHMENTS = {
    "bots": "topic:bot OR game bot",
    "games": "topic:game OR topic:source-port",
    "tools": "topic:mod-manager OR topic:modding",
}


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        mode = str(self.ctx.config.get("mode") or "all").lower().strip()
        enrichment = ENRICHMENTS.get(mode, "")

        q_parts = []
        clean_q = (query or "").strip()
        if clean_q:
            q_parts.append(clean_q)
        if enrichment:
            q_parts.append(enrichment)

        if not q_parts:
            full_q = "game"
        else:
            full_q = " ".join(q_parts)

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ROM-Hub/1.0",
        }
        token = (self.ctx.config.get("github_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params = {
            "q": full_q,
            "per_page": min(max(1, limit), 100),
        }

        resp = self.ctx.http.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers=headers,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        items = data.get("items", []) if isinstance(data, dict) else []

        results: list[SearchResult] = []
        for item in items[:limit]:
            owner = item.get("owner", {}).get("login", "")
            repo = item.get("name", "")
            full_name = item.get("full_name") or (f"{owner}/{repo}" if owner and repo else "")
            if not full_name:
                continue

            stars = item.get("stargazers_count", 0)
            lang = item.get("language") or ""
            size_kb = item.get("size")
            size_bytes = int(size_kb * 1024) if isinstance(size_kb, (int, float)) else None
            html_url = item.get("html_url") or f"https://github.com/{full_name}"

            results.append(
                SearchResult(
                    source_id=full_name,
                    title=repo or full_name,
                    platform="pc",
                    url=html_url,
                    size_bytes=size_bytes,
                    extra={
                        "kind": "repo",
                        "stars": str(stars),
                        "language": str(lang),
                    },
                )
            )
        return results
