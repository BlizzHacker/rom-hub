"""The `importer` capability.

A search result carries no credentials, so `plan()` reads the source from
config again by name and builds the `Authorization` header at fetch time.
The header is attached only when the file is on the source's own origin,
since a result can be replayed by anyone holding ROMarr's API key and the
credential must not follow a URL written into it.
"""

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .names import filename_from_url, safe_filename
from .search import PLATFORM
from .sources import origin_of, parse_sources, strip_userinfo


class ImportRefused(Exception):
    pass


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        name, sep, url = (result.source_id or "").partition("|")
        if not sep or not url:
            raise ImportRefused("this result was not produced by the switchfoil plugin")
        sources, _ = parse_sources((self.ctx.config or {}).get("sources") or [])
        source = next((s for s in sources if s.name == name), None)
        if source is None:
            raise ImportRefused(
                f"source {name!r} is no longer in the sources setting, so its "
                "credentials and address are unknown. Search again"
            )
        clean = strip_userinfo(url)
        if clean.partition("://")[0].lower() not in ("http", "https"):
            raise ImportRefused(f"source {name!r} listed a file without an http or https URL")
        headers = source.headers_for(clean)
        if origin_of(clean) != source.origin:
            headers.pop("Authorization", None)
        extra_name = (result.extra or {}).get("filename") or ""
        filename = safe_filename(extra_name or filename_from_url(url))
        return FetchPlan(
            files=[
                FetchFile(
                    url=clean,
                    filename=filename,
                    size_bytes=result.size_bytes,
                    headers=headers,
                )
            ],
            platform=PLATFORM,
        )
