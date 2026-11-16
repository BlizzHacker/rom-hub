"""YouTube importer capability."""

import re
from html import unescape

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename

DEFAULT_COLLECTION = "YouTube Tutorials"
_TIMEDTEXT_RE = re.compile(
    r'(?:["\']baseUrl["\']\s*:\s*["\']|["\'])(https://www\.youtube\.com/api/timedtext[^"\']*)["\']'
)


class ImportRefused(Exception):
    """The entry cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        video_id = (result.source_id or "").strip()
        if not video_id:
            raise ImportRefused("search result carries no YouTube video ID")

        title = result.title or video_id
        safe_stem = safe_filename(title, fallback=video_id)
        if "." in safe_stem:
            safe_stem = safe_stem.rsplit(".", 1)[0]
        safe_stem = safe_stem[:150] or video_id

        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        oembed_fn = safe_filename(f"{safe_stem}.oembed.json")

        thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        thumb_fn = safe_filename(f"{safe_stem}.jpg")

        files = [
            FetchFile(url=oembed_url, filename=oembed_fn),
            FetchFile(url=thumb_url, filename=thumb_fn),
        ]

        # Inspect watch page for timedtext caption tracks
        try:
            watch_url = f"https://www.youtube.com/watch?v={video_id}"
            watch_resp = self.ctx.http.get(watch_url)
            if watch_resp.status_code == 200:
                html = watch_resp.text or ""
                m = _TIMEDTEXT_RE.search(html)
                if m:
                    caption_url = unescape(m.group(1)).replace("\\u0026", "&")
                    caption_fn = safe_filename(f"{safe_stem}.vtt")
                    files.append(FetchFile(url=caption_url, filename=caption_fn))
        except Exception:
            pass

        platform = (result.platform or "").strip() or "youtube"
        collection = self.ctx.config.get("collection") or DEFAULT_COLLECTION

        return FetchPlan(
            files=files,
            platform=platform,
            collection=collection,
        )
