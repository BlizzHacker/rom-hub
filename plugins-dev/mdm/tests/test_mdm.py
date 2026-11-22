"""Offline suite for the Mister Download Manager ROM Hub plugin.

Runs against a ROM Hub checkout beside this one (../rom-hub/src) or an
installed `rom-hub` package. No network: GitHub is faked at ctx.http, with a
release shaped like the real v0.5.2 one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROM_HUB_SRC = ROOT.parent / "rom-hub" / "src"
if ROM_HUB_SRC.is_dir():
    sys.path.insert(0, str(ROM_HUB_SRC))
sys.path.insert(0, str(ROOT))

from rom_hub.manifest import parse_manifest  # noqa: E402
from rom_hub.netpolicy import url_allowed  # noqa: E402
from rom_hub_sdk import CoreArtifact  # noqa: E402
from rom_hub_sdk.context import HttpResponse  # noqa: E402

from mdm import CATEGORIES, CREDIT, MODES, UPSTREAM_LICENSE, UPSTREAM_URL  # noqa: E402
from mdm.cores import (  # noqa: E402
    API_BASE, DOWNLOAD_BASE, LATEST_RELEASE_URL, SUMS_ASSET, SYSTEM, TARGETS,
    CoreListError, Cores, UnknownCore, parse_release, sums_filename,
)

TAG = "v0.5.2"
VER = "0.5.2"
NAMES = {
    "mdm-linux-x86_64": f"MisterDownloadManager-{VER}-linux-x86_64.tar.gz",
    "mdm-windows-x86_64": f"MisterDownloadManager-{VER}-portable.zip",
    "mdm-macos-arm64": f"MisterDownloadManager-{VER}-macos-arm64-experimental.dmg",
    "mdm-macos-x86_64": f"MisterDownloadManager-{VER}-macos-x86_64-experimental.dmg",
}
OTHER = [f"MisterDownloadManager-{VER}-setup.exe", f"MisterDownloadManager-{VER}-x64.zip",
         f"MisterDownloadManager-{VER}-linux-x86_64.AppImage", "SHA256SUMS.txt.sig"]


def asset(name: str, i: int = 1, tag: str = TAG, **over) -> dict:
    raw = {"name": name, "size": 1000 + i, "digest": "sha256:" + f"{i % 16:x}" * 64,
           "browser_download_url": f"{DOWNLOAD_BASE}/{tag}/{name}"}
    raw.update(over)
    return raw


def release(tag: str = TAG, assets: list | None = None, **over) -> str:
    if assets is None:
        names = list(NAMES.values()) + OTHER + [SUMS_ASSET]
        assets = [asset(n, i) for i, n in enumerate(names, start=1)]
    doc = {"tag_name": tag, "draft": False, "prerelease": False, "assets": assets}
    doc.update(over)
    return json.dumps(doc)


class FakeGitHub:
    def __init__(self, body: str | None = None, status: int = 200):
        self.body = release() if body is None else body
        self.status = status
        self.calls: list[str] = []

    def get(self, url: str, params=None, headers=None):
        self.calls.append(url)
        return HttpResponse(status_code=self.status, text=self.body)


class FakeCtx:
    def __init__(self, http):
        self.http = http
        self.config = {}


def _cores(http) -> Cores:
    cores = Cores.__new__(Cores)
    cores.ctx = FakeCtx(http)
    return cores


def _manifest():
    return parse_manifest((ROOT / "manifest.toml").read_text("utf-8"))


def test_manifest_is_valid_and_keeps_the_token_secret():
    manifest = _manifest()
    assert manifest.slug == "mdm"
    assert manifest.name == "Mister Download Manager (direct downloads)"
    assert manifest.rpp_version == "1"
    assert manifest.license == "MIT"
    assert list(manifest.capabilities) == ["cores"]
    schema = manifest.config_schema
    assert set(schema) == {"mode", "remote_url", "api_token", "connections", "category"}
    assert schema["api_token"]["type"] == "secret"
    assert "default" not in schema["api_token"]
    # Blank mode is "not set": ROMarr keeps its own client settings until it is.
    assert schema["mode"] == {"type": "str", "default": ""}
    assert schema["remote_url"]["default"] == ""
    assert schema["connections"] == {"type": "int", "default": 8}
    assert schema["category"]["default"] == ""


def test_allowlist_is_exactly_the_release_chain():
    assert sorted(_manifest().network) == [
        "api.github.com", "github.com", "release-assets.githubusercontent.com"]


def test_list_offers_one_core_per_host_with_its_digest():
    http = FakeGitHub()
    cores = _cores(http).list()

    assert http.calls == [LATEST_RELEASE_URL]
    assert [c.core_id for c in cores] == [t.core_id for t in TARGETS]
    for core in cores:
        assert core.version == TAG and core.system == SYSTEM
        assert "anefzaoui" in core.name
        assert NAMES[core.core_id] in core.description
        assert "SHA-256 " in core.description and sums_filename(VER) in core.description
        assert len(core.description) <= 1000


def test_plan_fetches_the_archive_and_sums_inside_the_allowlist():
    manifest = _manifest()
    http = FakeGitHub()
    plan = _cores(http).plan(CoreArtifact(core_id="mdm-linux-x86_64", name="x", version=TAG))

    assert http.calls == [f"{API_BASE}/releases/tags/{TAG}"]
    archive, sums = plan.files
    assert archive.url == f"{DOWNLOAD_BASE}/{TAG}/{NAMES['mdm-linux-x86_64']}"
    assert archive.filename == NAMES["mdm-linux-x86_64"]
    assert archive.size_bytes == 1001
    assert sums.url == f"{DOWNLOAD_BASE}/{TAG}/{SUMS_ASSET}"
    assert sums.filename == f"MisterDownloadManager-{VER}-SHA256SUMS.txt"
    assert plan.platform == SYSTEM
    for url in (archive.url, sums.url, LATEST_RELEASE_URL, http.calls[0],
                # where github.com sends release downloads today
                "https://release-assets.githubusercontent.com/github-production-release-asset/1"):
        assert url_allowed(url, list(manifest.network)), url


def test_windows_and_macos_plans_pick_their_own_asset():
    cores = _cores(FakeGitHub())
    for core_id in ("mdm-windows-x86_64", "mdm-macos-arm64", "mdm-macos-x86_64"):
        plan = cores.plan(CoreArtifact(core_id=core_id, name="x", version=TAG))
        assert plan.files[0].filename == NAMES[core_id]


def test_a_non_experimental_macos_bundle_is_still_recognised():
    name = f"MisterDownloadManager-{VER}-macos-arm64.dmg"
    body = release(assets=[asset(name, 3), asset(SUMS_ASSET, 4)])
    [core] = _cores(FakeGitHub(body)).list()
    assert core.core_id == "mdm-macos-arm64" and name in core.description


@pytest.mark.parametrize("status", [403, 429, 404, 500])
def test_list_reports_upstream_failures(status):
    with pytest.raises(CoreListError):
        _cores(FakeGitHub(status=status)).list()


@pytest.mark.parametrize("body", [
    "not json", "[]", json.dumps({"tag_name": "../evil"}), json.dumps({"tag_name": ""}),
    json.dumps({"tag_name": "main"}), release(draft=True),
])
def test_a_bad_release_body_is_refused(body):
    with pytest.raises(CoreListError):
        parse_release(body)


@pytest.mark.parametrize("override", [
    {"digest": None},
    {"digest": "sha1:" + "a" * 40},
    {"digest": "sha256:" + "A" * 64},
    {"size": 0},
    {"size": True},
    {"size": "1000"},
    {"browser_download_url": "https://evil.example/MisterDownloadManager-0.5.2-portable.zip"},
    {"browser_download_url": f"{DOWNLOAD_BASE}/v0.5.1/MisterDownloadManager-0.5.2-portable.zip"},
])
def test_an_asset_that_cannot_be_checked_is_never_offered(override):
    name = NAMES["mdm-windows-x86_64"]
    body = release(assets=[asset(name, 2, **override), asset(NAMES["mdm-linux-x86_64"], 1),
                           asset(SUMS_ASSET, 5)])
    cores = _cores(FakeGitHub(body))
    assert [c.core_id for c in cores.list()] == ["mdm-linux-x86_64"]
    with pytest.raises(CoreListError):
        cores.plan(CoreArtifact(core_id="mdm-windows-x86_64", name="x", version=TAG))


def test_a_release_without_checksums_is_refused():
    body = release(assets=[asset(n, i) for i, n in enumerate(NAMES.values(), start=1)])
    cores = _cores(FakeGitHub(body))
    with pytest.raises(CoreListError, match="SHA256SUMS"):
        cores.list()
    with pytest.raises(CoreListError, match="SHA256SUMS"):
        cores.plan(CoreArtifact(core_id="mdm-linux-x86_64", name="x", version=TAG))


def test_a_release_with_no_usable_archive_is_refused():
    body = release(assets=[asset(SUMS_ASSET, 1), asset(OTHER[0], 2)])
    with pytest.raises(CoreListError, match="no checkable archive"):
        _cores(FakeGitHub(body)).list()


def test_names_with_traversal_are_dropped():
    raw = asset("..evil.zip", 1)
    assert parse_release(release(assets=[raw])).assets == {}


def test_plan_refuses_unknown_cores_bad_tags_and_a_different_release():
    cores = _cores(FakeGitHub())
    with pytest.raises(UnknownCore):
        cores.plan(CoreArtifact(core_id="other", name="x", version=TAG))
    with pytest.raises(CoreListError):
        cores.plan(CoreArtifact(core_id="mdm-linux-x86_64", name="x", version="main;rm"))
    with pytest.raises(CoreListError):
        cores.plan(CoreArtifact(core_id="mdm-linux-x86_64", name="x", version=None))
    other = _cores(FakeGitHub(release(tag="v0.5.3")))
    with pytest.raises(CoreListError, match="answered"):
        other.plan(CoreArtifact(core_id="mdm-linux-x86_64", name="x", version=TAG))


def test_constants_match_what_romarr_accepts():
    assert MODES == ("cli", "remote")
    assert set(CATEGORIES) == {"programs", "compressed", "documents", "music", "videos", "other"}


def test_credit_names_the_upstream_author_and_licence():
    assert "anefzaoui" in CREDIT and UPSTREAM_URL in CREDIT
    assert UPSTREAM_LICENSE in CREDIT
    readme = (ROOT / "README.md").read_text("utf-8")
    assert UPSTREAM_URL in readme and UPSTREAM_LICENSE in readme


def test_no_upstream_code_is_shipped():
    shipped = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
                     if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
                     and ".pytest_cache" not in p.parts)
    assert shipped == [".gitignore", "LICENSE", "README.md", "manifest.toml",
                       "mdm/__init__.py", "mdm/cores.py", "tests/test_mdm.py"]
