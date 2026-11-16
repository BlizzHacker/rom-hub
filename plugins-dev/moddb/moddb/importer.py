"""ModDB Import capability."""

import re

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import make_unique_filenames, safe_filename


class ImportRefused(Exception):
    """Raised when a ModDB item cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        slug = result.extra.get("slug") or result.source_id
        if not slug:
            raise ImportRefused("ModDB search result carries no slug or source_id")

        downloads_url = f"https://www.moddb.com/mods/{slug}/downloads"
        res = self.ctx.http.get(downloads_url)
        if res.status_code != 200:
            raise ImportRefused(
                f"Failed to fetch ModDB downloads page for {slug} (HTTP {res.status_code})"
            )

        download_ids = self._extract_download_ids(res.text)
        if not download_ids:
            # Check if downloads page directly has a mirror link
            direct_mirrors = self._extract_mirror_links(res.text)
            if not direct_mirrors:
                raise ImportRefused(f"No downloads found on ModDB for mod {slug!r}")
            resolved_files = direct_mirrors
        else:
            resolved_files = []
            for dl_id in download_ids:
                start_url = f"https://www.moddb.com/downloads/start/{dl_id}"
                start_res = self.ctx.http.get(start_url)
                if start_res.status_code == 200:
                    mirrors = self._extract_mirror_links(start_res.text)
                    resolved_files.extend(mirrors)

        if not resolved_files:
            raise ImportRefused(
                f"No downloadable mirror links resolved for ModDB mod {slug!r}"
            )

        unique_files = make_unique_filenames(resolved_files)
        fetch_files = [
            FetchFile(url=url, filename=fn, size_bytes=size)
            for url, fn, size in unique_files
        ]

        platform = result.platform or "pc"
        return FetchPlan(
            files=fetch_files,
            platform=platform,
            collection="ModDB",
        )

    def _extract_download_ids(self, html_text: str) -> list[str]:
        """Extract unique download IDs from a mod's downloads page."""
        ids: list[str] = []
        seen: set[str] = set()

        # Matches /downloads/start/{id}
        for dl_id in re.findall(r"/downloads/start/(\d+)", html_text):
            if dl_id not in seen:
                seen.add(dl_id)
                ids.append(dl_id)

        # Matches /downloads/{id}-filename or /downloads/{id}
        for dl_id in re.findall(r'/downloads/(\d+)(?:-[a-zA-Z0-9_-]+|[/"\'])', html_text):
            if dl_id not in seen:
                seen.add(dl_id)
                ids.append(dl_id)

        return ids

    def _extract_mirror_links(
        self, html_text: str
    ) -> list[tuple[str, str, int | None]]:
        """Extract /downloads/mirror/{id}/{hash}/{filename} links from HTML."""
        mirrors: list[tuple[str, str, int | None]] = []
        seen_urls: set[str] = set()

        # Regex matching /downloads/mirror/{id}/{hash}/{filename}
        pattern = re.compile(
            r'href="((?:https?://(?:www\.)?moddb\.com)?(/downloads/mirror/\d+/[a-zA-Z0-9]+/([^"\'\s<>]+)))"',
            re.IGNORECASE,
        )

        for match in pattern.finditer(html_text):
            full_match, mirror_path, raw_filename = match.groups()
            url = (
                f"https://www.moddb.com{mirror_path}"
                if not full_match.startswith("http")
                else full_match
            )
            if url in seen_urls:
                continue
            seen_urls.add(url)
            mirrors.append((url, raw_filename, None))

        return mirrors
