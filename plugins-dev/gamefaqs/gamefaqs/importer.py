"""GameFAQs importer capability."""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename

DEFAULT_COLLECTION = "GameFAQs Guides"


class ImportRefused(Exception):
    """The entry cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        source_id = (result.source_id or "").strip()
        if not source_id:
            raise ImportRefused("search result carries no source_id")

        path = source_id.lstrip("/").split("?")[0]
        url = f"https://gamefaqs.gamespot.com/{path}?single=1&print=1"

        raw_title = result.title or "guide"
        if not raw_title.lower().endswith(".html"):
            raw_title = f"{raw_title}.html"
        filename = safe_filename(raw_title, fallback="guide.html")

        platform = (result.platform or "").strip() or "guides"
        collection = self.ctx.config.get("collection") or DEFAULT_COLLECTION

        return FetchPlan(
            files=[
                FetchFile(
                    url=url,
                    filename=filename,
                )
            ],
            platform=platform,
            collection=collection,
        )
