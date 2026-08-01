"""A bencode reader for `.torrent` bytes the host has fetched.

This is the whole of the BitTorrent dependency, and that is the point.
See `rom_hub.torrents` for why the Hub does not link a torrent client:
the short version is that a `.torrent` from a source that also seeds over
HTTPS is a **verified file manifest**, and reading a manifest needs a
parser, not a peer-to-peer stack.

## Why a parser was written rather than installed

`libtorrent` is the only complete implementation and it is a C++ library
with Boost behind it; it wants a compiler or a platform wheel, and it
brings a session, a DHT node and a listening socket into a CLI that runs
one command and exits. The pure-Python clients that exist are partial and
mostly unmaintained. Neither trade is worth making for the ~120 lines
below, which is all this project actually needs.

## It reads hostile bytes, so it is default-deny

The input is a file fetched over the network. It is allowlist-gated, but
an allowlisted host can still be compromised and a plugin chooses which
allowlisted URL is fetched, so nothing here may assume the bytes are a
torrent at all. Every limit below exists because its absence is a way to
make the host misbehave on input somebody else chose:

  * **Depth** -- a nesting bomb (`llllll...`) is a stack overflow in a
    recursive parser, which is a crash the operator cannot read. Bounded
    at `MAX_DEPTH`, which is four times deeper than any real torrent.
  * **Integers** -- `int()` on an arbitrarily long digit run is quadratic
    and CPython refuses over 4300 digits by default anyway. Bounded to a
    length no real value reaches, and leading zeros, `-0` and `+` are
    refused because they are three spellings of a number that already has
    one and a parser that accepts all of them cannot be reasoned about.
  * **String lengths** -- `20:` says twenty bytes follow. A declared
    length longer than the remaining input is refused explicitly rather
    than silently truncated by a slice, because `data[a:b]` past the end
    returns a *short string* instead of raising, and a short `pieces`
    would become wrong hashes rather than an error.
  * **Trailing data** -- bytes after the top-level value mean this is not
    one bencoded document. Refused, so two documents concatenated cannot
    be read as the first one.
  * **Duplicate keys** -- `d3:abc..3:abc..e` has no single meaning.
    Refused rather than resolved by a last-wins rule nobody agreed to.

Unsorted dict keys are **accepted**, which looks like an omission next to
the list above and is not. The spec requires sorting, and a strict reader
would refuse; but the only thing this project derives from a torrent's
structure is its info-hash, and that is computed from the **raw bytes**
of the `info` value (see `Span`), never from a re-encoding of the parsed
form. Because nothing is ever re-encoded, key order cannot change an
answer here -- so refusing on it would reject real files to enforce an
invariant this module does not rely on.

## Why spans, and why there is no encoder

The info-hash is `sha1` of the bencoded `info` dictionary. The obvious
way to get it is to re-encode the parsed dictionary, and that is the way
this module deliberately does **not** offer, because it is only correct
for input that was already canonical: a torrent whose keys are unsorted,
or whose integers are spelled oddly, re-encodes to different bytes and
therefore to a different -- wrong -- info-hash, and the failure is silent.

So `decode` records, for every value it reads, the half-open byte range
it occupied in the input. `sha1(data[span.start:span.stop])` is then the
info-hash by definition rather than by reconstruction, and this module
needs no encoder at all. One direction of code, and the direction that
cannot be subtly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Deeper than any real torrent (which nests three levels at most:
#: `announce-list` is a list of lists of strings) and shallow enough that
#: the recursion below cannot exhaust the interpreter's stack.
MAX_DEPTH = 12

#: Digits in a bencoded integer. A torrent's largest number is a file
#: length; 24 digits is more than the observable universe in bytes.
MAX_INT_DIGITS = 24


class BencodeError(ValueError):
    """The bytes are not a single well-formed bencoded document."""


@dataclass(frozen=True)
class Span:
    """The half-open byte range one decoded value occupied in the input.

    Carried alongside every dictionary so the info-hash can be taken from
    the original bytes. See "Why spans, and why there is no encoder".
    """

    start: int
    stop: int


class BDict(dict):
    """A bencoded dictionary, plus where it was.

    A `dict` subclass rather than a wrapper so that everything reading a
    torrent uses ordinary mapping syntax; `span` is the one addition, and
    only the `info` value's span is ever used.
    """

    span: Span = Span(0, 0)


def decode(data: bytes) -> object:
    """Decode one complete bencoded document. Raise on anything else.

    Returns `bytes`, `int`, `list` or `BDict`. Strings stay `bytes`: a
    torrent's `pieces` is binary and its paths are arbitrary bytes that
    are *usually* UTF-8, so decoding here would either lose data or raise
    on a file the operator can see on the source's own website. Callers
    that want text ask for it explicitly, with an errors policy they
    chose.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise BencodeError(
            f"bencode input must be bytes, got {type(data).__name__}"
        )
    data = bytes(data)
    value, pos = _value(data, 0, 0)
    if pos != len(data):
        raise BencodeError(
            f"trailing data after the bencoded value: {len(data) - pos} "
            f"byte(s) at offset {pos}"
        )
    return value


def _value(data: bytes, pos: int, depth: int) -> tuple[object, int]:
    if depth > MAX_DEPTH:
        raise BencodeError(f"bencode nested deeper than {MAX_DEPTH} levels")
    if pos >= len(data):
        raise BencodeError(f"bencode ended early at offset {pos}")

    lead = data[pos : pos + 1]
    if lead == b"i":
        return _integer(data, pos)
    if lead == b"l":
        return _list(data, pos, depth)
    if lead == b"d":
        return _dict(data, pos, depth)
    if lead.isdigit():
        return _string(data, pos)
    raise BencodeError(
        f"bencode value at offset {pos} starts with {lead!r}, which is not "
        f"'i', 'l', 'd' or a length digit"
    )


def _integer(data: bytes, pos: int) -> tuple[int, int]:
    end = data.find(b"e", pos + 1)
    if end < 0:
        raise BencodeError(f"unterminated bencode integer at offset {pos}")
    body = data[pos + 1 : end]
    if not body:
        raise BencodeError(f"empty bencode integer at offset {pos}")
    if len(body) > MAX_INT_DIGITS + 1:
        raise BencodeError(
            f"bencode integer at offset {pos} has {len(body)} characters, "
            f"over the {MAX_INT_DIGITS}-digit limit"
        )
    digits = body[1:] if body[:1] == b"-" else body
    if not digits.isdigit():
        raise BencodeError(
            f"bencode integer at offset {pos} is not a number: {body!r}"
        )
    # One spelling per value. "i03e", "i-0e" and a leading "+" are all
    # re-spellings of something that already has a form, and a reader that
    # accepts every spelling is one whose output cannot be compared.
    if digits[:1] == b"0" and digits != b"0":
        raise BencodeError(
            f"bencode integer at offset {pos} has a leading zero: {body!r}"
        )
    if body == b"-0":
        raise BencodeError(f"bencode integer at offset {pos} is negative zero")
    return int(body), end + 1


def _string(data: bytes, pos: int) -> tuple[bytes, int]:
    colon = data.find(b":", pos)
    if colon < 0:
        raise BencodeError(f"bencode string at offset {pos} has no ':'")
    raw = data[pos:colon]
    if len(raw) > MAX_INT_DIGITS or not raw.isdigit():
        raise BencodeError(
            f"bencode string at offset {pos} has a bad length prefix: {raw!r}"
        )
    if raw[:1] == b"0" and raw != b"0":
        raise BencodeError(
            f"bencode string length at offset {pos} has a leading zero: {raw!r}"
        )
    length = int(raw)
    start = colon + 1
    stop = start + length
    # Explicit, because slicing past the end returns a short string rather
    # than raising -- and a short `pieces` is wrong hashes, not an error.
    if stop > len(data):
        raise BencodeError(
            f"bencode string at offset {pos} declares {length} bytes but "
            f"only {len(data) - start} remain"
        )
    return data[start:stop], stop


def _list(data: bytes, pos: int, depth: int) -> tuple[list, int]:
    out: list = []
    cursor = pos + 1
    while True:
        if cursor >= len(data):
            raise BencodeError(f"unterminated bencode list at offset {pos}")
        if data[cursor : cursor + 1] == b"e":
            return out, cursor + 1
        item, cursor = _value(data, cursor, depth + 1)
        out.append(item)


def _dict(data: bytes, pos: int, depth: int) -> tuple[BDict, int]:
    out = BDict()
    cursor = pos + 1
    while True:
        if cursor >= len(data):
            raise BencodeError(f"unterminated bencode dictionary at offset {pos}")
        if data[cursor : cursor + 1] == b"e":
            out.span = Span(pos, cursor + 1)
            return out, cursor + 1
        if not data[cursor : cursor + 1].isdigit():
            raise BencodeError(
                f"bencode dictionary key at offset {cursor} is not a string"
            )
        key, cursor = _string(data, cursor)
        if key in out:
            raise BencodeError(
                f"bencode dictionary has a duplicate key {key!r} at offset "
                f"{cursor}; there is no single meaning for that"
            )
        out[key], cursor = _value(data, cursor, depth + 1)
