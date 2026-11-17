"""Build FetchPlan for Thunderstore packages."""

from urllib.parse import urlsplit

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename

USER_AGENT = "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"


class ImportRefused(Exception):
    """Raised when a Thunderstore package cannot be resolved."""


class Importer(ImportProvider):
    def _parse_coordinates(self, result: SearchResult) -> tuple[str, str, str]:
        source_id = (result.source_id or "").strip()
        if ":" in source_id:
            community, _, rest = source_id.partition(":")
            if "/" in rest:
                owner, name = rest.split("/", 1)
                return community.strip(), owner.strip(), name.strip()

        extra = result.extra or {}
        if extra.get("community") and extra.get("owner") and extra.get("name"):
            return extra["community"].strip(), extra["owner"].strip(), extra["name"].strip()

        if "/" in source_id:
            community = str(self.ctx.config.get("community") or "valheim").strip()
            owner, name = source_id.split("/", 1)
            return community, owner.strip(), name.strip()

        if result.url:
            parts = [p for p in urlsplit(result.url).path.strip("/").split("/") if p]
            # e.g. ['c', community, 'p', owner, name]
            if len(parts) >= 5 and parts[0] == "c" and parts[2] == "p":
                return parts[1].strip(), parts[3].strip(), parts[4].strip()

        raise ImportRefused(f"Cannot parse Thunderstore coordinates from {result.source_id!r} or {result.url!r}")

    def plan(self, result: SearchResult) -> FetchPlan:
        community, owner, name = self._parse_coordinates(result)

        headers = {"User-Agent": USER_AGENT}
        url = f"https://thunderstore.io/api/experimental/package/{owner}/{name}/"
        resp = self.ctx.http.get(url, headers=headers)
        if resp.status_code != 200:
            raise ImportRefused(
                f"Package {owner}/{name} not found (HTTP {resp.status_code})"
            )

        data = resp.json()
        if not isinstance(data, dict):
            raise ImportRefused(f"Unexpected Thunderstore response for {owner}/{name}")

        latest_ver = data.get("latest")
        if not isinstance(latest_ver, dict):
            versions = data.get("versions", [])
            latest_ver = versions[0] if isinstance(versions, list) and versions else None
        if not isinstance(latest_ver, dict):
            raise ImportRefused(f"No versions found for package {owner}/{name}")

        download_url = latest_ver.get("download_url")
        if not download_url:
            raise ImportRefused(f"Latest version for {owner}/{name} has no download_url")

        ver_str = latest_ver.get("version_number", "1.0.0")
        file_size = latest_ver.get("file_size")
        filename = safe_filename(f"{owner}-{name}-{ver_str}.zip")

        return FetchPlan(
            platform="pc",
            collection=f"Thunderstore - {community}",
            files=[
                FetchFile(
                    url=download_url,
                    filename=filename,
                    size_bytes=file_size,
                )
            ],
        )
