"""CurseForge Import capability."""

from typing import Any

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import make_unique_filenames, safe_filename


class ImportRefused(Exception):
    """Raised when a CurseForge item cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        mod_id = result.extra.get("mod_id") or result.source_id
        if not mod_id:
            raise ImportRefused("CurseForge search result carries no mod_id")

        api_key = self.ctx.config.get("api_key")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key

        files_data = self._fetch_files(mod_id, headers, api_key)
        if not files_data:
            raise ImportRefused(f"CurseForge mod {mod_id} returned no files")

        resolved_files: list[tuple[str, str, int | None]] = []
        for file_entry in files_data:
            file_id = file_entry.get("id")
            if not file_id:
                continue

            file_name = file_entry.get("fileName") or f"{mod_id}_{file_id}.zip"
            size_bytes = file_entry.get("fileLength")
            download_url = file_entry.get("downloadUrl")

            # If downloadUrl is null and api_key is configured, query the download-url endpoint
            if not download_url and api_key:
                dl_res = self.ctx.http.get(
                    f"https://api.curseforge.com/v1/mods/{mod_id}/files/{file_id}/download-url",
                    headers=headers,
                )
                if dl_res.status_code == 200:
                    try:
                        dl_json = dl_res.json()
                        if isinstance(dl_json, dict) and isinstance(
                            dl_json.get("data"), str
                        ):
                            download_url = dl_json["data"]
                    except Exception:
                        pass

            # Fallback to ForgeCDN formula if downloadUrl is still null
            if not download_url or not isinstance(download_url, str):
                part1 = int(file_id) // 1000
                part2 = int(file_id) % 1000
                download_url = (
                    f"https://edge.forgecdn.net/files/{part1}/{part2}/{file_name}"
                )

            resolved_files.append((download_url, file_name, size_bytes))

        if not resolved_files:
            raise ImportRefused(f"CurseForge mod {mod_id} has no downloadable files")

        unique_files = make_unique_filenames(resolved_files)
        fetch_files = [
            FetchFile(url=url, filename=fn, size_bytes=size)
            for url, fn, size in unique_files
        ]

        platform = result.platform or "pc"
        return FetchPlan(
            files=fetch_files,
            platform=platform,
            collection="CurseForge",
        )

    def _fetch_files(
        self, mod_id: str, headers: dict[str, str], api_key: str | None
    ) -> list[dict[str, Any]]:
        # If API key is present or mod_id is numeric, try the Core API first
        if api_key or mod_id.isdigit():
            api_url = f"https://api.curseforge.com/v1/mods/{mod_id}/files"
            res = self.ctx.http.get(api_url, headers=headers)
            if res.status_code == 200:
                try:
                    data = res.json()
                    if isinstance(data, dict) and "data" in data:
                        return data["data"]
                except Exception:
                    pass

        # Fallback to web API endpoint
        web_url = f"https://www.curseforge.com/api/v1/mods/{mod_id}/files"
        web_res = self.ctx.http.get(web_url, headers={"Accept": "application/json"})
        if web_res.status_code == 200:
            try:
                data = web_res.json()
                if isinstance(data, dict) and "data" in data:
                    return data["data"]
            except Exception:
                pass

        return []
