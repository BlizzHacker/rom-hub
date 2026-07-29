"""The `LibraryBackend` seam itself: selection, and RomM's implementation.

Two separate claims are under test here.

**The seam is real.** `rom_hub.importer` and `rom_hub.metadata` reach a
library server only through `LibraryBackend`, and the module-level greps
below are the cheapest possible guard against that quietly stopping being
true: a `from rom_hub.backends.romm import ...` added to the pipeline for
one convenient call would put RomM back in the middle of it, and nothing
else in the suite would notice.

**RomM still does what it did.** `RommBackend` is a delegation layer, so
the tests for it are delegation tests -- the RomM *behaviour* (the
required `scope`, the bodyless 201, the chunk-size formula) is still
tested where it lives, against `RommClient`, in test_romm_*.py.
"""

from __future__ import annotations

import pathlib

import pytest

from rom_hub import backends
from rom_hub.backends.base import (
    ALL_CAPABILITIES,
    ARTWORK,
    COLLECTIONS,
    IMPORT,
    METADATA,
    SCAN,
    BackendError,
    BackendNotConfigured,
    LibraryBackend,
    Scanner,
    UnknownBackend,
    capabilities_of,
)
from rom_hub.backends.romm import RommBackend, RommError, settings_from_env

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "rom_hub"


# -- selection -------------------------------------------------------------


def test_romm_is_the_default_backend():
    assert backends.DEFAULT_BACKEND == "romm"
    assert "romm" in backends.available()


def test_an_unknown_backend_names_the_ones_that_exist():
    with pytest.raises(UnknownBackend) as exc:
        backends.backend_class("gaseous")
    assert "romm" in str(exc.value)


def test_describe_needs_no_connection(monkeypatch):
    """The operator most likely to ask is the one who cannot connect."""
    for name in ("ROMM_URL", "ROMM_USER", "ROMM_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    info = backends.describe("romm")
    assert info.name == "romm"
    assert info.capabilities == ALL_CAPABILITIES
    assert info.settings == ("ROMM_URL", "ROMM_USER", "ROMM_PASSWORD")


def test_loading_an_unconfigured_backend_says_what_is_missing(monkeypatch):
    for name in (
        "ROMM_URL",
        "ROMM_USER",
        "ROMM_PASSWORD",
        "ROM_HUB_BACKEND_URL",
        "ROM_HUB_BACKEND_USER",
        "ROM_HUB_BACKEND_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BackendNotConfigured) as exc:
        backends.load("romm")
    assert "ROMM_URL" in str(exc.value)


def test_settings_from_env_reports_every_missing_variable_at_once(monkeypatch):
    monkeypatch.setenv("ROMM_URL", "http://romm.example")
    for name in (
        "ROMM_USER",
        "ROMM_PASSWORD",
        "ROM_HUB_BACKEND_USER",
        "ROM_HUB_BACKEND_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BackendNotConfigured) as exc:
        settings_from_env()
    message = str(exc.value)
    # The list of what is missing, not the worked example that follows it.
    named = message.split(" not set.")[0]
    assert "ROMM_USER" in named and "ROMM_PASSWORD" in named
    assert "ROMM_URL" not in named


def test_a_romm_error_is_a_backend_error():
    """One name for every backend's failures, so `cli.main` needs one
    except clause rather than one per backend."""
    assert issubclass(RommError, BackendError)


# -- the RomM implementation ----------------------------------------------


class FakeClient:
    """Records what the backend delegates, without any HTTP."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.closed = False

    def _record(self, *args):
        self.calls.append(args)

    def authenticate(self):
        self._record("authenticate")

    def platform_id(self, slug):
        self._record("platform_id", slug)
        return 7

    def list_roms(self, platform_id):
        self._record("list_roms", platform_id)
        return [{"id": 1}]

    def get_rom(self, rom_id):
        self._record("get_rom", rom_id)
        return {"id": rom_id}

    def update_rom(self, rom_id, fields, artwork=None):
        self._record("update_rom", rom_id, dict(fields), artwork)
        return {"id": rom_id}

    def ensure_collection(self, name):
        self._record("ensure_collection", name)
        return 3

    def add_to_collection(self, collection_id, rom_ids):
        self._record("add_to_collection", collection_id, list(rom_ids))

    def close(self):
        self.closed = True

    @property
    def base_url(self):
        return "http://romm.example"


class FakeScanner:
    def __init__(self):
        self.calls: list[int] = []

    def scan_platform(self, platform_id):
        self.calls.append(platform_id)
        return {"scanned_platforms": 1}


def _backend(client=None, scanner=None):
    return RommBackend(
        "http://romm.example",
        "u",
        "p",
        client=client or FakeClient(),
        scanner=scanner,
    )


def test_romm_satisfies_the_protocol():
    assert isinstance(_backend(), LibraryBackend)


def test_romm_satisfies_the_scanner_protocol():
    """`run_import` defaults its scanner to the backend, so it must."""
    assert isinstance(_backend(), Scanner)


def test_romm_declares_every_capability():
    """Measured against a real RomM 4.9.2, not assumed -- which is exactly
    why it is stated as data instead of taken for granted by callers."""
    assert capabilities_of(_backend()) == frozenset(
        {IMPORT, COLLECTIONS, METADATA, ARTWORK, SCAN}
    )


@pytest.mark.parametrize(
    "call, expected",
    [
        (lambda b: b.authenticate(), ("authenticate",)),
        (lambda b: b.platform_id("dos"), ("platform_id", "dos")),
        (lambda b: b.list_roms(7), ("list_roms", 7)),
        (lambda b: b.get_rom(42), ("get_rom", 42)),
        (lambda b: b.ensure_collection("C"), ("ensure_collection", "C")),
        (lambda b: b.add_to_collection(3, [1]), ("add_to_collection", 3, [1])),
    ],
)
def test_the_backend_delegates_to_the_client(call, expected):
    client = FakeClient()
    call(_backend(client))
    assert client.calls == [expected]


def test_update_rom_passes_the_artwork_tuple_through():
    client = FakeClient()
    art = ("c.png", b"\x89PNG", "image/png")
    _backend(client).update_rom(1, {"name": "Doom"}, artwork=art)
    assert client.calls == [("update_rom", 1, {"name": "Doom"}, art)]


def test_upload_rom_drives_the_chunked_upload(tmp_path, monkeypatch):
    """The pipeline no longer calls `upload_file`; the backend does."""
    seen: list[tuple] = []
    monkeypatch.setattr(
        "rom_hub.backends.romm.backend.upload_file",
        lambda client, path, platform_id: seen.append((path, platform_id)) or {},
    )
    rom = tmp_path / "g.zip"
    rom.write_bytes(b"payload")
    assert _backend().upload_rom(rom, 7) is None
    assert seen == [(rom, 7)]


def test_scan_platform_uses_the_injected_scanner():
    scanner = FakeScanner()
    _backend(scanner=scanner).scan_platform(7)
    assert scanner.calls == [7]


def test_scan_platform_builds_a_socketio_scanner_when_none_was_given():
    """Lazily, so an import that dedups or fails early opens no socket."""
    from rom_hub.backends.romm.scan import SocketIOScanner

    backend = _backend()
    assert backend._scanner is None
    with pytest.raises(Exception):
        # No socket.io server here; the point is only that it got that far
        # by constructing the real scanner rather than by doing nothing.
        backend.scan_platform(7)
    assert isinstance(backend._scanner, SocketIOScanner)


def test_close_closes_the_client():
    client = FakeClient()
    _backend(client).close()
    assert client.closed


def test_capabilities_of_a_broken_backend_is_nothing_not_everything():
    """The assumption that fails silently is the one that uploads first."""

    class Broken:
        name = "broken"

        def capabilities(self):
            raise RuntimeError("boom")

    class Nonsense:
        name = "nonsense"

        def capabilities(self):
            return "collections"  # a string is not a set of capabilities

    assert capabilities_of(Broken()) == frozenset()
    # A bare string would otherwise iterate into single characters.
    assert capabilities_of(Nonsense()) == frozenset()


# -- the seam holds --------------------------------------------------------


@pytest.mark.parametrize("module", ["importer.py", "metadata.py", "cli.py"])
def test_the_pipelines_do_not_import_the_romm_package(module):
    """RomM is reachable only through the abstraction, or it is not one.

    A grep rather than an architecture-test framework, because the rule
    is one line long and the failure it prevents is someone adding
    `from rom_hub.backends.romm import RommClient` for one convenient
    call.
    """
    source = (SRC / module).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "backends.romm" not in stripped, line
            assert "RommClient" not in stripped, line
