"""Just enough protobuf wire format to talk to Retrom, and nothing more.

## Why this exists instead of generated stubs

Retrom's write API is gRPC only (see `client`), so the Hub has to speak
protobuf. The two conventional ways to get there both cost more than they
are worth *here*:

* `grpcio` + `grpcio-tools` pulls a large platform-specific binary wheel
  and a second IO stack into a sidecar whose entire network surface is
  otherwise one `httpx.Client` -- and it still could not be used, because
  the Hub talks **gRPC-Web over HTTP/1.1** (see `grpcweb`), which grpcio
  does not speak.
* `protobuf` + checked-in `*_pb2.py` would add a runtime dependency to
  *every* `rom-hub` install, including the ones that never select this
  backend, and several thousand lines of generated descriptor tables --
  to carry about twenty scalar fields.

The wire format itself is small, frozen, and public. What is written here
is the subset those twenty fields actually use: varints, length-delimited
values, and the two rules needed to survive a server that knows more
fields than this file does.

## The two rules that matter

**Unknown fields are skipped, not rejected.** `decode` returns whatever it
was given, keyed by field number, and silently steps over anything it does
not recognise. A Retrom release that adds a field to `Game` must not break
an import; a field this file never reads costs nothing to walk past.

**Nothing here knows what a message means.** `decode` yields
`{field_number: [values]}` -- there is no schema, no required field, no
type checking against a descriptor. The meaning lives in `client`, beside
the `.proto` line it was read from, so a field number and its name are
never more than a few lines apart.

Field numbers are cited in `client` against
`packages/codegen/protos/retrom/**` at the revision they were read from.
"""

from __future__ import annotations

# Wire types, from the protobuf encoding spec. Only these four exist in
# anything Retrom sends; group start/end (3, 4) were removed in proto3.
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_FIXED32 = 5


class ProtoError(ValueError):
    """A payload that is not decodable protobuf.

    A `ValueError` because that is what it is -- malformed input -- and
    because `client` turns it into a `RetromError` with the RPC's name
    attached, which is the part an operator can act on.
    """


# -- encoding --------------------------------------------------------------


def encode_varint(value: int) -> bytes:
    """Base-128 varint, as protobuf writes an unsigned integer.

    Negative values are sign-extended to 64 bits first, which is what
    protobuf does for a negative `int32`/`int64` -- ten bytes, not an
    error. No id the Hub sends is ever negative, but a codec that
    silently emitted something else for one would be a trap.
    """
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return encode_varint((field << 3) | wire_type)


def varint_field(field: int, value: int) -> bytes:
    """One `int32`/`int64`/`enum` field."""
    return _tag(field, WIRE_VARINT) + encode_varint(value)


def bool_field(field: int, value: bool) -> bytes:
    return varint_field(field, 1 if value else 0)


def bytes_field(field: int, value: bytes) -> bytes:
    """One length-delimited field: `bytes`, or an embedded message."""
    return _tag(field, WIRE_LEN) + encode_varint(len(value)) + value


def string_field(field: int, value: str) -> bytes:
    return bytes_field(field, value.encode("utf-8"))


def packed_varints(field: int, values: list[int]) -> bytes:
    """A `repeated int32` field, packed -- proto3's default encoding.

    An empty list encodes to nothing at all, which is correct: proto3 has
    no way to distinguish an empty repeated field from an absent one, and
    Retrom's handlers read `ids.is_empty()` as "no filter".
    """
    if not values:
        return b""
    body = b"".join(encode_varint(v) for v in values)
    return bytes_field(field, body)


# -- decoding --------------------------------------------------------------


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ProtoError("truncated varint at end of message")
        if shift > 63:
            raise ProtoError("varint longer than 64 bits")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7


def decode(data: bytes) -> dict[int, list]:
    """`{field_number: [value, ...]}` for one message.

    Varint and fixed-width fields decode to `int`; length-delimited fields
    stay `bytes` and are interpreted by the caller, which is the only
    layer that knows whether they are a string or a nested message.

    Every field is a list because `repeated` is not visible from the wire:
    a field that appears once and one that appears many times are the same
    encoding, and guessing wrong in either direction loses data.
    """
    fields: dict[int, list] = {}
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field = key >> 3
        wire_type = key & 0x07
        if field == 0:
            raise ProtoError("field number 0 is not valid")

        if wire_type == WIRE_VARINT:
            value, offset = _read_varint(data, offset)
        elif wire_type == WIRE_LEN:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ProtoError(
                    f"length-delimited field {field} claims {length} bytes "
                    f"but only {len(data) - offset} remain"
                )
            value = data[offset:end]
            offset = end
        elif wire_type == WIRE_FIXED64:
            if offset + 8 > len(data):
                raise ProtoError(f"truncated 64-bit field {field}")
            value = int.from_bytes(data[offset : offset + 8], "little")
            offset += 8
        elif wire_type == WIRE_FIXED32:
            if offset + 4 > len(data):
                raise ProtoError(f"truncated 32-bit field {field}")
            value = int.from_bytes(data[offset : offset + 4], "little")
            offset += 4
        else:
            # Groups (3, 4) and anything else. There is no way to know how
            # long an unknown wire type is, so the walk cannot continue.
            raise ProtoError(
                f"field {field} has unsupported wire type {wire_type}; "
                f"the rest of the message cannot be skipped safely"
            )

        fields.setdefault(field, []).append(value)
    return fields


# -- reading decoded fields ------------------------------------------------
#
# Each of these answers "what is field N, if it is there at all". They
# return a default rather than raising, because a field Retrom did not
# send is the normal case for every `optional` in its schema -- and
# because a missing field must never be confused with a wrong one.


def as_int(fields: dict[int, list], field: int, default: int | None = None):
    values = fields.get(field)
    if not values or not isinstance(values[-1], int):
        return default
    return values[-1]


def as_bool(fields: dict[int, list], field: int, default: bool = False) -> bool:
    value = as_int(fields, field)
    return default if value is None else bool(value)


def as_str(fields: dict[int, list], field: int, default: str | None = None):
    values = fields.get(field)
    if not values or not isinstance(values[-1], bytes):
        return default
    try:
        return values[-1].decode("utf-8")
    except UnicodeDecodeError:
        return default


def as_strs(fields: dict[int, list], field: int) -> list[str]:
    out = []
    for value in fields.get(field, []):
        if isinstance(value, bytes):
            try:
                out.append(value.decode("utf-8"))
            except UnicodeDecodeError:
                continue
    return out


def as_messages(fields: dict[int, list], field: int) -> list[dict[int, list]]:
    """Every occurrence of `field`, decoded as a nested message.

    An occurrence that is not decodable is dropped rather than raising:
    one malformed row in a listing of a thousand should cost that row, not
    the import.
    """
    out = []
    for value in fields.get(field, []):
        if not isinstance(value, bytes):
            continue
        try:
            out.append(decode(value))
        except ProtoError:
            continue
    return out


def as_message(fields: dict[int, list], field: int) -> dict[int, list] | None:
    messages = as_messages(fields, field)
    return messages[-1] if messages else None


def as_packed_ints(fields: dict[int, list], field: int) -> list[int]:
    """A `repeated` scalar field, however it was encoded.

    proto3 defaults to packed, but an unpacked encoding is still valid and
    conformant decoders must accept both -- so this accepts both.
    """
    out: list[int] = []
    for value in fields.get(field, []):
        if isinstance(value, int):
            out.append(value)
            continue
        if not isinstance(value, bytes):
            continue
        offset = 0
        try:
            while offset < len(value):
                item, offset = _read_varint(value, offset)
                out.append(item)
        except ProtoError:
            continue
    return out
