"""Game Manuals importer capability."""

import json
from urllib.parse import quote

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename

DEFAULT_COLLECTION = "Game Manuals"
MAX_MANUAL_FILES = 32

_ALLOWED_EXTS = (".pdf", ".cbz", ".epub", "_djvu.txt", ".djvu")
_IGNORED_EXTS = ("_meta.xml", "_files.xml", "_archive.torrent", "_thumb.jpg", "_itemimage.jpg")


class ImportRefused(Exception):
    """The entry cannot be imported."""


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        identifier = (result.source_id or "").strip()
        if not identifier:
            raise ImportRefused("search result carries no Archive.org identifier")

        meta_url = f"https://archive.org/metadata/{identifier}/files"
        response = self.ctx.http.get(meta_url)
        if response.status_code != 200:
            raise ImportRefused(f"Archive.org returned HTTP {response.status_code} for {identifier}")

        try:
            data = json.loads(response.text)
        except Exception as exc:
            raise ImportRefused(f"invalid JSON from Archive.org metadata for {identifier}: {exc}") from exc

        raw_files = data.get("result", [])
        if not isinstance(raw_files, list) or not raw_files:
            raise ImportRefused(f"Archive.org item {identifier} lists no files")

        selected = []
        for f in raw_files:
            name = str(f.get("name", "")).strip()
            if not name:
                continue
            lower = name.lower()
            if any(lower.endswith(ign) for ign in _IGNORED_EXTS):
                continue
            if any(lower.endswith(ext) for ext in _ALLOWED_EXTS):
                selected.append(f)

        # Fallback if no explicitly matched manual extensions were found
        if not selected:
            for f in raw_files:
                name = str(f.get("name", "")).strip()
                if not name:
                    continue
                lower = name.lower()
                if any(lower.endswith(ign) for ign in _IGNORED_EXTS):
                    continue
                if lower.endswith((".zip", ".txt")):
                    selected.append(f)

        if not selected:
            raise ImportRefused(f"Archive.org item {identifier} has no downloadable manual files")

        selected = selected[:MAX_MANUAL_FILES]

        plan_files: list[FetchFile] = []
        seen_filenames: set[str] = set()

        for f in selected:
            name = str(f.get("name", "")).strip()
            # Clean filename
            clean_name = safe_filename(name, fallback="manual.pdf")
            candidate = clean_name
            idx = 1
            while candidate.lower() in seen_filenames:
                stem, _, ext = clean_name.rpartition(".")
                candidate = safe_filename(f"{stem}_{idx}.{ext or 'pdf'}")
                idx += 1

            seen_filenames.add(candidate.lower())

            quoted_name = quote(name.replace("\\", "/"))
            file_url = f"https://archive.org/download/{identifier}/{quoted_name}"

            size_bytes: int | None = None
            raw_size = f.get("size")
            if raw_size is not None:
                try:
                    size_bytes = int(raw_size)
                except (ValueError, TypeError):
                    pass

            plan_files.append(
                FetchFile(
                    url=file_url,
                    filename=candidate,
                    size_bytes=size_bytes,
                )
            )

        platform = (result.platform or "").strip() or "manuals"
        collection = self.ctx.config.get("collection") or DEFAULT_COLLECTION

        return FetchPlan(
            files=plan_files,
            platform=platform,
            collection=collection,
        )
