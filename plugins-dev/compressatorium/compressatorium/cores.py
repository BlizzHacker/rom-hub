"""RPP v1 `cores` capability: Compressatorium's own docker-compose.yml.

Compressatorium runs as a container beside ROMarr, so the artifact an
operator needs to "install" it is the compose file its author publishes.
`list` reads the latest upstream release from GitHub; `plan` asks the host
to fetch that release's docker-compose.yml from the upstream repository.
The file comes from pacnpal's repository at download time; it is not copied
into this plugin.
"""

from __future__ import annotations

import json
import re

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

from . import UPSTREAM_REPO

LATEST_RELEASE_URL = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest"
CORE_ID = "compressatorium-compose"
COMPOSE_FILENAME = "compressatorium-docker-compose.yml"
SYSTEM = "rom-conversion"
_TAG_RE = re.compile(r"\Av?\d+(\.\d+){1,3}([.-][A-Za-z0-9]+)*\Z")


class CoreListError(Exception):
    """Raised when the upstream release cannot be read."""


class UnknownCore(CoreListError):
    """Raised when a core_id this plugin never listed is requested."""


def parse_release(body: str) -> str:
    """The tag name from GitHub's latest-release JSON, validated."""
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise CoreListError(f"{UPSTREAM_REPO} release response is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise CoreListError(f"{UPSTREAM_REPO} release response is not a JSON object")
    tag = str(doc.get("tag_name") or "").strip()
    if not _TAG_RE.match(tag):
        raise CoreListError(f"{UPSTREAM_REPO} release has no usable tag: {tag!r}")
    return tag


class Cores(CoreProvider):
    """Lists and plans Compressatorium's docker-compose.yml by release."""

    def list(self) -> list[CoreArtifact]:
        resp = self.ctx.http.get(LATEST_RELEASE_URL)
        if resp.status_code in (403, 429):
            raise CoreListError(
                f"GitHub API rate limit reached ({resp.status_code}) for {LATEST_RELEASE_URL}")
        if resp.status_code != 200:
            raise CoreListError(f"{LATEST_RELEASE_URL} answered HTTP {resp.status_code}")
        tag = parse_release(resp.text)
        return [
            CoreArtifact(
                core_id=CORE_ID,
                name=f"Compressatorium {tag} docker-compose.yml (by pacnpal)",
                version=tag,
                system=SYSTEM,
                description=(
                    "The compose file Compressatorium publishes for its container. "
                    "Mount your ROM library into it at the same path ROMarr uses, "
                    "then set this plugin's url (for example "
                    "http://compressatorium:8080)."
                ),
            )
        ]

    def plan(self, core: CoreArtifact) -> FetchPlan:
        if core.core_id != CORE_ID:
            raise UnknownCore(f"unknown core_id {core.core_id!r}; expected {CORE_ID!r}")
        tag = (core.version or "").strip()
        if not _TAG_RE.match(tag):
            raise CoreListError(f"invalid upstream release tag {tag!r}")
        url = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{tag}/docker-compose.yml"
        return FetchPlan(
            files=[FetchFile(url=url, filename=COMPOSE_FILENAME)],
            platform=SYSTEM,
        )
