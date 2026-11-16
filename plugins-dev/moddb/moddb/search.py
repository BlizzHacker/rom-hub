"""ModDB Search capability."""

import html
import re

from rom_hub_sdk import SearchProvider, SearchResult


class ModDBError(Exception):
    """Raised when ModDB search encounters a hard failure."""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        game_slug = self.ctx.config.get("game_slug", "").strip()
        clean_query = query.strip()
        if ":" in clean_query:
            prefix, _, rest = clean_query.partition(":")
            game_slug = prefix.strip()
            clean_query = rest.strip()

        if game_slug:
            url = f"https://www.moddb.com/games/{game_slug}/mods"
        else:
            url = "https://www.moddb.com/mods"

        params = {"filter": "t", "kw": clean_query}
        res = self.ctx.http.get(url, params=params)
        if res.status_code != 200:
            raise ModDBError(
                f"ModDB search failed (HTTP {res.status_code}): {res.text[:200]}"
            )

        return self._parse_html(res.text, game_slug, limit)

    def _parse_html(
        self, html_text: str, game_slug: str, limit: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_slugs: set[str] = set()

        # Matches mod links like href="/mods/plugy-the-survival-kit" or full URL
        # Also extracts title and adjacent summary paragraph if available
        entry_pattern = re.compile(
            r'<a\s+[^>]*href="(?:https?://(?:www\.)?moddb\.com)?/mods/([a-zA-Z0-9_-]+)"[^>]*>([\s\S]*?)</a>(?:[\s\S]*?<p[^>]*>([\s\S]*?)</p>)?',
            re.IGNORECASE,
        )

        for match in entry_pattern.finditer(html_text):
            slug, inner_title, raw_summary = match.groups()
            slug = slug.strip()
            # Ignore non-mod subpaths or static assets
            if not slug or slug in ("add", "edit", "top", "rss", "feed") or slug in seen_slugs:
                continue

            title = re.sub(r"<[^>]+>", " ", inner_title).strip()
            title = html.unescape(title)
            # If the anchor only had an image or was empty, clean it up
            if not title or len(title) > 120 or "\n" in title:
                title = slug.replace("-", " ").title()

            summary = ""
            if raw_summary:
                summary = re.sub(r"<[^>]+>", " ", raw_summary).strip()
                summary = html.unescape(summary)

            seen_slugs.add(slug)
            mod_url = f"https://www.moddb.com/mods/{slug}"
            results.append(
                SearchResult(
                    source_id=slug,
                    title=title,
                    platform=game_slug or "pc",
                    url=mod_url,
                    extra={
                        "slug": slug,
                        "game_slug": game_slug,
                        "summary": summary,
                    },
                )
            )
            if len(results) >= limit:
                break

        return results
