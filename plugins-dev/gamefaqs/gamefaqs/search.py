"""GameFAQs search capability.

    term -> /ajax/home_game_search (JSON) -> each game's /faqs page -> guides

The site's /search page builds its results with script, so its HTML carries
no game links (seen 2026-11-22). The JSON endpoint behind the site's own
search box answers with each game's path, and the per-game /faqs pages still
list guides as plain links. The results page is still parsed afterwards, in
case it ever renders server-side again.
"""

import json
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
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_GAME_PATH_RE = re.compile(r"^/([a-zA-Z0-9_-]+)/(\d+-[a-zA-Z0-9_-]+)$")
AJAX_SEARCH = "https://gamefaqs.gamespot.com/ajax/home_game_search"
#: Games whose /faqs page is read per search. Each one is a request.
MAX_GAMES = 3
_IGNORED_PLATS = frozenset({"search", "user", "boards", "community", "features", "news"})


def _strip_tags(text: str) -> str:
    clean = _TAG_RE.sub(" ", text)
    return " ".join(unescape(clean).split())


class GameFAQsError(Exception):
    """Raised when GameFAQs returns an HTTP error or Cloudflare challenge."""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []

        wanted_platform = (platform or self.ctx.config.get("platform") or "").strip().lower()

        ajax = self.ctx.http.get(f"{AJAX_SEARCH}?{urlencode({'term': q})}")
        if ajax.status_code in (403, 503):
            raise GameFAQsError(
                f"GameFAQs search blocked (HTTP {ajax.status_code}) — configure FlareSolverr URL in Settings -> General"
            )
        if ajax.status_code == 200:
            games = _ajax_games(ajax.text, wanted_platform)
            if games:
                return self._guides_for(games[:MAX_GAMES], limit, set())

        search_url = f"https://gamefaqs.gamespot.com/search?{urlencode({'game': q})}"
        response = self.ctx.http.get(search_url)
        if response.status_code != 200:
            if response.status_code in (403, 503):
                raise GameFAQsError(
                    f"GameFAQs search blocked (HTTP {response.status_code}) — configure FlareSolverr URL in Settings -> General"
                )
            raise GameFAQsError(f"GameFAQs search failed (HTTP {response.status_code})")

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

        results.extend(self._guides_for(game_links, limit - len(results), seen_ids))
        return results

    def _guides_for(self, game_links, limit: int, seen_ids: set[str]) -> list[SearchResult]:
        """Read each game's /faqs page and return its guides, up to `limit`."""
        results: list[SearchResult] = []
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


def _ajax_games(text: str, wanted_platform: str) -> list[tuple[str, str, str, str]]:
    """Games from /ajax/home_game_search, guide-carrying ones first.

    Through FlareSolverr the JSON arrives wrapped in the browser's own
    `<pre>` viewer, HTML-escaped; direct, it is bare JSON. Both are read.
    """
    body = text or ""
    wrapped = _PRE_RE.search(body)
    if wrapped:
        body = unescape(wrapped.group(1))
    try:
        rows = json.loads(body)
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    games = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        match = _GAME_PATH_RE.match(str(row.get("url") or ""))
        if not match:
            continue
        plat, game_slug = match.groups()
        if plat.lower() in _IGNORED_PLATS:
            continue
        if wanted_platform and plat.lower() != wanted_platform:
            continue
        title = " ".join(str(row.get("game_name") or row.get("product_name") or "").split())
        has_guides = str(row.get("has_guides") or "0") != "0"
        games.append((not has_guides, (f"/{plat}/{game_slug}", plat, game_slug, title)))
    games.sort(key=lambda item: item[0])
    return [game for _, game in games]
