"""Reading one GitHub release, defensively.

`GET /repos/{owner}/{repo}/releases/latest` is the whole network surface
of the catalogue. What comes back is a large JSON object of which this
plugin reads four fields, so everything else is ignored rather than
trusted, and every one of the four is checked for the type it is supposed
to be -- `ctx.http` hands over decoded text and nothing upstream of here
guarantees it is the document that was expected.

The failure modes worth naming, because each has a distinct message:

* **404.** GitHub answers this for a repository that publishes no
  releases at all, which is a real state and not an outage -- it is
  exactly what `dolphin-emu/dolphin` returns. Saying "not found" without
  saying that would send somebody looking for a network problem.
* **403 with a rate-limit body.** Unauthenticated GitHub API calls are
  capped at 60 per hour per IP. A catalogue of four projects is four
  calls, so this is not a limit a normal `cores list` reaches -- but it is
  shared with everything else on that address, and an operator who hits it
  needs to be told to wait rather than told the plugin is broken.
* **200 that is not JSON.** Every proxy and captive portal in the world
  answers 200 with HTML.
"""

import json
from dataclasses import dataclass


class ReleaseError(Exception):
    """The release could not be read, and the message says why."""


@dataclass(frozen=True)
class Asset:
    """One downloadable file attached to a release."""

    name: str
    url: str
    size_bytes: int | None


@dataclass(frozen=True)
class Release:
    """The parts of a GitHub release this plugin uses."""

    tag: str
    published: str
    html_url: str
    assets: tuple[Asset, ...]

    def names(self) -> list[str]:
        return [asset.name for asset in self.assets]

    def named(self, name: str) -> Asset:
        for asset in self.assets:
            if asset.name == name:
                return asset
        raise ReleaseError(f"release {self.tag!r} has no asset named {name!r}")


def _size(raw) -> int | None:
    """`assets[].size` is an int in every response seen, but it arrives
    from off-process and `FetchFile.size_bytes` is a `ge=0` field: one bad
    value would raise out of `plan()` rather than merely being unknown."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw >= 0 else None


def parse_release(body: str, repo: str) -> Release:
    """A `Release` from the body of `releases/latest`."""
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            f"GitHub's latest release for {repo} was not JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ReleaseError(
            f"GitHub's latest release for {repo} was not a JSON object"
        )

    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        raise ReleaseError(
            f"GitHub's latest release for {repo} carries no tag_name, so "
            f"there is no version to show an operator"
        )

    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ReleaseError(
            f"GitHub's latest release for {repo} ({tag}) has no assets. A "
            f"release with no attached files publishes source only, which is "
            f"not something this plugin can install."
        )

    assets: list[Asset] = []
    for entry in raw_assets:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        url = entry.get("browser_download_url")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(url, str) or not url:
            continue
        assets.append(Asset(name=name, url=url, size_bytes=_size(entry.get("size"))))

    if not assets:
        raise ReleaseError(
            f"none of the {len(raw_assets)} entries in {repo}'s latest release "
            f"({tag}) carried both a name and a browser_download_url"
        )

    published = data.get("published_at")
    html_url = data.get("html_url")
    return Release(
        tag=tag.strip(),
        published=published if isinstance(published, str) else "",
        html_url=html_url if isinstance(html_url, str) else "",
        assets=tuple(assets),
    )


def fetch_release(http, project) -> Release:
    """`project`'s latest release, or a refusal an operator can act on."""
    response = http.get(project.releases_url)
    status = response.status_code

    if status == 404:
        raise ReleaseError(
            f"{project.display} ({project.repo}) publishes no GitHub releases "
            f"-- its releases/latest endpoint answers 404. This is a real "
            f"state, not an outage: some projects ship builds from their own "
            f"site instead. See emulators/projects.py."
        )
    if status in (403, 429):
        raise ReleaseError(
            f"GitHub answered HTTP {status} for {project.repo}'s latest "
            f"release. Unauthenticated API requests are limited to 60 per "
            f"hour per address and this plugin makes one per project, so the "
            f"budget is most likely being shared with something else on this "
            f"network. Wait for the hour to roll over and try again."
        )
    if status != 200:
        raise ReleaseError(
            f"GitHub answered HTTP {status} for {project.repo}'s latest release"
        )
    return parse_release(response.text, project.repo)
