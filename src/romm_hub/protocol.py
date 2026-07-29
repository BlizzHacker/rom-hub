"""Newline-delimited JSON framing for host <-> plugin RPC.

One JSON object per line. json.dumps escapes embedded newlines, so the
framing holds for arbitrary payloads. The size cap stops a misbehaving
plugin from exhausting host memory with a single line.
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
        line = stream.readline()
        if line == "":
            return None  # clean EOF
        if len(line) > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                f"message too large: {len(line)} bytes exceeds {MAX_MESSAGE_BYTES}"
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
