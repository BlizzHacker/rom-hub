"""YouTube walkthrough and tutorial search capability."""

import json
import re
from html import unescape
from typing import Any

from rom_hub_sdk import SearchProvider, SearchResult

_YT_INITIAL_DATA_RE = re.compile(
    r'(?:var\s+ytInitialData\s*=\s*|ytInitialData\s*=\s*)({.+?});\s*(?:</script>|\n)',
    re.DOTALL,
)


def _find_video_renderers(obj: Any) -> list[dict]:
    """Recursively discover videoRenderer nodes in YouTube data trees."""
    renderers: list[dict] = []

    def _walk(cur: Any):
        if isinstance(cur, dict):
            if "videoRenderer" in cur and isinstance(cur["videoRenderer"], dict):
                renderers.append(cur["videoRenderer"])
            for val in cur.values():
                _walk(val)
        elif isinstance(cur, list):
            for item in cur:
                _walk(item)

    _walk(obj)
    return renderers


def _parse_text(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    if "runs" in node and isinstance(node["runs"], list):
        return "".join(str(r.get("text", "")) for r in node["runs"] if isinstance(r, dict))
    return ""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []

        suffix = str(self.ctx.config.get("default_suffix") or "").strip()
        if suffix and suffix.lower() not in q.lower():
            full_query = f"{q} {suffix}"
        else:
            full_query = q

        api_key = str(self.ctx.config.get("api_key") or "").strip()
        if api_key:
            return self._search_api(full_query, api_key, platform, limit)
        return self._search_keyless(full_query, platform, limit)

    def _search_api(
        self, query: str, api_key: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        endpoint = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": str(min(limit, 50)),
            "key": api_key,
        }
        resp = self.ctx.http.get(endpoint, params=params)
        if resp.status_code != 200:
            return []

        try:
            data = json.loads(resp.text)
        except Exception:
            return []

        results: list[SearchResult] = []
        for item in data.get("items", []):
            vid_id = item.get("id", {}).get("videoId")
            if not vid_id:
                continue
            snippet = item.get("snippet", {})
            title = unescape(str(snippet.get("title", "")).strip()) or vid_id
            channel = str(snippet.get("channelTitle", ""))

            results.append(
                SearchResult(
                    source_id=vid_id,
                    title=title,
                    platform=platform or "youtube",
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    extra={"kind": "tutorial", "channel": channel, "duration": ""},
                )
            )
            if len(results) >= limit:
                break
        return results

    def _search_keyless(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        # Try POST to youtubei internal endpoint first
        post_url = "https://www.youtube.com/youtubei/v1/search"
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20230522.01.00",
                }
            },
            "query": query,
        }
        resp = self.ctx.http.post(post_url, json=payload)
        data = None
        if resp.status_code == 200:
            try:
                data = json.loads(resp.text)
            except Exception:
                data = None

        if not data:
            # Fall back to GET /results page parsing
            get_url = "https://www.youtube.com/results"
            resp = self.ctx.http.get(get_url, params={"search_query": query})
            if resp.status_code == 200:
                html = resp.text or ""
                m = _YT_INITIAL_DATA_RE.search(html)
                if m:
                    try:
                        data = json.loads(m.group(1))
                    except Exception:
                        data = None

        if not data:
            return []

        renderers = _find_video_renderers(data)
        results: list[SearchResult] = []
        seen_ids: set[str] = set()

        for vr in renderers:
            vid_id = str(vr.get("videoId", "")).strip()
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)

            title = unescape(_parse_text(vr.get("title"))).strip() or vid_id
            channel = _parse_text(vr.get("ownerText") or vr.get("shortBylineText")).strip()
            duration = _parse_text(vr.get("lengthText")).strip()

            results.append(
                SearchResult(
                    source_id=vid_id,
                    title=title,
                    platform=platform or "youtube",
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    extra={"kind": "tutorial", "channel": channel, "duration": duration},
                )
            )
            if len(results) >= limit:
                break

        return results
