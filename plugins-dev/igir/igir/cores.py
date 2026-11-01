"""Igir `cores` capability: standalone Igir v5.8.0+ builds from GitHub releases.

Wraps Christian Emmer's `emmercm/igir` release assets under the RPP v1 `cores`
contract so the Hub can fetch and verify the exact standalone binary for the
operator's target architecture (`linux/x86_64`, `linux/aarch64`,
`windows/x86_64`, `windows/arm64`, `macos/x86_64`, `macos/arm64`) through the
broker's allowlisted downloader.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

REPO = "emmercm/igir"
RELEASE_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DEFAULT_TARGET = "linux/x86_64"

#: Target key -> (human label, anchored regex matching exactly one release asset).
TARGETS: dict[str, tuple[str, re.Pattern[str]]] = {
    "linux/x86_64": (
        "Linux x86_64 (amd64)",
        re.compile(r"\Aigir-[0-9]+\.[0-9]+\.[0-9]+-Linux-amd64\.tar\.gz\Z"),
    ),
    "linux/aarch64": (
        "Linux ARM64 (arm64v8)",
        re.compile(r"\Aigir-[0-9]+\.[0-9]+\.[0-9]+-Linux-arm64v8\.tar\.gz\Z"),
    ),
    "windows/x86_64": (
        "Windows x86_64 (x64)",
        re.compile(r"\Aigir-[0-9]+\.[0-9]+\.[0-9]+-Windows-x64\.zip\Z"),
    ),
    "windows/arm64": (
        "Windows ARM64",
        re.compile(r"\Aigir-[0-9]+\.[0-9]+\.[0-9]+-Windows-arm64\.zip\Z"),
    ),
    "macos/x86_64": (
        "macOS x86_64 (Intel)",
        re.compile(r"\Aigir-[0-9]+\.[0-9]+\.[0-9]+-macOS-x64\.tar\.gz\Z"),
    ),
    "macos/arm64": (
        "macOS ARM64 (Apple Silicon)",
        re.compile(r"\Aigir-[0-9]+\.[0-9]+\.[0-9]+-macOS-arm64\.tar\.gz\Z"),
    ),
}


class CoreListError(Exception):
    """The Igir release listing could not be produced."""


class UnknownTarget(CoreListError):
    """The configured `target` is not in TARGETS."""


class NoAssetForTarget(CoreListError):
    """The upstream release does not carry a matching asset for `target`."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size_bytes: int | None


@dataclass(frozen=True)
class Release:
    tag: str
    html_url: str
    assets: tuple[ReleaseAsset, ...]

    def select(self, target: str) -> ReleaseAsset:
        if target not in TARGETS:
            known = ", ".join(sorted(TARGETS))
            raise UnknownTarget(
                f"unknown target {target!r}; supported targets are: {known}"
            )
        _, pattern = TARGETS[target]
        matches = [a for a in self.assets if pattern.match(a.name)]
        if not matches:
            raise NoAssetForTarget(
                f"{REPO} {self.tag} has no asset matching {target!r}"
            )
        if len(matches) > 1:
            names = ", ".join(a.name for a in matches)
            raise NoAssetForTarget(
                f"ambiguous {target!r} match in {REPO} {self.tag}: {names}"
            )
        return matches[0]


def parse_release(body: str) -> Release:
    try:
        doc = json.loads(body)
    except Exception as exc:
        raise CoreListError(f"{REPO} release response is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise CoreListError(f"{REPO} release response is not a JSON object")
    tag = str(doc.get("tag_name") or "").strip()
    if not tag:
        raise CoreListError(f"{REPO} release is missing tag_name")
    raw_assets = doc.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise CoreListError(f"{REPO} {tag} has no release assets")
    assets: list[ReleaseAsset] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        size = item.get("size")
        size_bytes = int(size) if isinstance(size, int) and size > 0 else None
        if name and url.startswith("https://github.com/"):
            assets.append(ReleaseAsset(name=name, url=url, size_bytes=size_bytes))
    if not assets:
        raise CoreListError(f"{REPO} {tag} has no downloadable github.com assets")
    return Release(
        tag=tag,
        html_url=str(doc.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}"),
        assets=tuple(assets),
    )


def fetch_release(http) -> Release:
    resp = http.get(RELEASE_API_URL)
    if resp.status_code == 403 or resp.status_code == 429:
        raise CoreListError(
            f"GitHub API rate limit reached ({resp.status_code}) fetching {RELEASE_API_URL}"
        )
    if resp.status_code != 200:
        raise CoreListError(
            f"{RELEASE_API_URL} answered HTTP {resp.status_code}"
        )
    return parse_release(resp.text)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._+-]", "_", name.strip())
    return cleaned[:200] or "igir-archive.tar.gz"


class Cores(CoreProvider):
    """Lists and plans the standalone Igir v5.8.0+ 1G1R binary from GitHub releases."""

    def list(self) -> list[CoreArtifact]:
        target = self._target()
        if target not in TARGETS:
            known = ", ".join(sorted(TARGETS))
            raise UnknownTarget(
                f"unknown target {target!r}; supported targets are: {known}"
            )
        label, _ = TARGETS[target]
        release = fetch_release(self.ctx.http)
        asset = release.select(target)
        size = (
            f"{asset.size_bytes / 1_048_576:.1f} MB"
            if asset.size_bytes
            else "size unknown"
        )
        return [
            CoreArtifact(
                core_id="igir",
                name=f"Igir 1G1R Collection Manager [GPL-3.0] - {asset.name}"[:200],
                version=release.tag.lstrip("v"),
                system="1G1R Collection Manager (RomM / Gaseous / Retrom / ES-DE)",
                description=(
                    f"Igir {release.tag} for {label} by Christian Emmer (emmercm/igir). "
                    f"Licence: GPL-3.0. Sorts, filters (--single 1G1R), extracts, patches "
                    f"(IPS/BPS/UPS/Xdelta3/BSDiff v4/PMSR), and verifies ROMs against "
                    f"No-Intro, Redump, TOSEC, and MAME DATs. Asset: {asset.name} ({size}). "
                    f"Release: {release.html_url}"
                )[:1000],
            )
        ]

    def plan(self, core: CoreArtifact) -> FetchPlan:
        if core.core_id != "igir":
            raise CoreListError(f"unknown core_id {core.core_id!r}; expected 'igir'")
        target = self._target()
        release = fetch_release(self.ctx.http)
        asset = release.select(target)
        return FetchPlan(
            files=[
                FetchFile(
                    url=asset.url,
                    filename=_safe_filename(asset.name),
                    size_bytes=asset.size_bytes,
                )
            ],
            platform="1G1R Collection Manager",
        )

    def _target(self) -> str:
        return str(self.ctx.config.get("target") or DEFAULT_TARGET).strip()
