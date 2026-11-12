"""RPP v1 `cores` capability for eXo2romm (`derkommutator/eXo2romm`).

Exposes the upstream `eXo2romm.sh` standalone script metadata from GitHub
through ROM Hub's allowlisted `CoreProvider` contract.

Credits:
- Original author: derkommutator (`https://github.com/derkommutator/eXo2romm`, Unlicense).
- This plugin was inspired to assist with more users having this workflow.
"""

from __future__ import annotations

import json
import re

from rom_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan

UPSTREAM_REPO = "derkommutator/eXo2romm"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
COMMIT_API_URL = f"https://api.github.com/repos/{UPSTREAM_REPO}/commits/main"
_REF_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")


class CoreListError(Exception):
    """Raised when the upstream eXo2romm commit metadata cannot be read."""


class UnknownCore(CoreListError):
    """Raised when an unknown core_id is requested from eXo2romm."""


def parse_commit(body: str) -> tuple[str, str]:
    """Parse GitHub commit API JSON and return `(full_sha, short_sha)`."""
    try:
        doc = json.loads(body)
    except Exception as exc:
        raise CoreListError(
            f"{UPSTREAM_REPO} commit response is not valid JSON: {exc}"
        ) from exc
    if not isinstance(doc, dict):
        raise CoreListError(f"{UPSTREAM_REPO} commit response is not a JSON object")
    sha = str(doc.get("sha") or "").strip()
    if not sha or not _REF_RE.match(sha):
        raise CoreListError(f"{UPSTREAM_REPO} commit response is missing a valid sha")
    return sha, sha[:12]


class Cores(CoreProvider):
    """Lists and plans the upstream `eXo2romm.sh` script by `derkommutator`."""

    def list(self) -> list[CoreArtifact]:
        resp = self.ctx.http.get(COMMIT_API_URL)
        if resp.status_code in (403, 429):
            raise CoreListError(
                f"GitHub API rate limit reached ({resp.status_code}) fetching {COMMIT_API_URL}"
            )
        if resp.status_code != 200:
            raise CoreListError(
                f"{COMMIT_API_URL} answered HTTP {resp.status_code}"
            )
        full_sha, short_sha = parse_commit(resp.text)
        return [
            CoreArtifact(
                core_id="exo2romm",
                name=(
                    "eXo2romm.sh by derkommutator — repackage eXoDOS game zips "
                    "for RomM (inspired to assist more users) [Unlicense]"
                ),
                version=full_sha,
                system="dos",
            )
        ]

    def plan(self, core: CoreArtifact) -> FetchPlan:
        if core.core_id != "exo2romm":
            raise UnknownCore(
                f"unknown eXo2romm core_id {core.core_id!r}; expected 'exo2romm'"
            )
        ref = (core.version or "main").strip() or "main"
        if not _REF_RE.match(ref):
            raise CoreListError(f"invalid upstream git ref {ref!r}")
        raw_url = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{ref}/eXo2romm.sh"
        return FetchPlan(
            files=[FetchFile(url=raw_url, filename="eXo2romm.sh")],
            platform="dos",
            title="eXo2romm (by derkommutator)",
        )
