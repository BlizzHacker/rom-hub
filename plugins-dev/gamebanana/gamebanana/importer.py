"""Build FetchPlan for GameBanana item files."""

import posixpath
from urllib.parse import urlsplit

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import make_unique_filename, safe_filename

USER_AGENT = "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"


class ImportRefused(Exception):
    """Raised when GameBanana item or files cannot be resolved."""


class Importer(ImportProvider):
    def _parse_model_id(self, result: SearchResult) -> tuple[str, str]:
        source_id = (result.source_id or "").strip()
        if ":" in source_id:
            model, _, item_id = source_id.partition(":")
            return model.strip(), item_id.strip()

        extra = result.extra or {}
        if extra.get("model") and extra.get("id"):
            return extra["model"].strip(), extra["id"].strip()

        if result.url:
            parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if len(parts) >= 2:
                section = parts[0].strip()
                item_id = parts[1].strip()
                if section.endswith("s"):
                    model = section[:-1].capitalize()
                else:
                    model = section.capitalize()
                return model, item_id

        if source_id.isdigit():
            return "Mod", source_id

        raise ImportRefused(f"Cannot parse GameBanana model and id from {result.source_id!r} or {result.url!r}")

    def plan(self, result: SearchResult) -> FetchPlan:
        model, item_id = self._parse_model_id(result)

        headers = {"User-Agent": USER_AGENT}
        url = f"https://gamebanana.com/apiv11/{model}/{item_id}/ProfilePage"
        resp = self.ctx.http.get(url, headers=headers)
        if resp.status_code != 200:
            raise ImportRefused(f"Failed to fetch profile for {model} {item_id}: HTTP {resp.status_code}")

        data = resp.json()
        if not isinstance(data, dict):
            raise ImportRefused(f"Invalid profile response for {model} {item_id}")

        files_raw = data.get("_aFiles", [])
        if not isinstance(files_raw, list) or not files_raw:
            raise ImportRefused(f"No downloadable files found for {model} {item_id}")

        files: list[FetchFile] = []
        seen_names: set[str] = set()

        for f in files_raw:
            if not isinstance(f, dict):
                continue
            dl_url = f.get("_sDownloadUrl")
            if not dl_url:
                continue
            raw_file = f.get("_sFile") or posixpath.basename(dl_url) or f"{item_id}.zip"
            fname = make_unique_filename(safe_filename(raw_file), seen_names)
            size = f.get("_nFilesize")
            files.append(FetchFile(url=dl_url, filename=fname, size_bytes=size))

        if not files:
            raise ImportRefused(f"No valid files found for {model} {item_id}")

        game = result.extra.get("game") if result.extra else None
        collection = f"GameBanana - {game}" if game else "GameBanana"

        return FetchPlan(
            platform="pc",
            collection=collection,
            files=files,
        )
