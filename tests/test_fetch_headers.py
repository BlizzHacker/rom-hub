"""Authenticated downloads (rom-hub#50): FetchFile.headers end to end.

Three layers, each tested against the real code:

1. `types.validate_headers` / `FetchFile(headers=...)` -- what a plugin may
   put in a header at all (injection, transport-owned names, bounds).
2. `HttpDownloader.download(..., headers=...)` -- that the headers reach
   the wire on every hop of a redirect chain inside the allowlist, that
   they coexist with the resume Range without either clobbering the other,
   and that a hop outside the allowlist is refused before the request
   carrying the credential is ever built.
3. `run_import` with a plan whose file carries headers -- the full
   pipeline passes them through to the downloader.

All over `httpx.MockTransport`; no socket is opened.
"""

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from rom_hub.importer import DownloadError, HttpDownloader, run_import
from rom_hub.jobs import JobQueue
from rom_hub.types import MAX_HEADERS_PER_FILE, FetchFile, FetchPlan

AUTH = {"Authorization": "***"}


def _file(**kw):
    kw.setdefault("url", "https://allowed.example/g.zip")
    kw.setdefault("filename", "g.zip")
    return FetchFile(**kw)


# -- layer 1: what a FetchFile may carry --------------------------------------


def test_a_fetch_file_may_carry_an_authorization_header():
    f = _file(headers=dict(AUTH))
    assert f.headers == AUTH


def test_headers_default_to_absent_and_an_empty_dict_normalises_to_absent():
    assert _file().headers is None
    assert _file(headers={}).headers is None


@pytest.mark.parametrize(
    "value",
    [
        "LOW key:secret\r\nX-Injected: yes",
        "LOW key:secret\nX-Injected: yes",
        "LOW key:secret\r\n\r\n<html>",
        "nul\x00value",
    ],
)
def test_a_header_value_carrying_a_control_character_is_refused(value):
    with pytest.raises(ValidationError, match="control character"):
        _file(headers={"Authorization": value})


@pytest.mark.parametrize(
    "name",
    ["X Bad", "X:Y", "X(Y)", "bad\nname", "", "héllo", "a\tb"],
)
def test_a_header_name_that_is_not_a_token_is_refused(name):
    with pytest.raises(ValidationError, match="RFC 7230"):
        _file(headers={name: "v"})


@pytest.mark.parametrize(
    "name",
    [
        "Host",
        "host",
        "HOST",
        "Content-Length",
        "Transfer-Encoding",
        "Connection",
        "Upgrade",
        "TE",
        "Cookie",
        "cookie",
        "Range",
        "range",
    ],
)
def test_transport_owned_headers_are_refused_case_insensitively(name):
    with pytest.raises(ValidationError, match="owned by the downloader"):
        _file(headers={name: "anything"})


def test_the_header_count_is_bounded():
    many = {f"X-H{i}": "v" for i in range(MAX_HEADERS_PER_FILE + 1)}
    with pytest.raises(ValidationError, match="at most"):
        _file(headers=many)


def test_a_header_at_the_count_limit_is_accepted():
    many = {f"X-H{i}": "v" for i in range(MAX_HEADERS_PER_FILE)}
    assert len(_file(headers=many).headers) == MAX_HEADERS_PER_FILE


def test_an_overlong_value_is_refused():
    with pytest.raises(ValidationError, match="character limit"):
        _file(headers={"Authorization": "x" * 9000})


def test_headers_survive_a_plan_roundtrip():
    """The broker re-establishes a plan from model_dump() host-side; the
    headers must survive that exactly like every other field."""
    plan = FetchPlan(files=[_file(headers=dict(AUTH))], platform="dos")
    again = FetchPlan(**plan.model_dump())
    assert again.files[0].headers == AUTH


# -- layer 2: what HttpDownloader does with them ------------------------------


def _recording_transport(seen, status=200, content=b"payload"):
    def handler(request):
        seen.append(
            (str(request.url), {k.lower(): v for k, v in request.headers.items()})
        )
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handler)


def test_headers_reach_the_wire(tmp_path):
    seen: list = []
    dest = tmp_path / "g.zip"
    with HttpDownloader(
        allowlist=["allowed.example"], transport=_recording_transport(seen)
    ) as dl:
        dl.download("https://allowed.example/g.zip", dest, headers=dict(AUTH))
    assert dest.read_bytes() == b"payload"
    assert seen[0][1]["authorization"] == AUTH["Authorization"]


def test_headers_ride_every_hop_of_an_allowed_redirect_chain(tmp_path):
    """Archive.org 302s to iaNNNN.us.archive.org; an S3 Authorization must
    survive the hop or the authenticated download breaks exactly where the
    real one happens."""

    def handler(request):
        seen.append((str(request.url), {k.lower(): v for k, v in request.headers.items()}))
        if request.url.host == "allowed.example":
            return httpx.Response(
                302, headers={"location": "https://cdn.allowed.example/g.zip"}
            )
        return httpx.Response(200, content=b"payload")

    seen: list = []
    dest = tmp_path / "g.zip"
    with HttpDownloader(
        allowlist=["allowed.example", "*.allowed.example"],
        transport=httpx.MockTransport(handler),
    ) as dl:
        dl.download("https://allowed.example/g.zip", dest, headers=dict(AUTH))
    assert [u for u, _ in seen] == [
        "https://allowed.example/g.zip",
        "https://cdn.allowed.example/g.zip",
    ]
    assert all(h["authorization"] == AUTH["Authorization"] for _, h in seen)


def test_a_disallowed_redirect_hop_is_refused_before_the_request_goes_out(tmp_path):
    """The credential must not travel to a host outside the allowlist --
    the refusal happens at check_url, before the request is built, so the
    header never reaches the wire for the bad hop at all."""

    def handler(request):
        seen.append((str(request.url), {k.lower(): v for k, v in request.headers.items()}))
        return httpx.Response(
            302, headers={"location": "https://evil.example/g.zip"}
        )

    seen: list = []
    dest = tmp_path / "g.zip"
    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as dl:
        with pytest.raises(DownloadError, match="allowlist"):
            dl.download("https://allowed.example/g.zip", dest, headers=dict(AUTH))
    # exactly one request was made: the first hop. The evil hop was never
    # issued, so the Authorization never saw a host outside the allowlist.
    assert len(seen) == 1
    assert not dest.exists()


def test_the_resume_range_and_plugin_headers_coexist(tmp_path):
    """A partial file on disk means the downloader adds its own Range. The
    plugin's headers must still be present, and Range must be the
    downloader's, not a plugin's (validate_headers refuses a plugin Range,
    so this asserts the downloader side of that split)."""

    def handler(request):
        seen.append({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(
            206,
            content=b"tail",
            headers={"content-length": "4"},
        )

    seen: list = []
    dest = tmp_path / "g.zip"
    dest.write_bytes(b"head")  # a partial download, 4 bytes in
    with HttpDownloader(
        allowlist=["allowed.example"], transport=httpx.MockTransport(handler)
    ) as dl:
        dl.download(
            "https://allowed.example/g.zip",
            dest,
            expected_size=8,
            headers=dict(AUTH),
        )
    assert dest.read_bytes() == b"headtail"
    assert seen[0]["range"] == "bytes=4-"
    assert seen[0]["authorization"] == AUTH["Authorization"]


def test_no_headers_means_no_extra_request_headers(tmp_path):
    seen: list = []
    dest = tmp_path / "g.zip"
    with HttpDownloader(
        allowlist=["allowed.example"], transport=_recording_transport(seen)
    ) as dl:
        dl.download("https://allowed.example/g.zip", dest)
    assert "authorization" not in seen[0][1]


# -- layer 3: run_import threads FetchFile.headers through --------------------


class _RecordingDownloader:
    def __init__(self):
        self.calls = []

    def download(self, url, dest, expected_size=None, headers=None):
        self.calls.append((url, expected_size, headers))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"payload")
        return Path(dest)


def test_run_import_passes_the_plans_headers_to_the_downloader(tmp_path, monkeypatch):
    from tests.test_importer import (
        RESULT,
        FakePlugin,
        FakeRomm,
        FakeScanner,
        FakeUpload,
    )

    plan = FetchPlan(
        files=[_file(headers=dict(AUTH))],
        platform="dos",
    )
    fake_upload = FakeUpload()
    monkeypatch.setattr(
        FakeRomm,
        "upload_rom",
        lambda self, path, platform_id: fake_upload(self, path, platform_id),
    )
    romm = FakeRomm()
    queue = JobQueue(tmp_path / "jobs.db")
    downloader = _RecordingDownloader()
    result = run_import(
        FakePlugin(plan=plan),
        RESULT,
        backend=romm,
        queue=queue,
        download_dir=tmp_path / "dl",
        downloader=downloader,
        scanner=FakeScanner(romm),
    )
    assert downloader.calls == [("https://allowed.example/g.zip", None, AUTH)]
    # and the import actually completed -- threading the headers must not
    # have disturbed the pipeline it rides in.
    assert result.state.name == "DONE"
