"""Tests for the Igir (1G1R Collection Manager) plugin against captured v5.8.0 release data."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_hub_sdk import CoreArtifact  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

from igir.cores import (  # noqa: E402
    RELEASE_API_URL,
    TARGETS,
    CoreListError,
    Cores,
    NoAssetForTarget,
    UnknownTarget,
    parse_release,
)
from igir.planner import (  # noqa: E402
    build_1g1r_command,
    render_cleanup_script,
)

RELEASE_V5_8_0 = json.dumps(
    {
        "tag_name": "v5.8.0",
        "name": "v5.8.0",
        "html_url": "https://github.com/emmercm/igir/releases/tag/v5.8.0",
        "assets": [
            {
                "name": "igir-5.8.0-Linux-amd64.tar.gz",
                "size": 44538686,
                "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-Linux-amd64.tar.gz",
            },
            {
                "name": "igir-5.8.0-Linux-arm64v8.tar.gz",
                "size": 44282278,
                "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-Linux-arm64v8.tar.gz",
            },
            {
                "name": "igir-5.8.0-macOS-arm64.tar.gz",
                "size": 32503874,
                "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-macOS-arm64.tar.gz",
            },
            {
                "name": "igir-5.8.0-macOS-x64.tar.gz",
                "size": 35345687,
                "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-macOS-x64.tar.gz",
            },
            {
                "name": "igir-5.8.0-Windows-arm64.zip",
                "size": 43340632,
                "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-Windows-arm64.zip",
            },
            {
                "name": "igir-5.8.0-Windows-x64.zip",
                "size": 47922309,
                "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-Windows-x64.zip",
            },
        ],
    }
)


class FakeGitHub:
    def __init__(self, body: str = RELEASE_V5_8_0, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url: str, params=None):
        self.calls.append(url)
        return HttpResponse(status_code=self.status, text=self.body)


def make_cores(config: dict | None = None, http: FakeGitHub | None = None):
    http = http or FakeGitHub()
    return Cores(PluginContext(config=config or {}, http=http)), http


@pytest.mark.parametrize(
    "target,expected_asset",
    [
        ("linux/x86_64", "igir-5.8.0-Linux-amd64.tar.gz"),
        ("linux/aarch64", "igir-5.8.0-Linux-arm64v8.tar.gz"),
        ("windows/x86_64", "igir-5.8.0-Windows-x64.zip"),
        ("windows/arm64", "igir-5.8.0-Windows-arm64.zip"),
        ("macos/x86_64", "igir-5.8.0-macOS-x64.tar.gz"),
        ("macos/arm64", "igir-5.8.0-macOS-arm64.tar.gz"),
    ],
)
def test_every_supported_target_selects_exact_v5_8_0_asset(target, expected_asset):
    rel = parse_release(RELEASE_V5_8_0)
    assert rel.tag == "v5.8.0"
    assert len(rel.assets) == 6
    chosen = rel.select(target)
    assert chosen.name == expected_asset
    assert chosen.url.endswith(f"/v5.8.0/{expected_asset}")


def test_list_returns_core_artifact_with_gpl3_and_version():
    cores, http = make_cores({"target": "linux/x86_64"})
    rows = cores.list()
    assert http.calls == [RELEASE_API_URL]
    assert len(rows) == 1
    row = rows[0]
    assert row.core_id == "igir"
    assert row.version == "5.8.0"
    assert "[GPL-3.0]" in row.name
    assert "igir-5.8.0-Linux-amd64.tar.gz" in row.name


def test_plan_returns_fetch_plan_for_configured_target():
    cores, _ = make_cores({"target": "windows/x86_64"})
    artifact = CoreArtifact(core_id="igir", name="Igir", version="5.8.0", system="1G1R")
    plan = cores.plan(artifact)
    assert len(plan.files) == 1
    f = plan.files[0]
    assert f.filename == "igir-5.8.0-Windows-x64.zip"
    assert f.size_bytes == 47922309
    assert f.url.startswith("https://github.com/emmercm/igir/releases/download/v5.8.0/")


def test_unknown_target_refuses_by_name():
    cores, _ = make_cores({"target": "freebsd/riscv64"})
    with pytest.raises(UnknownTarget, match="freebsd/riscv64"):
        cores.list()


def test_rate_limit_surfaces_clear_message():
    cores, _ = make_cores(http=FakeGitHub(status=403))
    with pytest.raises(CoreListError, match="rate limit"):
        cores.list()


def test_missing_asset_in_upstream_release_refuses():
    partial = json.dumps(
        {
            "tag_name": "v5.8.0",
            "assets": [
                {
                    "name": "igir-5.8.0-Linux-amd64.tar.gz",
                    "size": 100,
                    "browser_download_url": "https://github.com/emmercm/igir/releases/download/v5.8.0/igir-5.8.0-Linux-amd64.tar.gz",
                }
            ],
        }
    )
    rel = parse_release(partial)
    with pytest.raises(NoAssetForTarget, match="windows/x86_64"):
        rel.select("windows/x86_64")


def test_1g1r_command_builder_and_cleanup_script():
    cmd = build_1g1r_command(
        {
            "commands": ["copy", "extract", "test", "clean", "report"],
            "input_dirs": ["/downloads/rom-hub"],
            "output_dir": "/library/roms/{romm}",
            "dat_dirs": ["/config/dats"],
            "single_1g1r": True,
            "prefer_regions": ["USA", "WORLD"],
            "prefer_languages": ["EN"],
            "dry_run": False,
        }
    )
    assert cmd[:6] == ["igir", "copy", "extract", "test", "clean", "report"]
    assert "--single" in cmd
    assert "--prefer-verified" in cmd
    assert "--prefer-region" in cmd
    assert "USA,WORLD" in cmd
    assert "/library/roms/{romm}" in cmd

    dry_cmd = build_1g1r_command({"commands": ["move", "clean", "report"], "dry_run": True})
    assert "move" not in dry_cmd
    assert "clean" not in dry_cmd
    assert dry_cmd[:2] == ["igir", "report"] or "test" in dry_cmd

    script = render_cleanup_script(backend="es-de", library_roms_dir="/roms")
    assert "/roms/{es}" in script
    assert "--single" in script
    assert set(TARGETS) == {
        "linux/x86_64",
        "linux/aarch64",
        "windows/x86_64",
        "windows/arm64",
        "macos/x86_64",
        "macos/arm64",
    }
