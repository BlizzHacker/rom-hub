"""Nexus Mods Import capability."""

from typing import Any

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import make_unique_filenames, safe_filename


class ImportRefused(Exception):
    """Raised when a Nexus Mods item cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        source_id = result.source_id or ""
        if "/" in source_id:
            domain, _, mod_id = source_id.partition("/")
        else:
            domain = result.extra.get("domain") or self.ctx.config.get(
                "game_domain", "diablo2resurrected"
            )
            mod_id = result.extra.get("mod_id") or source_id

        if not mod_id:
            raise ImportRefused("Nexus Mods search result carries no mod_id")

        api_key = self.ctx.config.get("api_key")
        browser_stream_url = (
            f"https://www.nexusmods.com/{domain}/mods/{mod_id}?tab=files"
        )
        if not api_key:
            raise ImportRefused(
                f"Nexus Mods API key is not configured. Nexus Mods free accounts "
                f"use Stream / browser download at {browser_stream_url}, while "
                f"Premium API keys download directly."
            )

        headers = {
            "apikey": api_key,
            "Accept": "application/json",
        }
        files_url = (
            f"https://api.nexusmods.com/v1/games/{domain}/mods/{mod_id}/files.json"
        )
        res = self.ctx.http.get(files_url, headers=headers)

        if res.status_code == 403:
            raise ImportRefused(
                f"Nexus Mods API file access denied (HTTP 403): Nexus Mods free "
                f"accounts use Stream / browser download at {browser_stream_url}, "
                f"while Premium API keys download directly."
            )
        if res.status_code != 200:
            raise ImportRefused(
                f"Nexus Mods failed to fetch files for {domain}/{mod_id} "
                f"(HTTP {res.status_code}): {res.text[:200]}"
            )

        try:
            files_data = res.json()
        except Exception as exc:
            raise ImportRefused(
                f"Nexus Mods invalid files response for {domain}/{mod_id}: {exc}"
            ) from exc

        files_list = files_data.get("files") if isinstance(files_data, dict) else []
        if not files_list:
            raise ImportRefused(
                f"Nexus Mods mod {domain}/{mod_id} lists no downloadable files"
            )

        resolved_files: list[tuple[str, str, int | None]] = []
        for file_entry in files_list:
            file_id = file_entry.get("file_id")
            if not file_id:
                continue

            file_name = (
                file_entry.get("file_name")
                or file_entry.get("name")
                or f"{mod_id}_{file_id}.zip"
            )
            size_bytes = file_entry.get("size_in_bytes")

            dl_link_url = (
                f"https://api.nexusmods.com/v1/games/{domain}/mods/{mod_id}/"
                f"files/{file_id}/download_link.json"
            )
            dl_res = self.ctx.http.get(dl_link_url, headers=headers)

            if dl_res.status_code == 403:
                raise ImportRefused(
                    f"Nexus Mods API download link generation denied (HTTP 403): "
                    f"Nexus Mods free accounts use Stream / browser download at "
                    f"{browser_stream_url}, while Premium API keys download directly."
                )
            if dl_res.status_code != 200:
                continue

            uri = self._extract_uri(dl_res.json())
            if uri:
                resolved_files.append((uri, file_name, size_bytes))

        if not resolved_files:
            raise ImportRefused(
                f"No downloadable links resolved for Nexus Mods mod {domain}/{mod_id}"
            )

        unique_files = make_unique_filenames(resolved_files)
        fetch_files = [
            FetchFile(url=url, filename=fn, size_bytes=size)
            for url, fn, size in unique_files
        ]

        return FetchPlan(
            files=fetch_files,
            platform=domain,
            collection="Nexus Mods",
        )

    def _extract_uri(self, data: Any) -> str | None:
        if isinstance(data, list) and len(data) > 0:
            first = data[0]
            if isinstance(first, dict):
                return first.get("URI") or first.get("url")
        elif isinstance(data, dict):
            return data.get("URI") or data.get("url")
        return None
