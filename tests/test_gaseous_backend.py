"""The Gaseous `LibraryBackend`: capabilities, listing, upload, the wait.

Every call is mocked with httpx.MockTransport. No test here may require a
live Gaseous instance.

The point of this file is the seam. `test_backends.py` asserts that the
pipelines only ever touch a `LibraryBackend`; these tests assert that a
second, materially different implementation of it -- cookie auth instead
of a token, an async import queue instead of a socket, and two
capabilities instead of five -- satisfies the same protocol and degrades
where it must.
"""

import httpx
import pytest

from rom_hub.backends.base import (
    ARTWORK,
    COLLECTIONS,
    IMPORT,
    METADATA,
    SCAN,
    CapabilityUnsupported,
    LibraryBackend,
    Scanner,
    capabilities_of,
)
from rom_hub.backends.gaseous import (
    BACKEND_NAME,
    CAPABILITIES,
    GaseousBackend,
    GaseousError,
    ImportWaiter,
    settings_from_env,
)
from rom_hub.backends.base import BackendNotConfigured

from test_gaseous_client import (  # noqa: E402 - sibling test module, shared fixtures
    API,
    ROM_ON_DOS,
    ROM_ON_UNKNOWN,
    _handler,
)


def _backend(calls, **kwargs):
    return GaseousBackend(
        "https://gaseous.example",
        "romhub@example.com",
        "pw",
        transport=httpx.MockTransport(_handler(calls, **kwargs)),
    )


# -- registration and identity ---------------------------------------------


def test_gaseous_satisfies_the_protocol():
    assert isinstance(_backend([]), LibraryBackend)


def test_gaseous_satisfies_the_scanner_protocol():
    assert isinstance(_backend([]), Scanner)


def test_the_backend_is_selectable_by_name():
    from rom_hub import backends

    assert BACKEND_NAME == "gaseous"
    assert "gaseous" in backends.available()
    assert backends.backend_class("gaseous") is GaseousBackend


def test_describe_reports_gaseous_without_connecting(monkeypatch):
    from rom_hub import backends

    for name in ("GASEOUS_URL", "GASEOUS_USER", "GASEOUS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    info = backends.describe("gaseous")
    assert info.name == "gaseous"
    assert info.capabilities == frozenset({IMPORT, SCAN})
    assert info.settings == ("GASEOUS_URL", "GASEOUS_USER", "GASEOUS_PASSWORD")


def test_settings_report_every_missing_variable_at_once(monkeypatch):
    monkeypatch.setenv("GASEOUS_URL", "http://gaseous.example:5198")
    for name in (
        "GASEOUS_USER",
        "GASEOUS_PASSWORD",
        "ROM_HUB_BACKEND_USER",
        "ROM_HUB_BACKEND_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BackendNotConfigured) as exc:
        settings_from_env()
    named = str(exc.value).split(" not set.")[0]
    assert "GASEOUS_USER" in named and "GASEOUS_PASSWORD" in named
    assert "GASEOUS_URL" not in named


def test_the_backend_neutral_setting_aliases_work(monkeypatch):
    for name in ("GASEOUS_URL", "GASEOUS_USER", "GASEOUS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROM_HUB_BACKEND_URL", "http://g.example")
    monkeypatch.setenv("ROM_HUB_BACKEND_USER", "a@b.c")
    monkeypatch.setenv("ROM_HUB_BACKEND_PASSWORD", "pw")
    assert settings_from_env() == ("http://g.example", "a@b.c", "pw")


def test_a_gaseous_error_is_a_backend_error():
    """One name for every backend's failures, so `cli.main` needs one
    except clause rather than one per backend."""
    from rom_hub.backends.base import BackendError

    assert issubclass(GaseousError, BackendError)


# -- capabilities ----------------------------------------------------------


def test_gaseous_declares_exactly_import_and_scan():
    assert CAPABILITIES == frozenset({IMPORT, SCAN})
    assert capabilities_of(_backend([])) == frozenset({IMPORT, SCAN})


@pytest.mark.parametrize("capability", [COLLECTIONS, METADATA, ARTWORK])
def test_gaseous_does_not_claim_what_it_cannot_do(capability):
    """Each of these was checked against gaseous-server's source and a
    running v2.0.0-rc.3. An overclaim here becomes a 404 halfway through
    an import, with the ROM half-filed."""
    assert capability not in capabilities_of(_backend([]))


def test_scan_is_declared_because_an_upload_alone_registers_nothing():
    """POST /Roms stages the file and queues it; ImportQueueProcessor
    creates the database row. Until it has, the rom is not listable."""
    assert SCAN in CAPABILITIES


# -- refusals --------------------------------------------------------------


def test_collections_refuse_with_the_reason():
    backend = _backend([])
    with pytest.raises(CapabilityUnsupported) as exc:
        backend.ensure_collection("Shooters")
    assert "Collections" in str(exc.value)

    with pytest.raises(CapabilityUnsupported):
        backend.add_to_collection(1, [2])


def test_metadata_writes_refuse_rather_than_approximate():
    """`LibraryBackend.update_rom`: a backend that cannot express a partial
    update must refuse rather than approximate one."""
    backend = _backend([])
    with pytest.raises(CapabilityUnsupported) as exc:
        backend.update_rom(1, {"name": "Rubik"})
    assert "name" in str(exc.value)


def test_get_rom_refuses_because_gaseous_has_no_id_only_lookup():
    backend = _backend([])
    with pytest.raises(CapabilityUnsupported) as exc:
        backend.get_rom(7)
    assert "7" in str(exc.value)


def test_a_refusal_never_opens_a_connection():
    """The point of a declared capability is that it costs nothing to be
    refused."""
    calls = []
    backend = _backend(calls)
    for call in (
        lambda: backend.ensure_collection("x"),
        lambda: backend.update_rom(1, {"name": "x"}),
        lambda: backend.get_rom(1),
    ):
        with pytest.raises(CapabilityUnsupported):
            call()
    assert calls == []


# -- listing ---------------------------------------------------------------


def test_list_roms_covers_the_asked_for_platform_and_the_unknown_one():
    """Gaseous files an unrecognised ROM under platform 0 regardless of
    OverridePlatformId, so a listing scoped strictly to the requested
    platform would be empty of exactly the roms the Hub imported."""
    calls = []
    backend = _backend(
        calls,
        games=[
            {"metadataMapId": 1, "platformIds": [0]},
            {"metadataMapId": 2, "platformIds": [13]},
        ],
        roms={(1, 0): [ROM_ON_UNKNOWN], (2, 13): [ROM_ON_DOS]},
    )
    roms = backend.list_roms(13)
    names = {rom["fs_name"] for rom in roms}
    assert names == {"rubik.zip", "identified.img"}


def test_list_roms_does_not_return_other_platforms():
    """Widening to the whole library would let find_by_filename skip an
    import because an unrelated ROM of the same name exists on a
    different, correctly-identified platform -- a false skip."""
    calls = []
    other = dict(ROM_ON_DOS, platformId=19, id=3, name="snes-game.sfc")
    backend = _backend(
        calls,
        games=[{"metadataMapId": 3, "platformIds": [19]}],
        roms={(3, 19): [other]},
    )
    assert backend.list_roms(13) == []


def test_list_roms_translates_into_the_vocabulary_dedup_reads():
    """`rom_hub.dedup` reads fs_name/crc_hash/md5_hash/sha1_hash. That
    vocabulary is the host's; a backend that answered in its own spelling
    would make the shared dedup code learn every backend's field names."""
    calls = []
    backend = _backend(
        calls,
        games=[{"metadataMapId": 1, "platformIds": [0]}],
        roms={(1, 0): [ROM_ON_UNKNOWN]},
    )
    (rom,) = backend.list_roms(13)
    assert rom["fs_name"] == "rubik.zip"
    assert rom["crc_hash"] == ROM_ON_UNKNOWN["crc"]
    assert rom["md5_hash"] == ROM_ON_UNKNOWN["md5"]
    assert rom["sha1_hash"] == ROM_ON_UNKNOWN["sha1"]
    # Gaseous' own keys survive, so a job's debug output is still readable.
    assert rom["relativePath"] == "unknown/rubik/rubik.zip"


def test_list_roms_works_with_the_hub_dedup_helpers():
    """The translation is only worth anything if the real dedup functions
    accept it, so this asserts against them rather than against the keys."""
    from rom_hub.dedup import FileHashes, find_by_filename, find_duplicate

    calls = []
    backend = _backend(
        calls,
        games=[{"metadataMapId": 1, "platformIds": [0]}],
        roms={(1, 0): [ROM_ON_UNKNOWN]},
    )
    library = backend.list_roms(13)

    assert find_by_filename("rubik.zip", library) is not None
    hashes = FileHashes(
        crc32=ROM_ON_UNKNOWN["crc"],
        md5=ROM_ON_UNKNOWN["md5"],
        sha1=ROM_ON_UNKNOWN["sha1"],
    )
    assert find_duplicate(hashes, library) is not None


def test_a_rom_reachable_from_two_games_is_listed_once():
    calls = []
    backend = _backend(
        calls,
        games=[
            {"metadataMapId": 1, "platformIds": [0, 13]},
            {"metadataMapId": 1, "platformIds": [0]},
        ],
        roms={(1, 0): [ROM_ON_UNKNOWN]},
    )
    assert len(backend.list_roms(13)) == 1


def test_a_missing_hash_is_none_rather_than_absent():
    """dedup treats a non-string as "no match"; a predictable shape beats
    a key that sometimes exists."""
    calls = []
    partial = {k: v for k, v in ROM_ON_UNKNOWN.items() if k != "sha1"}
    backend = _backend(
        calls,
        games=[{"metadataMapId": 1, "platformIds": [0]}],
        roms={(1, 0): [partial]},
    )
    (rom,) = backend.list_roms(13)
    assert rom["sha1_hash"] is None
    assert rom["md5_hash"] == ROM_ON_UNKNOWN["md5"]


# -- upload and the wait ---------------------------------------------------


def test_upload_returns_nothing_and_remembers_the_session(tmp_path):
    """The interface says upload_rom returns nothing, and Gaseous' 200
    carries an import session id rather than a rom id -- so the id is
    remembered here for scan_platform to wait on."""
    calls = []
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"PK\x03\x04")
    backend = _backend(calls)

    assert backend.upload_rom(rom, 13) is None
    assert backend._pending_sessions == ["4a5f2b1c-0000-4000-8000-abcdefabcdef"]


def test_scan_platform_waits_for_the_uploaded_session(tmp_path):
    calls = []
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"PK\x03\x04")
    session = "4a5f2b1c-0000-4000-8000-abcdefabcdef"
    backend = _backend(
        calls, imports=[{"sessionId": session, "state": "Completed"}]
    )
    backend.upload_rom(rom, 13)
    backend.scan_platform(13)

    assert any(c.url.path == f"{API}/Roms/Imports" for c in calls)
    # Drained: a second scan must not re-wait on a finished session.
    assert backend._pending_sessions == []


def test_scan_platform_is_a_no_op_when_nothing_was_uploaded():
    """A dedup-only import must not block on someone else's queue."""
    calls = []
    backend = _backend(calls)
    assert backend.scan_platform(13) is None
    assert calls == []


# -- the import waiter -----------------------------------------------------


def _waiter(client, **kwargs):
    slept = []
    kwargs.setdefault("poll_interval", 5.0)
    return (
        ImportWaiter(
            client,
            sleep=slept.append,
            monotonic=_FakeClock(slept),
            **kwargs,
        ),
        slept,
    )


class _FakeClock:
    """Advances only when the waiter sleeps, so a timeout test is instant."""

    def __init__(self, slept):
        self._slept = slept

    def __call__(self):
        return sum(self._slept)


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def import_states(self):
        self.calls += 1
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def test_the_waiter_polls_until_the_session_completes():
    client = _FakeClient(
        [
            [{"sessionId": "s1", "state": "Pending"}],
            [{"sessionId": "s1", "state": "Processing"}],
            [{"sessionId": "s1", "state": "Completed"}],
        ]
    )
    waiter, slept = _waiter(client)
    waiter.wait_for(["s1"])
    assert client.calls == 3
    assert slept == [5.0, 5.0]


def test_a_vanished_session_counts_as_finished():
    """RemoveOldImportStates prunes the queue on a timer, so an absent
    session completed a while ago. Treating absence as pending would hang
    on exactly the imports that went fine."""
    client = _FakeClient([[]])
    waiter, _ = _waiter(client)
    waiter.wait_for(["s1"])
    assert client.calls == 1


def test_an_import_error_is_raised_with_the_servers_message():
    client = _FakeClient(
        [[{"sessionId": "s1", "state": "Pending", "errorMessage": "bad archive",
           "fileName": "rubik.zip"}]]
    )
    waiter, _ = _waiter(client)
    with pytest.raises(GaseousError) as exc:
        waiter.wait_for(["s1"])
    assert "bad archive" in str(exc.value)


def test_a_stuck_queue_times_out_and_blames_the_right_thing():
    """If every import sits at Pending the background task is not running,
    which on a fresh server means first-run setup was never finished. An
    operator told only "the import did not complete" goes looking at the
    ROM instead."""
    client = _FakeClient([[{"sessionId": "s1", "state": "Pending"}]])
    waiter, _ = _waiter(client, timeout=20.0)
    with pytest.raises(GaseousError) as exc:
        waiter.wait_for(["s1"])
    message = str(exc.value)
    assert "do not upload them again" in message
    assert "first-run setup" in message


def test_waiting_for_nothing_makes_no_request():
    client = _FakeClient([[]])
    waiter, _ = _waiter(client)
    waiter.wait_for([])
    assert client.calls == 0
