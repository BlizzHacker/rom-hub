"""Build FetchPlan for GitHub repository releases and source code."""

from urllib.parse import urlsplit

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import make_unique_filename, safe_filename


class ImportRefused(Exception):
    """Raised when repository cannot be resolved or planned."""


class Importer(ImportProvider):
    def _parse_owner_repo(self, result: SearchResult) -> tuple[str, str]:
        source_id = (result.source_id or "").strip()
        if "/" in source_id:
            parts = source_id.split("/")
            return parts[0].strip(), parts[1].strip()

        if result.url:
            path_parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            if len(path_parts) >= 2:
                return path_parts[0], path_parts[1]

        raise ImportRefused(f"Cannot determine owner/repo from {result.source_id!r} or {result.url!r}")

    def plan(self, result: SearchResult) -> FetchPlan:
        owner, repo = self._parse_owner_repo(result)

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ROM-Hub/1.0",
        }
        token = (self.ctx.config.get("github_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        include_source = bool(self.ctx.config.get("include_source", True))

        files: list[FetchFile] = []
        seen_names: set[str] = set()

        # 1. Query latest release
        rel_resp = self.ctx.http.get(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers=headers,
        )
        assets = []
        if rel_resp.status_code == 200:
            rel_data = rel_resp.json()
            if isinstance(rel_data, dict):
                assets = rel_data.get("assets", [])

        # Add up to 32 release binary assets
        for asset in assets[:32]:
            url = asset.get("browser_download_url")
            name = asset.get("name")
            size = asset.get("size")
            if not url or not name:
                continue
            fname = make_unique_filename(safe_filename(name), seen_names)
            files.append(FetchFile(url=url, filename=fname, size_bytes=size))

        # 2. Add source zip if no binary assets or include_source is True
        if not files or include_source:
            default_branch = "main"
            repo_resp = self.ctx.http.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers=headers,
            )
            if repo_resp.status_code == 200:
                repo_data = repo_resp.json()
                if isinstance(repo_data, dict):
                    default_branch = repo_data.get("default_branch") or "main"

            source_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{default_branch}"
            source_name = make_unique_filename(safe_filename(f"{repo}-source.zip"), seen_names)
            files.append(FetchFile(url=source_url, filename=source_name))

        if not files:
            raise ImportRefused(f"No release assets or source archive found for {owner}/{repo}")

        return FetchPlan(
            platform="pc",
            collection="GitHub",
            files=files,
        )
