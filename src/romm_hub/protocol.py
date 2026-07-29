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

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
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
        line = stream.readline(MAX_MESSAGE_BYTES + 1)
        if line == "":
            return None  # clean EOF
        if len(line) > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                f"message too large: exceeds {MAX_MESSAGE_BYTES}; the stream is "
                "now desynchronised and the peer must be killed"
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
        if msg.get("kind") not in VALID_KINDS:
            raise ProtocolError(
                f"message has missing or invalid kind: {msg.get('kind')!r}"
            )
        return msg
