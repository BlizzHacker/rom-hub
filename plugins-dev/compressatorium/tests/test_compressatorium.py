"""Offline suite for the Compressatorium ROM Hub plugin.

Runs against a ROM Hub checkout beside this one (../rom-hub/src) or an
installed `rom-hub` package. No network: GitHub is faked at ctx.http.
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

from compressatorium import CREDIT, UPSTREAM_URL  # noqa: E402
from compressatorium.cores import (  # noqa: E402
    COMPOSE_FILENAME, CORE_ID, LATEST_RELEASE_URL, CoreListError, Cores,
    UnknownCore, parse_release,
)


class FakeGitHub:
    def __init__(self, body: str = json.dumps({"tag_name": "v4.5.2"}), status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url: str, params=None):
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


def test_manifest_is_valid_and_keeps_the_key_secret():
    manifest = _manifest()
    assert manifest.slug == "compressatorium"
    assert manifest.rpp_version == "1"
    assert list(manifest.capabilities) == ["cores"]
    assert manifest.license == "MIT"
    assert manifest.config_schema["api_key"]["type"] == "secret"
    assert "default" not in manifest.config_schema["api_key"]
    assert manifest.config_schema["url"]["default"] == ""


def test_list_reads_the_latest_release():
    http = FakeGitHub()
    [core] = _cores(http).list()

    assert http.calls == [LATEST_RELEASE_URL]
    assert core.core_id == CORE_ID and core.version == "v4.5.2"
    assert "pacnpal" in core.name


def test_plan_fetches_upstream_compose_at_that_tag_inside_the_allowlist():
    manifest = _manifest()
    plan = _cores(FakeGitHub()).plan(CoreArtifact(core_id=CORE_ID, name="x", version="v4.5.2"))

    [file] = plan.files
    assert file.url == ("https://raw.githubusercontent.com/pacnpal/compressatorium/"
                        "v4.5.2/docker-compose.yml")
    assert file.filename == COMPOSE_FILENAME
    assert url_allowed(file.url, list(manifest.network))
    assert url_allowed(LATEST_RELEASE_URL, list(manifest.network))


@pytest.mark.parametrize("status", [403, 429, 500])
def test_list_reports_upstream_failures(status):
    with pytest.raises(CoreListError):
        _cores(FakeGitHub(status=status)).list()


@pytest.mark.parametrize("body", ["not json", "[]", json.dumps({"tag_name": "../evil"}),
                                  json.dumps({"tag_name": ""})])
def test_a_bad_release_body_is_refused(body):
    with pytest.raises(CoreListError):
        parse_release(body)


def test_plan_refuses_unknown_cores_and_bad_tags():
    cores = _cores(FakeGitHub())
    with pytest.raises(UnknownCore):
        cores.plan(CoreArtifact(core_id="other", name="x", version="v1.0"))
    with pytest.raises(CoreListError):
        cores.plan(CoreArtifact(core_id=CORE_ID, name="x", version="main;rm"))


def test_credit_names_the_upstream_author():
    assert "pacnpal" in CREDIT and UPSTREAM_URL in CREDIT
    readme = (ROOT / "README.md").read_text("utf-8")
    assert UPSTREAM_URL in readme
