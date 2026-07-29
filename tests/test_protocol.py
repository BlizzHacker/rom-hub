import io

import pytest

from romm_hub.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    read_message,
    write_message,
)


def test_roundtrip():
    buf = io.StringIO()
    write_message(buf, {"kind": "call", "id": "h1", "method": "ping", "params": {}})
    buf.seek(0)
    msg = read_message(buf)
    assert msg == {"kind": "call", "id": "h1", "method": "ping", "params": {}}


def test_eof_returns_none():
    assert read_message(io.StringIO("")) is None


def test_blank_lines_skipped():
    buf = io.StringIO('\n\n{"kind": "result", "id": "p1", "result": 3}\n')
    assert read_message(buf)["result"] == 3


def test_invalid_json_raises():
    with pytest.raises(ProtocolError, match="invalid JSON"):
        read_message(io.StringIO("{not json}\n"))


def test_non_object_raises():
    with pytest.raises(ProtocolError, match="object"):
        read_message(io.StringIO("[1, 2, 3]\n"))


def test_missing_kind_raises():
    with pytest.raises(ProtocolError, match="kind"):
        read_message(io.StringIO('{"id": "h1"}\n'))


def test_oversize_line_raises():
    huge = '{"kind": "result", "id": "p1", "result": "' + "x" * MAX_MESSAGE_BYTES + '"}\n'
    with pytest.raises(ProtocolError, match="too large"):
        read_message(io.StringIO(huge))


class EndlessLine:
    """A stream whose current line never ends, counting what it hands out.

    A cap that is checked *after* readline() returns has already lost: the
    whole line is resident by then. This stream makes that visible.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.handed_out = 0

    def readline(self, size: int = -1) -> str:
        if size is None or size < 0:
            size = self.limit  # unbounded readline: hand over everything
        n = max(0, min(size, self.limit - self.handed_out))
        self.handed_out += n
        return "x" * n


def test_oversize_line_is_refused_before_it_is_buffered():
    stream = EndlessLine(limit=MAX_MESSAGE_BYTES + 1000)
    with pytest.raises(ProtocolError, match="too large"):
        read_message(stream)
    # The point of the cap: at most the cap (plus the one character that
    # proves the cap was exceeded) may ever reach host memory.
    assert stream.handed_out <= MAX_MESSAGE_BYTES + 1, (
        f"host buffered {stream.handed_out} chars for a capped "
        f"{MAX_MESSAGE_BYTES}-char message"
    )


def test_embedded_newlines_do_not_break_framing():
    buf = io.StringIO()
    write_message(buf, {"kind": "result", "id": "p1", "result": "a\nb"})
    buf.seek(0)
    assert read_message(buf)["result"] == "a\nb"
