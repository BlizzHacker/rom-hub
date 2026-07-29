"""Newline-delimited JSON framing for host <-> plugin RPC.

One JSON object per line. json.dumps escapes embedded newlines, so the
framing holds for arbitrary payloads.

The size cap stops a misbehaving plugin from exhausting host memory with a
single line, which requires bounding the *read* rather than measuring what
came back: `readline()` with no argument scans to the next newline however
far away it is, so a cap checked afterwards has already lost. Exceeding the
cap therefore leaves the stream mid-line and permanently desynchronised —
callers must kill the peer, never try to resync.
"""

import json
from typing import IO

# Characters, not bytes: these are text-mode streams and json.dumps runs with
# ensure_ascii=False, so a CJK payload is up to 3x this in bytes on the wire.
# The name says which one it is.
MAX_MESSAGE_CHARS = 8 * 1024 * 1024
VALID_KINDS = frozenset({"call", "result", "error"})


class ProtocolError(Exception):
    """The peer sent something that is not a well-formed RPP message."""


def write_message(stream: IO[str], msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    stream.write(line + "\n")
    stream.flush()


def read_message(stream: IO[str]) -> dict | None:
    while True:
        # Bounded: at most one character beyond the cap is ever resident, and
        # that character is only there to prove the cap was exceeded.
        line = stream.readline(MAX_MESSAGE_CHARS + 1)
        if line == "":
            return None  # clean EOF
        if len(line) > MAX_MESSAGE_CHARS:
            raise ProtocolError(
                f"message too large: exceeds {MAX_MESSAGE_CHARS} characters; "
                "the stream is now desynchronised and the peer must be killed"
            )
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON on the wire: {exc}") from exc
        if not isinstance(msg, dict):
            raise ProtocolError("each message must be a JSON object")
        kind = msg.get("kind")
        if kind not in VALID_KINDS:
            raise ProtocolError(f"message has missing or invalid kind: {kind!r}")
        _check_shape(kind, msg)
        return msg


def _check_shape(kind: str, msg: dict) -> None:
    """Reject a frame whose kind promises fields it does not carry.

    Both peers index these fields directly, and both peers are reading
    something the other side wrote -- for the host, that is arbitrary plugin
    code's stdout. Validating once here is what lets `msg["result"]` and
    `msg["error"]["message"]` stay unguarded at their call sites instead of
    escaping as KeyError or TypeError past a documented PluginCallError
    contract.
    """
    if not isinstance(msg.get("id"), str):
        raise ProtocolError(
            f"{kind} frame has a missing or non-string id: {msg.get('id')!r}"
        )
    if kind == "call":
        if not isinstance(msg.get("method"), str):
            raise ProtocolError(
                f"call frame has a missing or non-string method: "
                f"{msg.get('method')!r}"
            )
        params = msg.get("params")
        if params is not None and not isinstance(params, dict):
            raise ProtocolError(
                f"call frame params must be an object, got {type(params).__name__}"
            )
    elif kind == "result":
        if "result" not in msg:
            raise ProtocolError("result frame carries no 'result'")
    elif kind == "error":
        error = msg.get("error")
        if not isinstance(error, dict):
            raise ProtocolError(
                f"error frame's 'error' must be an object, got "
                f"{type(error).__name__}"
            )
        if not isinstance(error.get("message"), str):
            raise ProtocolError(
                f"error frame has a missing or non-string error.message: "
                f"{error.get('message')!r}"
            )
