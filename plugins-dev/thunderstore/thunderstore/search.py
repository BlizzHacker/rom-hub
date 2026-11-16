"""Search Thunderstore packages across one or more game communities."""

from rom_hub_sdk import SearchProvider, SearchResult

USER_AGENT = "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        raw_query = (query or "").strip()
        if ":" in raw_query:
            prefix, _, term_query = raw_query.partition(":")
            community_target = prefix.strip()
            search_query = term_query.strip()
        else:
            community_target = str(self.ctx.config.get("community") or "valheim").strip()
            search_query = raw_query

        communities = [c.strip() for c in community_target.split(",") if c.strip()]
        if not communities:
            communities = ["valheim"]

        terms = [t.lower() for t in search_query.split() if t]
        headers = {"User-Agent": USER_AGENT}

        results: list[SearchResult] = []

        for community in communities:
            if len(results) >= limit:
                break

            url = f"https://thunderstore.io/c/{community}/api/v1/package/"
            resp = self.ctx.http.get(url, headers=headers)
            if resp.status_code != 200:
                continue

            packages = resp.json()
            if not isinstance(packages, list):
                continue

            for pkg in packages:
                if len(results) >= limit:
                    break

                name = pkg.get("name", "")
                full_name = pkg.get("full_name", "")
                owner = pkg.get("owner", "")
                description = pkg.get("description", "") or ""

                if terms:
                    haystack = f"{name} {full_name} {owner} {description}".lower()
                    if not all(term in haystack for term in terms):
                        continue

                versions = pkg.get("versions", [])
                latest_ver = versions[0] if isinstance(versions, list) and versions else {}
                ver_number = latest_ver.get("version_number", "")
                file_size = latest_ver.get("file_size")
                pkg_url = pkg.get("package_url") or f"https://thunderstore.io/c/{community}/p/{owner}/{name}/"
                source_id = f"{community}:{owner}/{name}"

                results.append(
                    SearchResult(
                        source_id=source_id,
                        title=name or full_name or f"{owner}/{name}",
                        platform="pc",
                        size_bytes=file_size,
                        url=pkg_url,
                        extra={
                            "community": community,
                            "owner": owner,
                            "name": name,
                            "version": str(ver_number),
                            "description": description[:200],
                        },
                    )
                )

        return results
