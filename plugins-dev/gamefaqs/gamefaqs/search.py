"""GameFAQs search capability."""

import re
from html import unescape
from urllib.parse import urlencode

from rom_hub_sdk import SearchProvider, SearchResult

_FAQ_LINK_RE = re.compile(
    r'<a\s+[^>]*href=["\'](/([a-zA-Z0-9_-]+)/(\d+-[a-zA-Z0-9_-]+)/faqs/(\d+))["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_GAME_LINK_RE = re.compile(
    r'<a\s+[^>]*href=["\'](/([a-zA-Z0-9_-]+)/(\d+-[a-zA-Z0-9_-]+))["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_IGNORED_PLATS = frozenset({"search", "user", "boards", "community", "features", "news"})


def _strip_tags(text: str) -> str:
    clean = _TAG_RE.sub(" ", text)
    return " ".join(unescape(clean).split())


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []

        wanted_platform = (platform or self.ctx.config.get("platform") or "").strip().lower()

        search_url = f"https://gamefaqs.gamespot.com/search?{urlencode({'game': q})}"
        response = self.ctx.http.get(search_url)
        if response.status_code != 200:
            return []

        html = response.text or ""
        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        # 1. Look for direct FAQ links in the search page
        for match in _FAQ_LINK_RE.finditer(html):
            if len(results) >= limit:
                break
            full_path, plat, game_slug, faq_id, raw_title = match.groups()
            if plat.lower() in _IGNORED_PLATS:
                continue
            if wanted_platform and plat.lower() != wanted_platform:
                continue
            source_id = f"{plat}/{game_slug}/faqs/{faq_id}"
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            title = _strip_tags(raw_title) or f"Guide {faq_id}"
            guide_url = f"https://gamefaqs.gamespot.com/{source_id}?single=1&print=1"
            results.append(
                SearchResult(
                    source_id=source_id,
                    title=title,
                    platform=plat,
                    url=guide_url,
                    extra={"kind": "guide"},
                )
            )

        if len(results) >= limit:
            return results

        # 2. Extract game hub links to inspect /faqs pages (up to 3 games)
        game_links: list[tuple[str, str, str, str]] = []
        seen_games: set[str] = set()
        for match in _GAME_LINK_RE.finditer(html):
            full_path, plat, game_slug, raw_title = match.groups()
            if plat.lower() in _IGNORED_PLATS:
                continue
            if wanted_platform and plat.lower() != wanted_platform:
                continue
            game_id = f"{plat}/{game_slug}"
            if game_id in seen_games:
                continue
            seen_games.add(game_id)
            game_title = _strip_tags(raw_title)
            game_links.append((full_path, plat, game_slug, game_title))
            if len(game_links) >= 3:
                break

        for _, plat, game_slug, game_title in game_links:
            if len(results) >= limit:
                break
            faqs_url = f"https://gamefaqs.gamespot.com/{plat}/{game_slug}/faqs"
            faqs_resp = self.ctx.http.get(faqs_url)
            if faqs_resp.status_code != 200:
                continue
            faqs_html = faqs_resp.text or ""

            for match in _FAQ_LINK_RE.finditer(faqs_html):
                if len(results) >= limit:
                    break
                _, f_plat, f_slug, faq_id, raw_title = match.groups()
                source_id = f"{f_plat}/{f_slug}/faqs/{faq_id}"
                if source_id in seen_ids:
                    continue
                seen_ids.add(source_id)

                guide_title = _strip_tags(raw_title) or f"FAQ {faq_id}"
                full_title = f"{game_title} - {guide_title}" if game_title else guide_title
                guide_url = f"https://gamefaqs.gamespot.com/{source_id}?single=1&print=1"
                results.append(
                    SearchResult(
                        source_id=source_id,
                        title=full_title,
                        platform=f_plat,
                        url=guide_url,
                        extra={"kind": "guide"},
                    )
                )

        return results
