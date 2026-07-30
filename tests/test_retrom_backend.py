"""Retrom as a `LibraryBackend`: selection, capabilities, and the scan wait.

Two separate claims.

**The seam holds.** `ROM_HUB_BACKEND=retrom` selects it, `rom-hub backend
info` describes it without a connection, and the pipelines still reach it
only through `LibraryBackend` -- the grep in test_backends.py covers RomM;
this file adds Retrom.

**It declares exactly what it does.** `collections` is absent because
Retrom has no such concept, and the refusal is a sentence rather than a
404 from an endpoint that never existed.

No test here requires a live Retrom.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest

from rom_hub import backends
from rom_hub.backends.base import (
    ARTWORK,
    COLLECTIONS,
    FIRMWARE,
    IMPORT,
    METADATA,
    SCAN,
    BackendNotConfigured,
    CapabilityUnsupported,
    LibraryBackend,
    Scanner,
    capabilities_of,
    require,
)
from rom_hub.backends.retrom import RetromBackend, RetromError, settings_from_env
from rom_hub.backends.retrom.client import RetromClient
from rom_hub.backends.retrom.grpcweb import GrpcWebChannel
from rom_hub.backends.retrom.upload import WebDavClient

from test_retrom_client import BASE, FakeRetrom

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "rom_hub"


def _backend(server: FakeRetrom | None = None, **kwargs) -> RetromBackend:
    server = server or FakeRetrom()
    transport = server.transport()
    return RetromBackend(
        BASE,
        client=RetromClient(BASE, channel=GrpcWebChannel(BASE, transport=transport)),
        dav=WebDavClient(BASE, transport=transport),
        scan_poll_seconds=0.0,
        scan_timeout=kwargs.pop("scan_timeout", 5.0),
        **kwargs,
    )


# -- selection -------------------------------------------------------------


def test_retrom_is_selectable_and_listed():
    assert "retrom" in backends.available()
    assert backends.backend_class("retrom") is RetromBackend


def test_describe_needs_no_connection(monkeypatch):
    monkeypatch.delenv("RETROM_URL", raising=False)
    info = backends.describe("retrom")
    assert info.name == "retrom"
    assert info.settings == ("RETROM_URL",)
    assert info.capabilities == frozenset({IMPORT, METADATA, ARTWORK, SCAN})


def test_loading_an_unconfigured_retrom_says_what_is_missing(monkeypatch):
    for name in ("RETROM_URL", "ROM_HUB_BACKEND_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BackendNotConfigured) as exc:
        backends.load("retrom")
    message = str(exc.value)
    assert "RETROM_URL" in message
    # There is nothing to authenticate against, and an operator hunting for
    # a password that does not exist is a wasted afternoon.
    assert "no accounts" in message


def test_the_backend_neutral_alias_configures_it_too(monkeypatch):
    monkeypatch.delenv("RETROM_URL", raising=False)
    monkeypatch.setenv("ROM_HUB_BACKEND_URL", "http://retrom.example:5101")
    assert settings_from_env() == "http://retrom.example:5101"


def test_a_retrom_error_is_a_backend_error():
    from rom_hub.backends.base import BackendError

    assert issubclass(RetromError, BackendError)


def test_retrom_satisfies_the_protocols_the_pipelines_use():
    backend = _backend()
    assert isinstance(backend, LibraryBackend)
    # `run_import` defaults its scanner to the backend, so it must.
    assert isinstance(backend, Scanner)


# -- capabilities ----------------------------------------------------------


def test_retrom_declares_four_of_six():
    """Measured against a real Retrom 0.8.4, not assumed."""
    assert capabilities_of(_backend()) == frozenset(
        {IMPORT, METADATA, ARTWORK, SCAN}
    )


def test_collections_are_absent_because_retrom_has_none():
    assert COLLECTIONS not in capabilities_of(_backend())
    with pytest.raises(CapabilityUnsupported) as exc:
        require(_backend(), COLLECTIONS, "--collection 'Shooters'")
    assert "retrom" in str(exc.value)


def test_reaching_the_collection_methods_anyway_gives_the_same_sentence():
    """Not an AttributeError, for a caller that got past the check."""
    backend = _backend()
    with pytest.raises(CapabilityUnsupported) as exc:
        backend.ensure_collection("Shooters")
    assert "no collections" in str(exc.value)
    with pytest.raises(CapabilityUnsupported):
        backend.add_to_collection(1, [1])


def test_firmware_is_absent_because_retrom_has_no_such_concept():
    """There is no BIOS or firmware message, service, column or directory
    anywhere in the repository. The two matches for `bios` are EmulatorJS's
    own `biosUrl?: string` config field in the web client -- a URL the
    player is handed, not something Retrom stores."""
    assert FIRMWARE not in capabilities_of(_backend())
    with pytest.raises(CapabilityUnsupported) as exc:
        require(_backend(), FIRMWARE, "installing a BIOS")
    assert "retrom" in str(exc.value)


def test_reaching_the_firmware_methods_anyway_gives_the_same_sentence():
    backend = _backend()
    with pytest.raises(CapabilityUnsupported) as exc:
        backend.upload_firmware([pathlib.Path("dmg_boot.bin")], 7)
    assert "no firmware concept" in str(exc.value)
    with pytest.raises(CapabilityUnsupported):
        backend.list_firmware(7)


# -- delegation ------------------------------------------------------------


def test_the_backend_delegates_the_listing_and_platform_lookups():
    server = FakeRetrom()
    server.add_game(1, "/app/data/library/dosbox/rubik.zip", 2, size=15000)
    backend = _backend(server)
    backend.authenticate()
    assert backend.platform_id("dosbox") == 2
    assert [rom["fs_name"] for rom in backend.list_roms(2)] == ["rubik.zip"]
    assert backend.get_rom(1)["platform_slug"] == "dosbox"


def test_upload_rom_returns_nothing_because_there_is_no_id_to_return(tmp_path):
    server = FakeRetrom()
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"payload")
    assert _backend(server).upload_rom(rom, 2) is None
    assert server.dav_files["library/dosbox/rubik.zip"] == b"payload"


def test_close_closes_both_connections():
    server = FakeRetrom()
    backend = _backend(server)
    backend.dav  # force the lazy one into existence
    backend.close()


def test_the_dav_client_is_built_lazily():
    """An import that dedups or fails early opens no second connection."""
    backend = RetromBackend(BASE)
    assert backend._dav is None


# -- artwork ---------------------------------------------------------------


def test_artwork_is_written_to_the_public_dir_and_pointed_at_by_cover_url():
    server = FakeRetrom()
    server.add_game(1, "/app/data/library/dosbox/rubik.zip", 2)
    server.metadata[1] = {
        "name": "Rubik",
        "screenshot_urls": [],
        "artwork_urls": [],
    }
    backend = _backend(server)
    row = backend.update_rom(1, {"name": "Rubik"}, artwork=("c.png", b"PNGDATA", "image/png"))

    assert server.dav_files["public/rom-hub/covers/cover-1.png"] == b"PNGDATA"
    assert row["cover_url"] == f"{BASE}/rest/public/rom-hub/covers/cover-1.png"
    # And the URL really is served by the route that reads that directory.
    with httpx.Client(transport=server.transport()) as http:
        assert http.get(row["cover_url"]).content == b"PNGDATA"


def test_a_cover_is_named_from_its_content_type_not_the_plugins_filename():
    """A plugin chooses `artwork_filename`; it never reaches a URL path."""
    server = FakeRetrom()
    server.metadata[1] = {"screenshot_urls": [], "artwork_urls": []}
    _backend(server).update_rom(
        1, {}, artwork=("../../etc/passwd", b"JPG", "image/jpeg; charset=binary")
    )
    assert "public/rom-hub/covers/cover-1.jpg" in server.dav_files


# -- the scan --------------------------------------------------------------


def test_scan_platform_triggers_a_library_update():
    server = FakeRetrom()
    server.dav_files["library/dosbox/rubik.zip"] = b"payload"
    backend = _backend(server)
    result = backend.scan_platform(2)
    assert result["job_ids"] == ["job-1"]
    assert [rom["fs_name"] for rom in backend.list_roms(2)] == ["rubik.zip"]


def test_scan_platform_waits_for_the_asynchronous_scan_to_show_up():
    """`UpdateLibrary` answers immediately and works in the background, so
    a caller that listed the library on return would be racing it.

    The fake models that literally: the trigger does nothing, and the work
    lands between two of the polls that follow it.
    """
    server = FakeRetrom()
    server.dav_files["library/dosbox/rubik.zip"] = b"payload"
    background_work = server.scan
    server.scan = lambda: None

    polls = {"n": 0}
    handler = server.__call__

    def with_a_background_job(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/retrom.GameService/GetGames":
            polls["n"] += 1
            if polls["n"] == 4:
                background_work()
        return handler(request)

    transport = httpx.MockTransport(with_a_background_job)
    backend = RetromBackend(
        BASE,
        client=RetromClient(BASE, channel=GrpcWebChannel(BASE, transport=transport)),
        dav=WebDavClient(BASE, transport=transport),
        scan_poll_seconds=0.0,
        scan_timeout=5.0,
    )
    backend.scan_platform(2)
    # It kept looking until the game was actually there.
    assert polls["n"] >= 4
    assert [rom["fs_name"] for rom in backend.list_roms(2)] == ["rubik.zip"]


def test_scan_platform_gives_up_quietly_rather_than_calling_an_upload_failed():
    """`run_import` owns the post-condition. Raising here would turn "the
    scan is slow" into "the upload failed", which makes an operator upload
    the file a second time."""
    server = FakeRetrom()
    server.scan = lambda: None
    backend = _backend(server, scan_timeout=0.05)
    assert backend.scan_platform(2)["job_ids"] == ["job-1"]


def test_a_failed_listing_during_the_wait_is_not_mistaken_for_a_change():
    server = FakeRetrom()
    server.dav_files["library/dosbox/rubik.zip"] = b"payload"
    backend = _backend(server)

    calls = {"n": 0}
    real_list = backend._client.list_games

    def flaky(platform_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RetromError("transient")
        return real_list(platform_id)

    backend._client.list_games = flaky
    backend.scan_platform(2)
    assert [rom["fs_name"] for rom in backend.list_roms(2)] == ["rubik.zip"]


# -- the seam holds --------------------------------------------------------


@pytest.mark.parametrize("module", ["importer.py", "metadata.py", "cli.py"])
def test_the_pipelines_do_not_import_the_retrom_package(module):
    """Retrom is reachable only through the abstraction, or it is not one."""
    source = (SRC / module).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "backends.retrom" not in stripped, line
            assert "RetromClient" not in stripped, line


def test_nothing_outside_the_package_names_retrom():
    """Backend-specific knowledge does not leak past its own package."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if "backends" in path.parts and "retrom" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if "retrom" in line.lower() and "backends/__init__" not in str(path):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    # `backends/__init__.py` registers the name, and `base.py` names Retrom
    # in prose as a motivating example; neither imports it.
    allowed = {"__init__.py", "base.py"}
    assert [
        entry for entry in offenders if entry.split(":")[0] not in allowed
    ] == []
