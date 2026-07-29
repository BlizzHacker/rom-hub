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


def test_embedded_newlines_do_not_break_framing():
    buf = io.StringIO()
    write_message(buf, {"kind": "result", "id": "p1", "result": "a\nb"})
    buf.seek(0)
    assert read_message(buf)["result"] == "a\nb"
