"""Offline suite for the eXo2romm ROM Hub plugin."""

from __future__ import annotations

import json
import sys
import warnings
import zipfile
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
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

from exo2romm.cores import COMMIT_API_URL, CoreListError, Cores, UnknownCore, parse_commit  # noqa: E402
from exo2romm.platforms import PLATFORMS  # noqa: E402
from exo2romm.repackager import (  # noqa: E402
    INSPIRED_NOTE,
    UPSTREAM_URL,
    inspect_exo_zip,
    repackage_batch,
    repackage_zip,
    rewrite_dosbox_conf_bytes,
)

SAMPLE_COMMIT_SHA = "f433980923abcdef0123456789abcdef01234567"
SAMPLE_COMMIT_JSON = json.dumps({"sha": SAMPLE_COMMIT_SHA})


class FakeGitHub:
    def __init__(self, body: str = SAMPLE_COMMIT_JSON, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url: str, params=None):
        self.calls.append(url)
        return HttpResponse(status_code=self.status, text=self.body)


def test_manifest_is_valid_and_credits_platform():
    manifest = parse_manifest((ROOT / "manifest.toml").read_text("utf-8"))
    assert manifest.slug == "exo2romm"
    assert manifest.rpp_version == "1"
    assert list(manifest.capabilities) == ["cores"]
    assert list(PLATFORMS) == ["dos"]


def test_cores_list_and_plan_allowlisted():
    manifest = parse_manifest((ROOT / "manifest.toml").read_text("utf-8"))
    http = FakeGitHub()
    cores = Cores(PluginContext(config={}, http=http))
    rows = cores.list()
    assert http.calls == [COMMIT_API_URL]
    assert len(rows) == 1
    assert rows[0].core_id == "exo2romm"
    assert "derkommutator" in rows[0].name
    assert rows[0].version == SAMPLE_COMMIT_SHA
    plan = cores.plan(rows[0])
    assert len(plan.files) == 1
    assert plan.files[0].filename == "eXo2romm.sh"
    assert url_allowed(plan.files[0].url, manifest.network)


def test_cores_refuses_unknown_id_and_rate_limit():
    cores = Cores(PluginContext(config={}, http=FakeGitHub(status=429)))
    with pytest.raises(CoreListError, match="rate limit"):
        cores.list()
    with pytest.raises(UnknownCore):
        cores.plan(CoreArtifact(core_id="other", name="other", version="1", system="dos"))
    assert parse_commit(SAMPLE_COMMIT_JSON)[0] == SAMPLE_COMMIT_SHA


def test_rewrite_dosbox_conf_preserves_crlf_comments_and_other_dirs():
    conf = (
        "[autoexec]\r\n"
        "# keep comment .\\eXoDOS\\Abaron\\CD\r\n"
        "REM rem mount c .\\eXoDOS\\Abaron\r\n"
        "mount c .\\eXoDOS\\Abaron\r\n"
        '@IMGMOUNT d ".\\exodos\\ABARON\\CD\\ABARON.CUE" -t cdrom\r\n'
        "mount e .\\eXoDOS\\OtherGame\\Extra\r\n"
        "boot .\\eXoDOS\\\r\n"
    ).encode("utf-8")
    edited, count = rewrite_dosbox_conf_bytes(conf, "Abaron")
    assert count == 3
    lines = edited.decode("utf-8").split("\r\n")
    assert lines[1] == "# keep comment .\\eXoDOS\\Abaron\\CD"
    assert lines[2] == "REM rem mount c .\\eXoDOS\\Abaron"
    assert lines[3] == "mount c .\\"
    assert lines[4] == '@IMGMOUNT d ".\\CD\\ABARON.CUE" -t cdrom'
    assert lines[5] == "mount e .\\eXoDOS\\OtherGame\\Extra"
    assert lines[6] == "boot .\\"


def test_rewrite_literal_regex_metachars_in_game_dir():
    conf = b"mount c .\\eXoDOS\\Game (1995) [v1.0]+\\disk1\n"
    edited, count = rewrite_dosbox_conf_bytes(conf, "Game (1995) [v1.0]+")
    assert count == 1
    assert edited == b"mount c .\\disk1\n"


def test_repackage_zip_replaces_existing_conf_without_duplicates_and_leaves_input_untouched(tmp_path):
    in_dir = tmp_path / "in"
    dos_dir = tmp_path / "!dos"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (dos_dir / "Abaron").mkdir(parents=True)
    (dos_dir / "Abaron" / "dosbox.conf").write_bytes(
        b"[autoexec]\r\nmount c .\\eXoDOS\\Abaron\r\n@c:\r\nAbaron.exe\r\n"
    )

    in_zip = in_dir / "Abaron (1995).zip"
    with zipfile.ZipFile(in_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Abaron/ABARON.EXE", b"MZ-GAME-PAYLOAD")
        zf.writestr("Abaron/dosbox.conf", b"stale original conf")
    before_bytes = in_zip.read_bytes()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = repackage_zip(in_zip, dos_dir, out_dir)
    assert not [w for w in caught if "Duplicate name" in str(w.message)]

    assert res.status == "processed"
    assert in_zip.read_bytes() == before_bytes
    out_zip = out_dir / "Abaron (1995).zip"
    assert out_zip.is_file()
    with zipfile.ZipFile(out_zip, "r") as zf:
        assert zf.namelist().count("Abaron/dosbox.conf") == 1
        assert zf.read("Abaron/ABARON.EXE") == b"MZ-GAME-PAYLOAD"
        assert b"mount c .\\\r\n" in zf.read("Abaron/dosbox.conf")

    # Resume-safe: second call skips existing output without overwriting.
    skipped = repackage_zip(in_zip, dos_dir, out_dir)
    assert skipped.status == "skipped_exists"

    # Explicit overwrite replaces the existing output zip.
    overwritten = repackage_zip(in_zip, dos_dir, out_dir, overwrite=True)
    assert overwritten.status == "processed"

    # Dry-run checks archive & rewrites without creating new output files.
    dry_out = tmp_path / "dry-out"
    dry = repackage_zip(in_zip, dos_dir, dry_out, dry_run=True)
    assert dry.status == "dry_run"
    assert dry.rewritten_lines == 1
    assert "mount c .\\" in dry.preview_conf
    assert not dry_out.exists()


def test_inspect_exo_zip_rejects_flat_multi_top_and_traversal(tmp_path):
    flat = tmp_path / "flat.zip"
    with zipfile.ZipFile(flat, "w") as zf:
        zf.writestr("GAME.EXE", b"1")
    with pytest.raises(ValueError, match="zip root"):
        inspect_exo_zip(flat)

    multi = tmp_path / "multi.zip"
    with zipfile.ZipFile(multi, "w") as zf:
        zf.writestr("One/a.exe", b"1")
        zf.writestr("Two/b.exe", b"2")
    with pytest.raises(ValueError, match="single top-level directory"):
        inspect_exo_zip(multi)

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape/a.exe", b"1")
    with pytest.raises(ValueError, match="traversal"):
        inspect_exo_zip(evil)


def test_repackage_batch_logs_skips_and_carries_credit(tmp_path):
    dos_dir = tmp_path / "!dos"
    out_dir = tmp_path / "out"
    (dos_dir / "OkGame").mkdir(parents=True)
    (dos_dir / "OkGame" / "dosbox.conf").write_text("[autoexec]\nmount c .\\eXoDOS\\OkGame\n", encoding="utf-8")

    ok_zip = tmp_path / "OkGame.zip"
    with zipfile.ZipFile(ok_zip, "w") as zf:
        zf.writestr("OkGame/GAME.EXE", b"ok")
    missing_zip = tmp_path / "NoTemplate.zip"
    with zipfile.ZipFile(missing_zip, "w") as zf:
        zf.writestr("NoTemplate/GAME.EXE", b"no")

    batch = repackage_batch([ok_zip, missing_zip], dos_dir=dos_dir, output_dir=out_dir)
    assert batch.processed == 1
    assert batch.skipped == 1
    assert not batch.ok
    assert UPSTREAM_URL in batch.upstream_url
    assert "derkommutator" in batch.credit
    assert "inspired to assist" in INSPIRED_NOTE
    err_log = Path(batch.error_log)
    assert err_log.is_file()
    assert "NoTemplate" in err_log.read_text(encoding="utf-8")
