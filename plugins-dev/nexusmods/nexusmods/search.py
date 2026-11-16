"""Nexus Mods Search capability."""

from typing import Any

from rom_hub_sdk import SearchProvider, SearchResult


class NexusModsError(Exception):
    """Raised when Nexus Mods GraphQL search encounters a hard failure."""


GRAPHQL_QUERY = """
query SearchMods($filter: ModsFilter) {
  mods(filter: $filter) {
    nodes {
      modId
      name
      summary
      game {
        domainName
        name
      }
      pictureUrl
    }
  }
}
"""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        default_domain = self.ctx.config.get("game_domain", "diablo2resurrected").strip()
        clean_query = query.strip()
        domain = default_domain

        if ":" in clean_query:
            prefix, _, rest = clean_query.partition(":")
            domain = prefix.strip()
            clean_query = rest.strip()

        filter_obj: dict[str, Any] = {
            "name": {"value": clean_query, "op": "WILDCARD"},
        }
        if domain:
            filter_obj["gameDomainName"] = {"value": domain}

        payload = {
            "query": GRAPHQL_QUERY,
            "variables": {"filter": filter_obj},
        }

        res = self.ctx.http.post(
            "https://api.nexusmods.com/v2/graphql",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if res.status_code != 200:
            raise NexusModsError(
                f"Nexus Mods GraphQL search failed (HTTP {res.status_code}): {res.text[:200]}"
            )

        return self._parse_graphql_data(res.json(), domain, limit)

    def _parse_graphql_data(
        self, body: dict[str, Any], default_domain: str, limit: int
    ) -> list[SearchResult]:
        data = body.get("data", {})
        mods_container = data.get("mods") or {}
        nodes = mods_container.get("nodes") or []

        results: list[SearchResult] = []
        for node in nodes[:limit]:
            mod_id = node.get("modId")
            if not mod_id:
                continue
            name = node.get("name") or f"Mod {mod_id}"
            summary = node.get("summary") or ""
            game_obj = node.get("game") or {}
            mod_domain = game_obj.get("domainName") or default_domain or "mods"
            source_id = f"{mod_domain}/{mod_id}"
            mod_url = f"https://www.nexusmods.com/{mod_domain}/mods/{mod_id}"

            results.append(
                SearchResult(
                    source_id=source_id,
                    title=name,
                    platform=mod_domain,
                    url=mod_url,
                    extra={
                        "mod_id": str(mod_id),
                        "domain": str(mod_domain),
                        "summary": str(summary),
                    },
                )
            )
        return results
