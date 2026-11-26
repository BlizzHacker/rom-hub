"""FitGirl & DODI Repacks RSS subscription and search provider.

Subscribes to the official RSS 2.0 feeds:

    https://fitgirl-repacks.site/feed/
    https://dodi-repacks.site/feed/

and queries WordPress search feeds (`/?s=<query>&feed=rss2` with a fallback to
`/wp-json/wp/v2/posts?search=<query>`) when a specific title is requested.

When `query` is empty or one of `latest`, `feed`, `rss`, or `*`, the provider
returns the latest repack releases from the subscribed RSS feeds so ROMarr's
scheduled RSS sync (`RssSync`) and the plugin dashboard can poll new repacks
automatically.
"""

from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, unquote_plus, urlsplit

from rom_hub_sdk import SearchProvider, SearchResult

PLATFORM = "win"
PLATFORMS: dict[str, str] = {
    "win": "win",
    "pc": "win",
    "windows": "win",
}

FITGIRL_BASE = "https://fitgirl-repacks.site"
FITGIRL_FEED = "https://fitgirl-repacks.site/feed/"
DODI_BASE = "https://dodi-repacks.site"
DODI_FEED = "https://dodi-repacks.site/feed/"

#: Queries that ask for the latest items from the subscribed RSS feeds.
RSS_FEED_QUERIES = frozenset({"", "*", "latest", "feed", "rss", "recent", "new"})

#: Non-game digest and announcement posts published in FitGirl's RSS feed.
_DIGEST_PREFIXES = (
    "upcoming repacks",
    "updates digest",
    "pink paw award",
    "digest for ",
    "donate to fitgirl",
)

#: Leading catalogue numbers on DODI post titles, e.g. "2352- Gears of War...".
_DODI_NUMBER_PREFIX = re.compile(r"^\s*\d{1,6}\s*[-–—]\s*")

#: Magnet URI matcher inside HTML/XML content.
_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[A-Za-z0-9][^\"'\s<>]*", re.IGNORECASE)

#: Href matcher for mirror discovery.
_HREF_RE = re.compile(r'href=["\'](https?://[^"\'\s<>]+)["\']', re.IGNORECASE)

#: Repack size patterns in titles and post bodies ("from 6.7 GB", "Repack Size: 6.7/6.9 GB").
_REPACK_SIZE_RE = re.compile(
    r"(?:repack\s+size|from)\s*[:\-–]?\s*(?:from\s+)?(\d+(?:[.,]\d+)?)\s*(?:/\d+(?:[.,]\d+)?\s*)?(gb|mb)",
    re.IGNORECASE,
)
_ANY_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(gb|mb)\b", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(raw: str) -> str:
    text = html.unescape(str(raw or ""))
    text = _TAG_RE.sub(" ", text)
    return " ".join(text.split()).strip()


def _is_digest_post(title: str) -> bool:
    lowered = title.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _DIGEST_PREFIXES)


def _info_hash(magnet: str) -> str:
    if not magnet.lower().startswith("magnet:"):
        return ""
    query = magnet.split("?", 1)[-1]
    for part in query.split("&"):
        if part.lower().startswith("xt=urn:btih:"):
            raw = part[len("xt=urn:btih:"):].strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", raw):
                return raw.lower()
            if re.fullmatch(r"[A-Za-z2-7]{32}", raw):
                import base64
                try:
                    return base64.b32decode(raw.upper()).hex()
                except ValueError:
                    return ""
    return ""


def _magnet_dn(magnet: str) -> str:
    if not magnet.lower().startswith("magnet:"):
        return ""
    query = magnet.split("?", 1)[-1]
    for part in query.split("&"):
        if part.lower().startswith("dn="):
            return _clean_text(unquote_plus(part[3:]))
    return ""


def _extract_magnets(raw_html: str) -> list[str]:
    unescaped = html.unescape(str(raw_html or ""))
    out: list[str] = []
    seen: set[str] = set()
    for match in _MAGNET_RE.findall(unescaped):
        cleaned = match.strip().rstrip(".,;)")
        btih = _info_hash(cleaned)
        if not btih or btih in seen:
            continue
        seen.add(btih)
        out.append(cleaned)
    return out


def _extract_mirrors(raw_html: str, page_url: str) -> list[str]:
    unescaped = html.unescape(str(raw_html or ""))
    page_host = urlsplit(page_url).hostname or ""
    ignored_hosts = {
        page_host,
        "fitgirl-repacks.site",
        "dodi-repacks.site",
        "en.riotpixels.com",
        "riotpixels.com",
        "www.internetdownloadmanager.com",
        "youtu.be",
        "www.youtube.com",
        "youtube.com",
        "store.steampowered.com",
    }
    mirrors: list[str] = []
    for href in _HREF_RE.findall(unescaped):
        host = (urlsplit(href).hostname or "").lower()
        if not host or host in ignored_hosts:
            continue
        if any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
            continue
        if href not in mirrors:
            mirrors.append(href)
    return mirrors[:8]


def _parse_size_bytes(title: str, raw_html: str) -> int | None:
    plain = _clean_text(raw_html)
    combined = f"{title} {plain}"
    match = _REPACK_SIZE_RE.search(combined) or _ANY_SIZE_RE.search(title) or _ANY_SIZE_RE.search(plain)
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    unit = match.group(2).upper()
    multiplier = 1024**3 if unit == "GB" else 1024**2
    value = int(amount * multiplier)
    return value if value > 0 else None


def _format_repack_title(raw_title: str, repacker: str, magnet: str = "") -> str:
    """Normalize a FitGirl or DODI post title so ROMarr's PC scorer recognizes it."""
    title = _clean_text(raw_title)
    if repacker == "DODI":
        title = _DODI_NUMBER_PREFIX.sub("", title).strip()
    dn = _magnet_dn(magnet)
    # FitGirl's magnet `dn=` carries the full scene-style repack title
    # including `[FitGirl Repack, Selective Download - from X.Y GB]`.
    if repacker == "FitGirl" and dn and "fitgirl" in dn.lower():
        return dn
    badge = "[FitGirl Repack]" if repacker == "FitGirl" else "[DODI Repack]"
    if repacker.lower() not in title.lower():
        title = f"{title} {badge}"
    return title


def _matches_query(title: str, raw_html: str, query: str) -> bool:
    q = (query or "").strip().lower()
    if q in RSS_FEED_QUERIES:
        return True
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", q) if len(tok) >= 2 or tok.isdigit()]
    if not tokens:
        return True
    haystack = f"{title} {_clean_text(raw_html)[:400]}".lower()
    return all(tok in haystack for tok in tokens)


def parse_rss_items(xml_text: str, repacker: str) -> list[dict]:
    """Parse an RSS 2.0 XML document from FitGirl or DODI into normalized item dicts."""
    if not xml_text or "<rss" not in xml_text.lower():
        return []
    try:
        root = ET.fromstring(xml_text.lstrip())
    except ET.ParseError:
        return []
    items: list[dict] = []
    content_tag = "{http://purl.org/rss/1.0/modules/content/}encoded"
    for elem in root.findall("./channel/item"):
        raw_title = _clean_text(elem.findtext("title") or "")
        if not raw_title or _is_digest_post(raw_title):
            continue
        link = (elem.findtext("link") or "").strip()
        pub_date = (elem.findtext("pubDate") or "").strip()
        body = (
            elem.findtext(content_tag)
            or elem.findtext("description")
            or ""
        )
        for enc in elem.findall("enclosure"):
            enc_url = enc.attrib.get("url", "")
            if enc_url:
                body = f"{body} {enc_url}"
        magnets = _extract_magnets(body)
        primary_magnet = magnets[0] if magnets else ""
        title = _format_repack_title(raw_title, repacker, primary_magnet)
        size_bytes = _parse_size_bytes(title, body)
        mirrors = _extract_mirrors(body, link)
        items.append({
            "repacker": repacker,
            "title": title,
            "raw_title": raw_title,
            "url": link,
            "pub_date": pub_date,
            "size_bytes": size_bytes,
            "magnet": primary_magnet,
            "magnets": magnets,
            "mirrors": mirrors,
            "body": body,
        })
    return items


def parse_wp_json_posts(posts: list, repacker: str) -> list[dict]:
    """Parse WordPress `/wp-json/wp/v2/posts` items into normalized item dicts."""
    if not isinstance(posts, list):
        return []
    items: list[dict] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        title_obj = post.get("title")
        raw_title = _clean_text(title_obj.get("rendered", "") if isinstance(title_obj, dict) else str(title_obj or ""))
        if not raw_title or _is_digest_post(raw_title):
            continue
        link = str(post.get("link") or "").strip()
        pub_date = str(post.get("date_gmt") or post.get("date") or "").strip()
        content_obj = post.get("content")
        excerpt_obj = post.get("excerpt")
        body = ""
        if isinstance(content_obj, dict):
            body = str(content_obj.get("rendered") or "")
        if not body and isinstance(excerpt_obj, dict):
            body = str(excerpt_obj.get("rendered") or "")
        magnets = _extract_magnets(body)
        primary_magnet = magnets[0] if magnets else ""
        title = _format_repack_title(raw_title, repacker, primary_magnet)
        size_bytes = _parse_size_bytes(title, body)
        mirrors = _extract_mirrors(body, link)
        items.append({
            "repacker": repacker,
            "title": title,
            "raw_title": raw_title,
            "url": link,
            "pub_date": pub_date,
            "size_bytes": size_bytes,
            "magnet": primary_magnet,
            "magnets": magnets,
            "mirrors": mirrors,
            "body": body,
        })
    return items


class Search(SearchProvider):
    """Search and RSS feed subscription provider for FitGirl and DODI Repacks."""

    def __init__(self, ctx=None):
        super().__init__(ctx)

    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        if limit <= 0:
            return []
        if platform and platform.strip().lower() not in PLATFORMS:
            return []

        cfg = self.ctx.config or {}
        fitgirl_on = bool(cfg.get("fitgirl_enabled", True))
        dodi_on = bool(cfg.get("dodi_enabled", True))
        fitgirl_feed = str(cfg.get("fitgirl_feed") or FITGIRL_FEED).strip()
        dodi_feed = str(cfg.get("dodi_feed") or DODI_FEED).strip()
        include_mirrors = bool(cfg.get("include_mirrors", True))

        clean_query = (query or "").strip()
        is_feed_poll = clean_query.lower() in RSS_FEED_QUERIES

        sources: list[tuple[str, str, str]] = []
        if fitgirl_on:
            sources.append(("FitGirl", FITGIRL_BASE, fitgirl_feed))
        if dodi_on:
            sources.append(("DODI", DODI_BASE, dodi_feed))

        collected: list[dict] = []
        for repacker, base_url, feed_url in sources:
            # 1. Always read the subscribed RSS feed first (latest releases).
            feed_items = self._fetch_rss(feed_url, repacker)
            for item in feed_items:
                if _matches_query(item["title"], item["body"], clean_query):
                    collected.append(item)

            # 2. For specific game queries, also query the WordPress search RSS
            #    feed (`/?s=<query>&feed=rss2`) and fall back to WP REST JSON.
            if not is_feed_poll:
                search_rss_url = f"{base_url}/?s={quote_plus(clean_query)}&feed=rss2"
                search_items = self._fetch_rss(search_rss_url, repacker)
                if not search_items:
                    search_items = self._fetch_wp_json(base_url, clean_query, repacker)
                for item in search_items:
                    if _matches_query(item["title"], item["body"], clean_query):
                        collected.append(item)

        return self._to_results(collected, limit=limit, include_mirrors=include_mirrors)

    def _fetch_rss(self, url: str, repacker: str) -> list[dict]:
        try:
            response = self.ctx.http.get(url)
        except Exception:
            return []
        if getattr(response, "status_code", 0) != 200:
            return []
        return parse_rss_items(getattr(response, "text", "") or "", repacker)

    def _fetch_wp_json(self, base_url: str, query: str, repacker: str) -> list[dict]:
        url = f"{base_url}/wp-json/wp/v2/posts"
        try:
            response = self.ctx.http.get(url, params={"search": query, "per_page": 15})
        except Exception:
            return []
        if getattr(response, "status_code", 0) != 200:
            return []
        try:
            payload = response.json()
        except Exception:
            return []
        return parse_wp_json_posts(payload, repacker)

    @staticmethod
    def _to_results(
        items: list[dict], *, limit: int, include_mirrors: bool
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_keys: set[str] = set()

        # Rank items with inline magnets ahead of mirror-only posts so
        # automated torrent grabs always see direct magnet releases first.
        ordered = sorted(items, key=lambda x: (0 if x.get("magnet") else 1))
        for item in ordered:
            magnet = str(item.get("magnet") or "").strip()
            mirrors = list(item.get("mirrors") or [])
            if not magnet and not include_mirrors:
                continue
            if not magnet and not mirrors and not item.get("url"):
                continue

            btih = _info_hash(magnet)
            url = str(item.get("url") or "").strip()
            repacker = str(item.get("repacker") or "Repack")
            identity_basis = btih or url or item["title"]
            dedupe_key = f"{repacker.lower()}:{identity_basis.lower()}"
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:16]
            source_id = f"repack:{repacker.lower()}:{btih or digest}"

            extra: dict[str, str] = {
                "repacker": repacker,
                "pub_date": str(item.get("pub_date") or ""),
            }
            if magnet:
                extra["torrent_magnet"] = magnet
                extra["torrent_hash"] = btih
                extra["downloadable"] = "torrent"
            else:
                extra["downloadable"] = "browser"
            if mirrors:
                extra["mirrors"] = ", ".join(mirrors[:5])

            results.append(
                SearchResult(
                    source_id=source_id,
                    title=item["title"],
                    platform=PLATFORM,
                    size_bytes=item.get("size_bytes"),
                    url=url or None,
                    extra=extra,
                )
            )
            if len(results) >= limit:
                break
        return results
