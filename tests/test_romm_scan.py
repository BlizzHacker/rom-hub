"""Tests for the post-upload library scan trigger.

No test here may require a live RomM. `FakeSocketClient` stands in for
`socketio.Client` and records everything the scanner does to it, so the
ordering that actually matters -- handlers registered *before* the emit --
is observable rather than assumed.
"""

from __future__ import annotations

import pytest

from rom_hub.romm.scan import (
    SCAN_DONE_EVENT,
    SCAN_DONE_KO_EVENT,
    SCAN_EVENT,
    SOCKETIO_PATH,
    ScanError,
    SocketIOScanner,
)


class FakeSocketClient:
    """A stand-in for `socketio.Client`.

    `reply` is fired from inside `emit()`, which is the harshest ordering
    the real client can produce: the server answering before the caller
    has begun waiting. The scanner must still see it.
    """

    def __init__(self, reply=None, connect_error=None):
        self.handlers: dict[str, object] = {}
        self.connect_calls: list[tuple[str, dict]] = []
        self.emitted: list[tuple[str, dict]] = []
        self.disconnect_calls = 0
        self.handlers_registered_at_emit: set[str] | None = None
        self._reply = reply
        self._connect_error = connect_error

    def on(self, event, handler):
        self.handlers[event] = handler

    def connect(self, url, **kwargs):
        if self._connect_error is not None:
            raise self._connect_error
        self.connect_calls.append((url, kwargs))

    def emit(self, event, data=None):
        self.handlers_registered_at_emit = set(self.handlers)
        self.emitted.append((event, data))
        if self._reply is not None:
            name, payload = self._reply
            handler = self.handlers.get(name)
            if handler is not None:
                handler(payload)

    def disconnect(self):
        self.disconnect_calls += 1


class FakeRomm:
    def __init__(self, token="tok-abc", base_url="http://rommtest:8080"):
        self._token = token
        self.base_url = base_url
        self.token_calls = 0

    def bearer_token(self):
        self.token_calls += 1
        return self._token


DONE_STATS = {"scanned_platforms": 1, "scanned_roms": 1, "new_roms": 1}


def _scanner(client, **kwargs):
    romm = kwargs.pop("romm", None) or FakeRomm()
    return SocketIOScanner(romm, client_factory=lambda: client, **kwargs)


# -- what gets sent -------------------------------------------------------


def test_the_scan_is_scoped_to_one_platform_and_asks_for_no_metadata():
    client = FakeSocketClient(reply=(SCAN_DONE_EVENT, DONE_STATS))
    _scanner(client).scan_platform(7)

    assert len(client.emitted) == 1
    event, options = client.emitted[0]
    assert event == SCAN_EVENT
    # Scoped to the one platform just uploaded to -- never a whole-library
    # sweep, which on a real library is hours of work.
    assert options["platforms"] == [7]
    assert options["type"] == "quick"
    # An empty apis list is what keeps this from firing a metadata sweep
    # against IGDB/MobyGames on every single import.
    assert options["apis"] == []


def test_the_socket_carries_the_same_bearer_token_the_rest_client_uses():
    client = FakeSocketClient(reply=(SCAN_DONE_EVENT, DONE_STATS))
    romm = FakeRomm(token="tok-xyz")
    _scanner(client, romm=romm).scan_platform(7)

    assert romm.token_calls == 1
    _url, kwargs = client.connect_calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer tok-xyz"
    # Some RomM builds gate the scan socket on a connect-time auth payload
    # rather than the header, so both carry the token.
    assert kwargs["auth"]["token"] == "tok-xyz"


def test_connect_targets_romms_own_socketio_path_not_the_default():
    client = FakeSocketClient(reply=(SCAN_DONE_EVENT, DONE_STATS))
    _scanner(client, romm=FakeRomm(base_url="http://rommtest:8080")).scan_platform(1)

    url, kwargs = client.connect_calls[0]
    assert url == "http://rommtest:8080"
    # RomM mounts its socket.io app at /ws/socket.io; python-socketio would
    # otherwise use /socket.io and 404 forever.
    assert kwargs["socketio_path"] == SOCKETIO_PATH


def test_the_completion_handler_is_registered_before_the_scan_is_emitted():
    """The race that makes a fire-and-forget scanner look like it works.

    A scan on an empty platform finishes in milliseconds. If `scan:done`
    were subscribed after the emit, the event would already have been and
    gone and the scanner would wait out its full timeout on a scan that
    had, in fact, succeeded.
    """
    client = FakeSocketClient(reply=(SCAN_DONE_EVENT, DONE_STATS))
    _scanner(client).scan_platform(7)

    assert client.handlers_registered_at_emit is not None
    assert SCAN_DONE_EVENT in client.handlers_registered_at_emit
    assert SCAN_DONE_KO_EVENT in client.handlers_registered_at_emit


# -- what comes back ------------------------------------------------------


def test_a_completed_scan_returns_the_servers_own_stats():
    client = FakeSocketClient(reply=(SCAN_DONE_EVENT, DONE_STATS))
    assert _scanner(client).scan_platform(7) == DONE_STATS


def test_a_scan_already_in_progress_raises_instead_of_waiting_it_out():
    """RomM answers a concurrent scan with `scan:done_ko`, not silence."""
    client = FakeSocketClient(
        reply=(SCAN_DONE_KO_EVENT, "A scan is already in progress")
    )
    with pytest.raises(ScanError) as exc:
        _scanner(client).scan_platform(7)
    assert "A scan is already in progress" in str(exc.value)


def test_a_scan_that_never_completes_times_out_rather_than_hanging():
    client = FakeSocketClient(reply=None)  # server never answers
    with pytest.raises(ScanError) as exc:
        _scanner(client, timeout=0.05).scan_platform(7)
    assert "timed out" in str(exc.value).lower()


def test_a_connect_failure_surfaces_as_a_scan_error():
    client = FakeSocketClient(connect_error=RuntimeError("connection refused"))
    with pytest.raises(ScanError) as exc:
        _scanner(client).scan_platform(7)
    assert "connection refused" in str(exc.value)
    assert client.emitted == []


# -- cleanup --------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        (SCAN_DONE_EVENT, DONE_STATS),
        (SCAN_DONE_KO_EVENT, "boom"),
        None,
    ],
    ids=["done", "done_ko", "timeout"],
)
def test_the_socket_is_always_disconnected(reply):
    """A leaked socket keeps a RomM worker slot and an ssh tunnel open."""
    client = FakeSocketClient(reply=reply)
    try:
        _scanner(client, timeout=0.05).scan_platform(7)
    except ScanError:
        pass
    assert client.disconnect_calls == 1


def test_a_disconnect_that_itself_fails_does_not_mask_the_scan_result():
    class Rude(FakeSocketClient):
        def disconnect(self):
            super().disconnect()
            raise RuntimeError("socket already gone")

    client = Rude(reply=(SCAN_DONE_EVENT, DONE_STATS))
    assert _scanner(client).scan_platform(7) == DONE_STATS
    assert client.disconnect_calls == 1
