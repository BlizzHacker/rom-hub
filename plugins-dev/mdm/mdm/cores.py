"""RPP v1 `cores` capability: Mister Download Manager's official release archives.

`list` reads the latest upstream release from GitHub's API and offers one
artifact per host MDM publishes a usable build for. `plan` reads that same
release again and asks the host to fetch the archive and the release's
`SHA256SUMS.txt` from GitHub. Nothing is copied into this plugin: every byte
comes from anefzaoui's release at download time.

Checksums. GitHub's API reports each asset's SHA-256 (`digest`), computed by
GitHub when the asset was uploaded. An asset without one is never offered,
and neither is a release without a `SHA256SUMS.txt`. The plan pins the
archive's size, which the host enforces, and lands upstream's
`SHA256SUMS.txt` beside it under a versioned name, so the archive can be
checked exactly as ROMarr's `install_mdm` checks it:

    sha256sum -c --ignore-missing MisterDownloadManager-<ver>-SHA256SUMS.txt

The sandbox cannot read SHA256SUMS.txt itself (its link redirects, and plugin
HTTP never follows redirects), and the host does not hash what it fetches, so
that check is the operator's, or ROMarr's, to run. The SHA-256 in each
artifact's description is the value it must produce.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

from . import UPSTREAM_REPO

API_BASE = f"https://api.github.com/repos/{UPSTREAM_REPO}"
LATEST_RELEASE_URL = f"{API_BASE}/releases/latest"
DOWNLOAD_BASE = f"https://github.com/{UPSTREAM_REPO}/releases/download"
SUMS_ASSET = "SHA256SUMS.txt"
SYSTEM = "download-manager"

_TAG_RE = re.compile(r"\Av\d+\.\d+\.\d+(?:[.-][A-Za-z0-9]+)*\Z")
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
_DIGEST_RE = re.compile(r"\Asha256:([0-9a-f]{64})\Z")


class CoreListError(Exception):
    """Raised when the upstream release cannot be read or is unusable."""


class UnknownCore(CoreListError):
    """Raised when a core_id this plugin never listed is requested."""


@dataclass(frozen=True)
class Target:
    """One host MDM publishes a build for, and how to recognise its asset."""

    core_id: str
    label: str
    #: Regex for the asset name; `{ver}` is the escaped version, without `v`.
    pattern: str
    note: str

    def matches(self, name: str, version: str) -> bool:
        return re.fullmatch(self.pattern.format(ver=re.escape(version)), name) is not None


#: The archives ROMarr's `install_mdm` installs, plus the macOS bundles.
TARGETS: tuple[Target, ...] = (
    Target("mdm-linux-x86_64", "Linux x86_64 (glibc)",
           r"MisterDownloadManager-{ver}-linux-x86_64\.tar\.gz",
           "Holds misterdownloadmanager-cli and the desktop app. Built against "
           "glibc: an Alpine/musl container cannot run it, so use Remote mode there."),
    Target("mdm-windows-x86_64", "Windows x86_64 (portable)",
           r"MisterDownloadManager-{ver}-portable\.zip",
           "Portable build holding misterdownloadmanager-cli.exe and the desktop app."),
    Target("mdm-macos-arm64", "macOS arm64",
           r"MisterDownloadManager-{ver}-macos-arm64(?:-experimental)?\.dmg",
           "An app bundle; set MDM_CLI_PATH to the CLI inside it."),
    Target("mdm-macos-x86_64", "macOS x86_64",
           r"MisterDownloadManager-{ver}-macos-x86_64(?:-experimental)?\.dmg",
           "An app bundle; set MDM_CLI_PATH to the CLI inside it."),
)
_TARGETS = {t.core_id: t for t in TARGETS}


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    tag: str
    assets: dict[str, Asset]

    @property
    def version(self) -> str:
        return self.tag[1:]

    def asset_for(self, target: Target) -> Asset | None:
        for name in sorted(self.assets):
            if target.matches(name, self.version):
                return self.assets[name]
        return None

    def sums(self) -> Asset:
        sums = self.assets.get(SUMS_ASSET)
        if sums is None:
            raise CoreListError(
                f"{UPSTREAM_REPO} {self.tag} publishes no usable {SUMS_ASSET}, so "
                "nothing from it can be checked; refusing to offer it")
        return sums


def sums_filename(version: str) -> str:
    """Where a plan writes SHA256SUMS.txt: versioned, so releases never collide."""
    return f"MisterDownloadManager-{version}-{SUMS_ASSET}"


def _asset(raw: object, tag: str) -> Asset | None:
    """One release asset, or None when anything about it is off.

    The download URL must be exactly the upstream release link for this tag
    and name: release JSON that points anywhere else is not trusted to
    describe what the host will fetch.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "")
    if not _NAME_RE.match(name) or ".." in name:
        return None
    url = str(raw.get("browser_download_url") or "")
    if url != f"{DOWNLOAD_BASE}/{tag}/{name}":
        return None
    digest = _DIGEST_RE.match(str(raw.get("digest") or ""))
    size = raw.get("size")
    if digest is None or isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    return Asset(name=name, url=url, size=size, sha256=digest.group(1))


def parse_release(body: str) -> Release:
    """The tag and checkable assets from GitHub's release JSON, validated."""
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise CoreListError(f"{UPSTREAM_REPO} release response is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise CoreListError(f"{UPSTREAM_REPO} release response is not a JSON object")
    tag = str(doc.get("tag_name") or "").strip()
    if not _TAG_RE.match(tag):
        raise CoreListError(f"{UPSTREAM_REPO} release has no usable tag: {tag!r}")
    if doc.get("draft") is True:
        raise CoreListError(f"{UPSTREAM_REPO} {tag} is a draft release")
    raw_assets = doc.get("assets")
    assets: dict[str, Asset] = {}
    for raw in raw_assets if isinstance(raw_assets, list) else []:
        asset = _asset(raw, tag)
        if asset is not None:
            assets[asset.name] = asset
    return Release(tag=tag, assets=assets)


class Cores(CoreProvider):
    """Lists and plans Mister Download Manager's release archives by host."""

    def _release(self, url: str) -> Release:
        resp = self.ctx.http.get(url)
        if resp.status_code in (403, 429):
            raise CoreListError(f"GitHub API rate limit reached ({resp.status_code}) for {url}")
        if resp.status_code != 200:
            raise CoreListError(f"{url} answered HTTP {resp.status_code}")
        return parse_release(resp.text)

    def list(self) -> list[CoreArtifact]:
        release = self._release(LATEST_RELEASE_URL)
        sums = release.sums()
        sums_name = sums_filename(release.version)
        cores = []
        for target in TARGETS:
            asset = release.asset_for(target)
            if asset is None:
                continue
            cores.append(CoreArtifact(
                core_id=target.core_id,
                name=f"Mister Download Manager {release.tag} for {target.label} (by anefzaoui)",
                version=release.tag,
                system=SYSTEM,
                description=(
                    f"{asset.name}, {asset.size} bytes, SHA-256 {asset.sha256}. "
                    f"{target.note} Installs with {sums_name} (SHA-256 {sums.sha256}); "
                    f"check the archive with `sha256sum -c --ignore-missing {sums_name}`."
                ),
            ))
        if not cores:
            raise CoreListError(
                f"{UPSTREAM_REPO} {release.tag} has no checkable archive for any host")
        return cores

    def plan(self, core: CoreArtifact) -> FetchPlan:
        target = _TARGETS.get(core.core_id)
        if target is None:
            raise UnknownCore(
                f"unknown core_id {core.core_id!r}; expected one of {sorted(_TARGETS)}")
        tag = (core.version or "").strip()
        if not _TAG_RE.match(tag):
            raise CoreListError(f"invalid upstream release tag {tag!r}")
        release = self._release(f"{API_BASE}/releases/tags/{tag}")
        if release.tag != tag:
            raise CoreListError(f"asked GitHub for {tag} and it answered {release.tag}")
        asset = release.asset_for(target)
        if asset is None:
            raise CoreListError(
                f"{UPSTREAM_REPO} {tag} has no checkable archive for {target.label}")
        sums = release.sums()
        return FetchPlan(
            files=[
                FetchFile(url=asset.url, filename=asset.name, size_bytes=asset.size),
                FetchFile(url=sums.url, filename=sums_filename(release.version),
                          size_bytes=sums.size),
            ],
            platform=SYSTEM,
        )
