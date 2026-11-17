"""StrategyWiki importer capability."""

import json
from urllib.parse import quote

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename

API_ENDPOINT = "https://strategywiki.org/w/api.php"
DEFAULT_COLLECTION = "StrategyWiki Guides"


class ImportRefused(Exception):
    """The entry cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        title = (result.source_id or result.title or "").strip()
        if not title:
            raise ImportRefused("search result carries no title or source_id")

        files: list[FetchFile] = []
        seen_filenames: set[str] = set()

        def _add_file(page_title: str, is_main: bool = False):
            quoted = quote(page_title.replace(" ", "_"))
            url = f"{API_ENDPOINT}?action=parse&page={quoted}&redirects=1&prop=text&format=json"
            base_name = page_title.replace("/", " - ").strip()
            raw_filename = f"{base_name}.json"
            clean_name = safe_filename(raw_filename, fallback="guide.json")

            candidate = clean_name
            idx = 1
            while candidate.lower() in seen_filenames:
                stem, _, ext = clean_name.rpartition(".")
                candidate = safe_filename(f"{stem}_{idx}.{ext or 'json'}")
                idx += 1

            seen_filenames.add(candidate.lower())
            files.append(FetchFile(url=url, filename=candidate))

        # Resolve redirects first so subpage prefix lookup uses the canonical title.
        try:
            r_resp = self.ctx.http.get(
                API_ENDPOINT,
                params={"action": "query", "titles": title, "redirects": "1", "format": "json"},
            )
            if r_resp.status_code == 200:
                r_data = json.loads(r_resp.text)
                pages = r_data.get("query", {}).get("pages", {})
                if isinstance(pages, dict):
                    for page_obj in pages.values():
                        canon = str((page_obj or {}).get("title") or "").strip()
                        if canon:
                            title = canon
                            break
        except Exception:
            pass

        # Always include the main guide page first
        _add_file(title, is_main=True)

        # Check for subpages/chapters: action=query&list=allpages&apprefix={Title}/
        subpages_params = {
            "action": "query",
            "list": "allpages",
            "apprefix": f"{title}/",
            "aplimit": "50",
            "format": "json",
        }
        try:
            resp = self.ctx.http.get(API_ENDPOINT, params=subpages_params)
            if resp.status_code == 200:
                data = json.loads(resp.text)
                for page in data.get("query", {}).get("allpages", []):
                    sub_title = str(page.get("title", "")).strip()
                    if sub_title and sub_title != title:
                        _add_file(sub_title)
                        if len(files) >= 51:
                            break
        except Exception:
            pass

        platform = (result.platform or "").strip() or "guides"
        collection = self.ctx.config.get("collection") or DEFAULT_COLLECTION

        return FetchPlan(
            files=files,
            platform=platform,
            collection=collection,
        )
