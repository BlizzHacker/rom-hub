"""Build FetchPlan for Modrinth project files."""

import posixpath
from urllib.parse import urlsplit

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import make_unique_filename, safe_filename

USER_AGENT = "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"


class ImportRefused(Exception):
    """Raised when project or files cannot be resolved."""


class Importer(ImportProvider):
    def _parse_slug_or_id(self, result: SearchResult) -> str:
        source_id = (result.source_id or "").strip()
        if source_id:
            return source_id

        if result.url:
            path_parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if path_parts:
                return path_parts[-1]

        raise ImportRefused("Cannot determine project slug or id from result")

    def plan(self, result: SearchResult) -> FetchPlan:
        slug_or_id = self._parse_slug_or_id(result)

        headers = {
            "User-Agent": USER_AGENT,
        }

        url = f"https://api.modrinth.com/v2/project/{slug_or_id}/version"
        resp = self.ctx.http.get(url, headers=headers)
        if resp.status_code != 200:
            raise ImportRefused(f"Failed to fetch versions for {slug_or_id}: HTTP {resp.status_code}")

        versions = resp.json()
        if not isinstance(versions, list) or not versions:
            raise ImportRefused(f"No versions available for project {slug_or_id}")

        latest_version = versions[0]
        files_raw = latest_version.get("files", [])
        if not isinstance(files_raw, list) or not files_raw:
            raise ImportRefused(f"No files found in latest version for {slug_or_id}")

        plan_files: list[FetchFile] = []
        seen_names: set[str] = set()

        for item in files_raw:
            file_url = item.get("url")
            if not file_url:
                continue
            raw_name = item.get("filename") or posixpath.basename(file_url) or "mod.jar"
            fname = make_unique_filename(safe_filename(raw_name), seen_names)
            size = item.get("size")
            plan_files.append(FetchFile(url=file_url, filename=fname, size_bytes=size))

        if not plan_files:
            raise ImportRefused(f"No valid downloadable files for {slug_or_id}")

        return FetchPlan(
            platform="pc",
            collection="Modrinth",
            files=plan_files,
        )
