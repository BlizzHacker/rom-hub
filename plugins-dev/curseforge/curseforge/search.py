"""CurseForge Search capability."""

import html
import re
from typing import Any

from rom_hub_sdk import SearchProvider, SearchResult

GAME_PREFIXES: dict[str, int] = {
    "wow": 1,
    "warcraft": 1,
    "worldofwarcraft": 1,
    "mc": 432,
    "minecraft": 432,
    "stardew": 669,
    "stardewvalley": 669,
    "sv": 669,
}

GAME_NAMES: dict[int, str] = {
    1: "World of Warcraft",
    432: "Minecraft",
    669: "Stardew Valley",
}

GAME_URL_SLUGS: dict[int, str] = {
    1: "wow",
    432: "minecraft",
    669: "stardewvalley",
}


def parse_query_prefix(raw_query: str, default_game_id: int) -> tuple[str, int]:
    """Parse potential game prefix (e.g. 'wow:dbm', 'mc:sodium', '432:foo') from query."""
    query = raw_query.strip()
    if ":" in query:
        prefix, _, rest = query.partition(":")
        clean_prefix = prefix.strip().lower()
        if clean_prefix in GAME_PREFIXES:
            return rest.strip(), GAME_PREFIXES[clean_prefix]
        if clean_prefix.isdigit():
            return rest.strip(), int(clean_prefix)
    return query, default_game_id


class CurseForgeError(Exception):
    """Raised when CurseForge API or web search encounters a hard failure."""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        cfg_game_id = self.ctx.config.get("game_id", 1)
        clean_query, game_id = parse_query_prefix(query, cfg_game_id)
        api_key = self.ctx.config.get("api_key")

        if api_key:
            return self._search_api(clean_query, game_id, limit, api_key)
        return self._search_web_fallback(clean_query, game_id, limit)

    def _search_api(
        self, query: str, game_id: int, limit: int, api_key: str
    ) -> list[SearchResult]:
        headers = {
            "x-api-key": api_key,
            "Accept": "application/json",
        }
        params: dict[str, Any] = {
            "searchFilter": query,
            "pageSize": min(limit, 50),
        }
        if game_id > 0:
            params["gameId"] = game_id

        res = self.ctx.http.get(
            "https://api.curseforge.com/v1/mods/search",
            params=params,
            headers=headers,
        )
        if res.status_code != 200:
            raise CurseForgeError(
                f"CurseForge API search failed (HTTP {res.status_code}): {res.text[:200]}"
            )
        return self._parse_json_data(res.json(), game_id, limit)

    def _search_web_fallback(
        self, query: str, game_id: int, limit: int
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "searchFilter": query,
            "pageSize": min(limit, 50),
        }
        if game_id > 0:
            params["gameId"] = game_id

        # 1. Try web JSON API endpoint
        res = self.ctx.http.get(
            "https://www.curseforge.com/api/v1/mods/search",
            params=params,
            headers={"Accept": "application/json"},
        )
        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, dict) and "data" in data:
                    return self._parse_json_data(data, game_id, limit)
            except Exception:
                pass

        # 2. Try HTML search page fallback
        game_slug = GAME_URL_SLUGS.get(game_id, "wow")
        html_url = f"https://www.curseforge.com/{game_slug}/search"
        html_res = self.ctx.http.get(html_url, params={"search": query})
        if html_res.status_code != 200:
            raise CurseForgeError(
                f"CurseForge web search failed (HTTP {html_res.status_code})"
            )
        return self._parse_html_search(html_res.text, game_id, limit)

    def _parse_json_data(
        self, body: dict[str, Any], game_id: int, limit: int
    ) -> list[SearchResult]:
        data = body.get("data", [])
        results: list[SearchResult] = []
        for item in data[:limit]:
            mod_id = item.get("id")
            if not mod_id:
                continue
            name = item.get("name") or f"Mod {mod_id}"
            slug = item.get("slug") or str(mod_id)
            summary = item.get("summary") or ""
            item_game_id = item.get("gameId") or game_id
            links = item.get("links") or {}
            website_url = links.get("websiteUrl") or f"https://www.curseforge.com/mods/{slug}"
            platform_name = GAME_NAMES.get(item_game_id, f"game-{item_game_id}")

            results.append(
                SearchResult(
                    source_id=str(mod_id),
                    title=name,
                    platform=platform_name,
                    url=website_url,
                    extra={
                        "mod_id": str(mod_id),
                        "slug": str(slug),
                        "game_id": str(item_game_id),
                        "summary": str(summary),
                    },
                )
            )
        return results

    def _parse_html_search(
        self, html_text: str, game_id: int, limit: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        platform_name = GAME_NAMES.get(game_id, f"game-{game_id}")

        # Matches mod cards/links like href="/wow/addons/deadly-boss-mods" or href="/minecraft/mc-mods/jei"
        pattern = re.compile(
            r'<a\s+[^>]*href="(/[^"/]+/(?:addons|mc-mods|mods)/([^"/]+))"[^>]*>([\s\S]*?)</a>'
        )
        seen_slugs: set[str] = set()

        for match in pattern.finditer(html_text):
            href, slug, inner_text = match.groups()
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            # Extract clean title from inner html or fallback to slug
            title = re.sub(r"<[^>]+>", " ", inner_text).strip()
            title = html.unescape(title)
            if not title or len(title) > 100:
                title = slug.replace("-", " ").title()

            results.append(
                SearchResult(
                    source_id=slug,
                    title=title,
                    platform=platform_name,
                    url=f"https://www.curseforge.com{href}",
                    extra={
                        "slug": slug,
                        "game_id": str(game_id),
                    },
                )
            )
            if len(results) >= limit:
                break
        return results
