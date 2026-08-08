"""The receiving half: what the socket accepts, and what it refuses.

These tests bind a real `http.server` to loopback on port 0 and speak
real HTTP to it over `http.client`. No plugin, no library server and no
outbound connection is involved -- the fulfilment callable is a fake, and
in most tests it is a fake that blocks, because "the handler answered
before the work finished" is the one thing a mocked transport could not
prove.

Nothing here skips on any platform. `http.server` on loopback works
everywhere the suite runs, and a skip in this file would be a receiver
that stopped being tested.
"""

import http.client
import json
import socket
import threading
from urllib.parse import urlsplit

import pytest

from rom_hub.webhook import RequestEvent, RequestLog, RequestState
from rom_hub.webhook_server import (
    MAX_BODY_BYTES,
    QUEUE_MAX,
    TOKEN_MIN_LENGTH,
    WebhookServer,
    WeakToken,
)

TOKEN = "a-token-long-enough-to-be-a-secret"

PAYLOAD = {
    "type": "game_request",
    "title": "New Game Request: Chrono Trigger",
    "message": 'alice requested "Chrono Trigger"',
    "priority": 5,
    "timestamp": "2026-01-01T00:00:00.000Z",
    "data": {
        "request_id": "eac1cd44-5f6e-4f49-8ac1-9936066105a6",
        "user_id": "12",
        "game_title": "Chrono Trigger",
        "igdb_id": "1234",
        "platforms": ["Super Nintendo"],
        "request_type": "game",
    },
}


def body(**data):
    inner = dict(PAYLOAD["data"])
    inner.update(data)
    return json.dumps({**PAYLOAD, "data": inner}).encode()


class Recorder:
    """A fulfilment callable that records, and optionally blocks.

    `release` is what makes the 5-second contract testable: while it is
    held, no fulfilment can complete, so a 202 that comes back anyway
    came back without waiting for one.
    """

    def __init__(self, *, block=False):
        self.seen: list[str] = []
        self.started = threading.Event()
        self.done = threading.Event()
        self.release = threading.Event()
        if not block:
            self.release.set()

    def __call__(self, event, log):
        self.started.set()
        self.release.wait(timeout=10)
        self.seen.append(event.request_id)
        self.done.set()


class Client:
    """One HTTP conversation with a running server, and its log."""

    def __init__(self, server, log, notes):
        self.server = server
        self.log = log
        self.notes = notes

    @property
    def base(self) -> str:
        return f"/requests/{TOKEN}"

    def post(self, path=None, data=None, headers=None, method="POST"):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=10
        )
        try:
            conn.request(
                method,
                self.base if path is None else path,
                body=b"" if data is None else data,
                headers=headers or {"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def raw(self, request_bytes: bytes) -> bytes:
        """Speak bytes at the socket, for the malformed-framing cases."""
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=10)
        try:
            sock.sendall(request_bytes)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            sock.close()


@pytest.fixture
def running(tmp_path):
    """A started server, torn down whatever the test does."""
    made = []

    def start(fulfil=None, *, token=TOKEN, path="/requests"):
        notes: list[str] = []
        log = RequestLog(tmp_path / "requests.db")
        worker = fulfil or Recorder()
        server = WebhookServer(
            token=token,
            log=log,
            fulfil=worker,
            host="127.0.0.1",
            port=0,
            path=path,
            log_line=notes.append,
        )
        server.start()
        made.append((server, log, worker))
        return Client(server, log, notes)

    yield start
    for server, log, worker in made:
        # A test that left a blocking Recorder held would otherwise make
        # `stop()` wait out SHUTDOWN_GRACE for a worker that is never
        # coming back, and pay it once per server.
        release = getattr(worker, "release", None)
        if release is not None:
            release.set()
        server.stop()
        log.close()


# --- the happy path -----------------------------------------------------


def test_a_valid_payload_is_accepted_with_202(running):
    worker = Recorder()
    client = running(worker)
    status, raw = client.post(data=body())
    assert status == 202
    answer = json.loads(raw)
    assert answer["status"] == "accepted"
    assert answer["request_id"] == PAYLOAD["data"]["request_id"]


def test_a_valid_payload_queues_the_work(running):
    worker = Recorder()
    client = running(worker)
    client.post(data=body())
    assert worker.done.wait(timeout=10)
    assert worker.seen == [PAYLOAD["data"]["request_id"]]


def test_the_handler_returns_before_the_work_completes(running):
    """GG Requestz allows five seconds and logs a failure past it. The
    whole design rests on this: the response must not wait for a
    multi-gigabyte download."""
    worker = Recorder(block=True)
    client = running(worker)

    status, _ = client.post(data=body())

    assert status == 202
    # The fulfilment has started and cannot possibly have finished -- it is
    # still inside `release.wait()`.
    assert worker.started.wait(timeout=10)
    assert not worker.done.is_set()
    worker.release.set()
    assert worker.done.wait(timeout=10)


def test_the_token_is_accepted_as_a_query_parameter_too(running):
    worker = Recorder()
    client = running(worker)
    status, _ = client.post(path=f"/requests?token={TOKEN}", data=body())
    assert status == 202
    assert worker.done.wait(timeout=10)


def test_a_trailing_slash_on_the_token_path_is_accepted(running):
    client = running()
    status, _ = client.post(path=f"/requests/{TOKEN}/", data=body())
    assert status == 202


# --- refusals -----------------------------------------------------------


def test_a_wrong_token_gets_401(running):
    worker = Recorder()
    client = running(worker)
    status, raw = client.post(path="/requests/not-the-token", data=body())
    assert status == 401
    assert not worker.started.is_set()
    assert b"token" in raw.lower()


def test_a_missing_token_gets_401(running):
    client = running()
    assert client.post(path="/requests", data=body())[0] == 401


def test_a_wrong_query_token_gets_401(running):
    client = running()
    assert client.post(path="/requests?token=nope", data=body())[0] == 401


def test_an_unrelated_path_gets_401_not_404(running):
    """404 would tell a scanner which paths exist. There is exactly one."""
    client = running()
    assert client.post(path="/", data=body())[0] == 401
    assert client.post(path="/admin", data=body())[0] == 401


def test_a_token_prefix_is_not_enough(running):
    client = running()
    assert client.post(path=f"/requests/{TOKEN[:-1]}", data=body())[0] == 401


def test_a_token_with_something_after_it_is_not_enough(running):
    client = running()
    assert client.post(path=f"/requests/{TOKEN}extra", data=body())[0] == 401
    assert client.post(path=f"/requests/{TOKEN}/more", data=body())[0] == 401


def test_a_non_ascii_path_is_answered_rather_than_crashing_the_handler(running):
    """`BaseHTTPRequestHandler` decodes the request line as latin-1, so a
    path can be a non-ASCII `str` -- and `hmac.compare_digest` raises
    `TypeError` on those. Raising inside `authorised` would escape the
    handler's own `try` and leave the sender with a reset instead of a
    401."""
    client = running()
    answer = client.raw(
        b"POST /requests/caf\xe9 HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )
    assert b" 401 " in answer
    # And the server is still serving, which a swallowed TypeError in the
    # handler thread would not have prevented -- so this is the assertion
    # that makes the one above mean something.
    assert client.post(data=body())[0] == 202


def test_the_root_path_configuration_still_authorises(tmp_path):
    """ROM_HUB_WEBHOOK_PATH=/ normalises to the empty string, which is the
    one configuration where the route and the prefix logic can disagree."""
    log = RequestLog(tmp_path / "r.db")
    server = WebhookServer(
        token=TOKEN, log=log, fulfil=Recorder(), host="127.0.0.1", port=0, path="/"
    )
    server.start()
    try:
        client = Client(server, log, [])
        assert server.path == ""
        assert client.post(path=f"/{TOKEN}", data=body())[0] == 202
        assert client.post(path=f"/?token={TOKEN}", data=body(request_id="q"))[0] == 202
        assert client.post(path="/nope", data=body())[0] == 401
    finally:
        server.stop()
        log.close()


def test_the_wrong_method_on_the_right_path_gets_405(running):
    client = running()
    assert client.post(method="GET")[0] == 405
    assert client.post(method="PUT", data=body())[0] == 405


def test_the_wrong_method_on_the_wrong_path_still_gets_401(running):
    """Authorisation is decided before the method, so a GET cannot be used
    to find out whether a path exists."""
    client = running()
    assert client.post(path="/requests/nope", method="GET")[0] == 401


def test_a_malformed_body_gets_400_and_the_server_survives(running):
    worker = Recorder()
    client = running(worker)
    status, raw = client.post(data=b"{not json at all")
    assert status == 400
    assert not worker.started.is_set()
    # Still serving: the next valid request is accepted.
    assert client.post(data=body())[0] == 202
    assert worker.done.wait(timeout=10)


def test_a_json_body_missing_the_data_object_gets_400(running):
    client = running()
    status, raw = client.post(data=json.dumps({"type": "game_request"}).encode())
    assert status == 400
    assert b"data" in raw


def test_an_empty_body_gets_400(running):
    client = running()
    assert client.post(data=b"")[0] == 400


def test_an_oversized_body_is_refused_without_reading_it(running):
    client = running()
    status, raw = client.post(
        data=b"x",
        headers={
            "Content-Type": "application/json",
            # Lie about the length: the refusal must come from the header,
            # before a body byte is read, or the cap buys nothing.
            "Content-Length": str(MAX_BODY_BYTES + 1),
        },
    )
    assert status == 413
    assert b"large" in raw.lower()


def test_a_body_that_really_is_oversized_is_refused(running):
    client = running()
    payload = json.dumps(
        {"type": "game_request", "data": {"pad": "x" * (MAX_BODY_BYTES + 100)}}
    ).encode()
    assert client.post(data=payload)[0] == 413


def test_a_request_with_no_content_length_gets_411(running):
    """`http.server` cannot read a chunked body. Saying so beats reading
    zero bytes and calling the payload malformed."""
    client = running()
    answer = client.raw(
        f"POST /requests/{TOKEN} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        "Transfer-Encoding: chunked\r\n"
        "\r\n"
        "0\r\n\r\n".encode()
    )
    assert b" 411 " in answer


# --- idempotency --------------------------------------------------------


def test_a_repeated_request_id_is_accepted_but_not_imported_twice(running):
    worker = Recorder()
    client = running(worker)

    first = client.post(data=body())
    assert worker.done.wait(timeout=10)
    second = client.post(data=body())

    assert first[0] == 202
    assert json.loads(first[1])["status"] == "accepted"
    assert second[0] == 202
    assert json.loads(second[1])["status"] == "duplicate"
    assert worker.seen == [PAYLOAD["data"]["request_id"]]


def test_a_repeat_does_not_wait_for_the_first_to_finish(running):
    """A re-approval arriving while the first import is still running is
    the common case, not the rare one."""
    worker = Recorder(block=True)
    client = running(worker)
    client.post(data=body())
    assert worker.started.wait(timeout=10)

    status, raw = client.post(data=body())

    assert status == 202
    assert json.loads(raw)["status"] == "duplicate"
    worker.release.set()
    assert worker.done.wait(timeout=10)
    assert len(worker.seen) == 1


def test_a_different_request_id_is_a_different_request(running):
    worker = Recorder()
    client = running(worker)
    client.post(data=body())
    client.post(data=body(request_id="second-request"))
    for _ in range(100):
        if len(worker.seen) == 2:
            break
        worker.done.wait(timeout=0.1)
    assert sorted(worker.seen) == sorted(
        [PAYLOAD["data"]["request_id"], "second-request"]
    )


def test_the_row_is_written_before_the_response(running):
    """The claim is what makes a duplicate a no-op, so it cannot be left
    to the worker -- a second POST may arrive first."""
    worker = Recorder(block=True)
    client = running(worker)
    client.post(data=body())
    row = client.log.get(PAYLOAD["data"]["request_id"])
    assert row is not None
    assert row.state in (RequestState.RECEIVED, RequestState.SEARCHING)


# --- backpressure, logging, configuration -------------------------------


def test_a_full_queue_is_refused_rather_than_silently_dropped(running):
    worker = Recorder(block=True)
    client = running(worker)

    # One goes to the worker and blocks there -- waited for, so the count
    # below is not a race -- then QUEUE_MAX more fill the queue behind it,
    # and the next has nowhere to go.
    assert client.post(data=body(request_id="in-the-worker"))[0] == 202
    assert worker.started.wait(timeout=10)
    for i in range(QUEUE_MAX):
        assert client.post(data=body(request_id=f"req-{i}"))[0] == 202
    status, raw = client.post(data=body(request_id="one-too-many"))

    assert status == 503
    assert client.log.get("one-too-many").state is RequestState.FAILED
    assert "queue" in client.log.get("one-too-many").detail
    worker.release.set()


def test_the_token_never_reaches_the_log(running):
    """`BaseHTTPRequestHandler.log_request` writes the request line, and
    the request line contains the token."""
    client = running()
    client.post(data=body())
    client.post(path=f"/requests?token={TOKEN}", data=body(request_id="q"))
    client.post(path="/requests/wrong-token-value", data=body())
    joined = "\n".join(client.notes)
    assert joined  # something was logged, or this proves nothing
    assert TOKEN not in joined
    assert "wrong-token-value" not in joined


def test_the_log_line_says_what_happened(running):
    client = running()
    client.post(data=body())
    assert any("202" in line for line in client.notes)
    client.post(path="/nope", data=body())
    assert any("401" in line for line in client.notes)


def test_a_configured_path_is_honoured(running):
    client = running(path="/gg")
    conn_status, _ = client.post(path=f"/gg/{TOKEN}", data=body())
    assert conn_status == 202
    assert client.post(path=f"/requests/{TOKEN}", data=body())[0] == 401


def test_a_short_token_is_refused_at_construction(tmp_path):
    """The URL is the only gate there is, so a four-character one is not a
    configuration choice, it is an open door."""
    with RequestLog(tmp_path / "r.db") as log:
        with pytest.raises(WeakToken) as exc:
            WebhookServer(token="abc", log=log, fulfil=Recorder())
    assert str(TOKEN_MIN_LENGTH) in str(exc.value)


def test_an_empty_token_is_refused_at_construction(tmp_path):
    with RequestLog(tmp_path / "r.db") as log:
        with pytest.raises(WeakToken):
            WebhookServer(token="", log=log, fulfil=Recorder())


def test_the_url_it_prints_is_the_one_it_serves(running):
    """An operator pastes this into GG Requestz. If it is wrong they get a
    401 with nothing to tell them which half is at fault."""
    client = running()
    url = client.server.url()
    assert url.endswith(f"/requests/{TOKEN}")
    parts = urlsplit(url)
    assert parts.port == client.server.port
    assert client.post(path=parts.path, data=body())[0] == 202


def test_binding_off_loopback_is_announced(tmp_path):
    """Not refused -- an operator behind a reverse proxy has a real reason.
    But the URL secret is now on the network and they must be told."""
    with RequestLog(tmp_path / "r.db") as log:
        notes: list[str] = []
        server = WebhookServer(
            token=TOKEN,
            log=log,
            fulfil=Recorder(),
            host="0.0.0.0",
            port=0,
            log_line=notes.append,
        )
        try:
            server.start()
            assert any("loopback" in line for line in notes)
        finally:
            server.stop()


def test_stopping_a_server_that_never_started_is_harmless(tmp_path):
    with RequestLog(tmp_path / "r.db") as log:
        server = WebhookServer(token=TOKEN, log=log, fulfil=Recorder())
        server.stop()


def test_stopping_twice_from_two_threads_is_still_one_shutdown(tmp_path):
    """`serve_forever` stops in its own `finally` while the operator's
    Ctrl-C (or a test) stops it from another thread. Two callers is the
    normal case, and interleaving them used to raise on the way out of a
    clean shutdown."""
    log = RequestLog(tmp_path / "r.db")
    server = WebhookServer(
        token=TOKEN, log=log, fulfil=Recorder(), host="127.0.0.1", port=0
    )
    server.start()
    errors: list[BaseException] = []

    def stop():
        try:
            server.stop()
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            errors.append(exc)

    threads = [threading.Thread(target=stop) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    log.close()
    assert errors == []


def test_the_port_before_starting_is_the_one_that_was_asked_for(tmp_path):
    with RequestLog(tmp_path / "r.db") as log:
        server = WebhookServer(token=TOKEN, log=log, fulfil=Recorder(), port=8770)
        assert server.port == 8770
        assert ":8770" in server.url()


def test_a_wildcard_bind_is_not_printed_as_an_address(tmp_path):
    """0.0.0.0 is not somewhere the sender can post to, and printing it as
    if it were is how an operator debugs the wrong end."""
    with RequestLog(tmp_path / "r.db") as log:
        server = WebhookServer(
            token=TOKEN, log=log, fulfil=Recorder(), host="0.0.0.0", port=8770
        )
        assert "0.0.0.0" not in server.url()
        assert "<this-host>" in server.url()


def test_serve_forever_runs_until_stopped(tmp_path):
    """The entry point the CLI uses. Its loop is what keeps the process
    alive, so a bug in it is a receiver that exits immediately."""
    log = RequestLog(tmp_path / "r.db")
    worker = Recorder()
    notes: list[str] = []
    server = WebhookServer(
        token=TOKEN,
        log=log,
        fulfil=worker,
        host="127.0.0.1",
        port=0,
        log_line=notes.append,
    )
    blocking = threading.Thread(target=server.serve_forever, daemon=True)
    blocking.start()
    try:
        for _ in range(100):
            if server.port:
                break
            threading.Event().wait(0.05)
        client = Client(server, log, notes)
        assert client.post(data=body())[0] == 202
        assert worker.done.wait(timeout=10)
    finally:
        server.stop()
        blocking.join(timeout=10)
        log.close()
    assert not blocking.is_alive()


def test_a_fulfilment_that_raises_does_not_stop_the_worker(tmp_path):
    """`webhook.fulfil` records its own failures, so reaching the worker's
    own guard means the injected callable itself broke. The next request
    must still be served."""
    log = RequestLog(tmp_path / "r.db")
    seen: list[str] = []
    notes: list[str] = []

    def explode(event, request_log):
        seen.append(event.request_id)
        if len(seen) == 1:
            raise RuntimeError("worker exploded")

    server = WebhookServer(
        token=TOKEN,
        log=log,
        fulfil=explode,
        host="127.0.0.1",
        port=0,
        log_line=notes.append,
    )
    server.start()
    try:
        client = Client(server, log, notes)
        assert client.post(data=body(request_id="first"))[0] == 202
        assert client.post(data=body(request_id="second"))[0] == 202
        for _ in range(200):
            if len(seen) == 2:
                break
            threading.Event().wait(0.05)
    finally:
        server.stop()
        log.close()
    assert seen == ["first", "second"]
    assert any("worker exploded" in line for line in notes)


def test_a_receiver_error_is_500_and_the_server_keeps_listening(tmp_path):
    """Anything unforeseen inside `accept` -- here a log that cannot be
    written -- must not take the endpoint down with it."""
    log = RequestLog(tmp_path / "r.db")
    notes: list[str] = []
    server = WebhookServer(
        token=TOKEN,
        log=log,
        fulfil=Recorder(),
        host="127.0.0.1",
        port=0,
        log_line=notes.append,
    )
    server.start()
    try:
        client = Client(server, log, notes)
        log.close()  # Every claim from here raises.
        status, raw = client.post(data=body())
        assert status == 500
        assert b"closed database" in raw or b"ProgrammingError" in raw
        # Still answering: a second request gets the same 500, not a
        # connection refused.
        assert client.post(data=body(request_id="another"))[0] == 500
    finally:
        server.stop()


def test_every_other_method_is_answered_rather_than_hanging(running):
    """`http.server` answers 501 for a verb with no handler, which reads as
    a broken receiver. Each one it might plausibly be sent is routed."""
    client = running()
    assert client.post(method="DELETE")[0] == 405
    assert client.post(method="HEAD")[0] == 405
    assert client.post(path="/nope", method="DELETE")[0] == 401


def test_a_content_length_that_is_not_a_number_gets_400(running):
    client = running()
    answer = client.raw(
        f"POST /requests/{TOKEN} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: banana\r\n"
        "\r\n".encode()
    )
    assert b" 400 " in answer


def test_a_negative_content_length_gets_400(running):
    client = running()
    answer = client.raw(
        f"POST /requests/{TOKEN} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: -5\r\n"
        "\r\n".encode()
    )
    assert b" 400 " in answer


def test_in_flight_rows_are_failed_when_the_server_starts(running):
    """A row left SEARCHING by a killed process is stranded: nothing is
    coming back for it, and a repeat POST is a no-op by design."""
    client = running()
    client.log.claim(
        RequestEvent(request_id="stranded", game_title="X", request_type="game")
    )
    client.log.begin("stranded", RequestState.IMPORTING)

    # A second server over the same file is what a restart looks like.
    running()

    assert client.log.get("stranded").state is RequestState.FAILED
