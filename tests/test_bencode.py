"""The bencode reader, which is the whole of the BitTorrent dependency.

Every test here is offline. `rubik_202308.torrent` is a real Archive.org
torrent captured on 2026-08-01 and checked into `tests/fixtures/`; the
rest of the input is written inline, because the interesting cases are
the malformed ones and nobody publishes those.
"""

import hashlib
from pathlib import Path

import pytest

from rom_hub.bencode import MAX_DEPTH, BDict, BencodeError, decode

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "archive_org_torrent"

#: The info-hash Archive.org itself publishes for this item, as `btih` in
#: its /metadata/ reply. The point of asserting against it rather than
#: against whatever this module computes is that it is an *independent*
#: answer: if the span arithmetic below is wrong, this fails.
RUBIK_BTIH = "6e56c747303e7bf35bf86b1956fb7ea06c99b805"


def test_the_shapes_round_trip():
    assert decode(b"i42e") == 42
    assert decode(b"i-42e") == -42
    assert decode(b"i0e") == 0
    assert decode(b"4:spam") == b"spam"
    assert decode(b"0:") == b""
    assert decode(b"le") == []
    assert decode(b"l4:spami3ee") == [b"spam", 3]
    assert decode(b"de") == {}
    assert decode(b"d3:cow3:moo4:spam4:eggse") == {b"cow": b"moo", b"spam": b"eggs"}


def test_strings_stay_bytes():
    """A torrent's `pieces` is binary and its paths need not be UTF-8.

    Decoding here would either lose data or raise on a file the operator
    can see on the source's own website, so callers ask for text
    explicitly with an errors policy they chose.
    """
    value = decode(b"d4:name6:caf\xc3\xa9!e")[b"name"]
    assert isinstance(value, bytes)
    assert value == b"caf\xc3\xa9!"


def test_a_dictionary_records_where_it_was():
    document = decode(b"d4:infod1:ai1eee")
    info = document[b"info"]
    assert isinstance(info, BDict)
    # The span is the `info` VALUE, not the key and not the whole document.
    assert b"d4:infod1:ai1eee"[info.span.start : info.span.stop] == b"d1:ai1ee"


@pytest.mark.parametrize(
    "data, fragment",
    [
        (b"i03e", "leading zero"),
        (b"i-0e", "negative zero"),
        (b"ie", "empty"),
        (b"i12", "unterminated"),
        (b"i1x3e", "not a number"),
        (b"i" + b"9" * 40 + b"e", "over the"),
        (b"02:ab", "leading zero"),
        (b"2", "no ':'"),
        (b"5:ab", "declares 5 bytes"),
        (b"l", "unterminated bencode list"),
        (b"d", "unterminated bencode dictionary"),
        (b"di1e1:ae", "key at offset 1 is not a string"),
        (b"x", "not 'i', 'l', 'd' or a length digit"),
        (b"", "ended early"),
        (b"i1ei2e", "trailing data"),
        (b"1:ax", "trailing data"),
        (b"d1:ai1e1:ai2ee", "duplicate key"),
    ],
)
def test_malformed_input_is_refused_with_a_reason(data, fragment):
    with pytest.raises(BencodeError) as excinfo:
        decode(data)
    assert fragment in str(excinfo.value)


def test_a_short_string_is_refused_rather_than_silently_truncated():
    """`data[a:b]` past the end returns a short string instead of raising.

    That is the failure mode worth a test of its own: a truncated
    `pieces` would become *wrong hashes* rather than an error, and wrong
    hashes fail later, somewhere that cannot explain them.
    """
    with pytest.raises(BencodeError):
        decode(b"d6:pieces40:" + b"\x00" * 20)


def test_a_nesting_bomb_is_refused_before_the_stack_is():
    bomb = b"l" * (MAX_DEPTH + 5) + b"e" * (MAX_DEPTH + 5)
    with pytest.raises(BencodeError, match="nested deeper"):
        decode(bomb)
    # And the legal depth still parses, so the bound is not merely small.
    ok = b"l" * MAX_DEPTH + b"e" * MAX_DEPTH
    assert decode(ok) == eval("[" * MAX_DEPTH + "]" * MAX_DEPTH)  # noqa: S307


def test_unsorted_keys_are_accepted_on_purpose():
    """The spec requires sorting; this reader does not, and that is a decision.

    Nothing here is ever re-encoded -- the info-hash comes from the raw
    byte span -- so key order cannot change an answer. Refusing on it
    would reject real files to enforce an invariant this module does not
    rely on.
    """
    assert decode(b"d1:zi1e1:ai2ee") == {b"z": 1, b"a": 2}


def test_input_must_be_bytes():
    with pytest.raises(BencodeError, match="must be bytes"):
        decode("d1:ai1ee")  # type: ignore[arg-type]


# ------------------------------------------------- against the real thing


def test_a_live_archive_org_torrent_hashes_to_the_btih_it_publishes():
    """The whole reason spans exist, checked against an outside answer.

    Archive.org publishes the info-hash for its own torrents in
    /metadata/. Taking `sha1` of `data[span]` must reproduce it exactly --
    and it must do so *without* re-encoding, which is the thing that would
    be silently wrong for any torrent that was not already canonical.
    """
    data = (FIXTURES / "rubik_202308.torrent").read_bytes()
    info = decode(data)[b"info"]
    computed = hashlib.sha1(data[info.span.start : info.span.stop]).hexdigest()
    assert computed == RUBIK_BTIH


def test_the_real_torrent_reads_as_the_document_it_is():
    data = (FIXTURES / "rubik_202308.torrent").read_bytes()
    document = decode(data)
    assert document[b"announce"] == b"http://bt1.archive.org:6969/announce"
    assert b"https://archive.org/download/" in document[b"url-list"]
    info = document[b"info"]
    assert info[b"name"] == b"rubik_202308"
    assert info[b"piece length"] == 524288
    assert len(info[b"pieces"]) % 20 == 0
    names = [entry[b"path"][0] for entry in info[b"files"]]
    assert b"rubik.zip" in names
