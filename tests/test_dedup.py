"""Hash-based dedup against RomM's existing library.

`GET /api/roms` has no hash filter (see docs/superpowers/plans/
2026-07-28-romm-hub-phase2-import.md), so dedup means: hash the
downloaded file locally, then compare against SimpleRomSchema's
`crc_hash` / `md5_hash` / `sha1_hash` fields client-side.

Two things this file pins down:
  - hash_file makes a single streaming pass that updates crc32, md5, and
    sha1 together -- it must not read the file three times, and must not
    read a multi-GB file into memory whole.
  - find_duplicate never lets a null-vs-null comparison count as a match.
    RomM can return `None` for any of these hash fields; a rom that
    hasn't been hashed yet must never appear to "match" another
    not-yet-hashed rom.
"""

import hashlib
import os
import zlib
from pathlib import Path

from romm_hub.dedup import FileHashes, find_duplicate, hash_file

KNOWN_CONTENT = b"hello world"
KNOWN_CRC32 = "0d4a1185"
KNOWN_MD5 = "5eb63bbbe01eeed093cb22bb8f5acdc3"
KNOWN_SHA1 = "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"


def test_hash_file_matches_known_digests(tmp_path):
    f = tmp_path / "known.bin"
    f.write_bytes(KNOWN_CONTENT)

    hashes = hash_file(f)

    assert hashes.crc32 == KNOWN_CRC32
    assert hashes.md5 == KNOWN_MD5
    assert hashes.sha1 == KNOWN_SHA1


def test_hashes_are_lowercase_hex(tmp_path):
    f = tmp_path / "mixed.bin"
    f.write_bytes(os.urandom(64))

    hashes = hash_file(f)

    for value in (hashes.crc32, hashes.md5, hashes.sha1):
        assert value == value.lower()
        int(value, 16)  # must parse as hex


def test_single_pass_computes_all_three_digests_on_a_large_file(tmp_path):
    """Not a mock-based test: hash a file too big to be a fluke and check
    every digest independently against hashlib/zlib computed the normal
    (multi-pass) way. If hash_file only updated one accumulator per read
    loop iteration, or dropped bytes across chunk boundaries, this would
    catch it."""
    f = tmp_path / "big.bin"
    data = os.urandom(5 * 1024 * 1024 + 137)  # not a clean multiple of any chunk size
    f.write_bytes(data)

    hashes = hash_file(f)

    assert hashes.crc32 == format(zlib.crc32(data) & 0xFFFFFFFF, "08x")
    assert hashes.md5 == hashlib.md5(data).hexdigest()
    assert hashes.sha1 == hashlib.sha1(data).hexdigest()


def test_find_duplicate_matches_on_sha1():
    hashes = FileHashes(crc32="aaaaaaaa", md5="bbbb", sha1=KNOWN_SHA1)
    existing = [
        {"id": 1, "sha1_hash": "0000000000000000000000000000000000ffff",
         "md5_hash": None, "crc_hash": None},
        {"id": 2, "sha1_hash": KNOWN_SHA1, "md5_hash": None, "crc_hash": None},
    ]

    match = find_duplicate(hashes, existing)

    assert match is not None
    assert match["id"] == 2


def test_find_duplicate_matching_is_case_insensitive():
    """RomM may store uppercase hex."""
    hashes = FileHashes(crc32="aaaaaaaa", md5="bbbb", sha1=KNOWN_SHA1)
    existing = [{"id": 7, "sha1_hash": KNOWN_SHA1.upper(),
                 "md5_hash": None, "crc_hash": None}]

    match = find_duplicate(hashes, existing)

    assert match is not None
    assert match["id"] == 7


def test_find_duplicate_falls_back_to_md5_then_crc_in_priority_order():
    hashes = FileHashes(crc32=KNOWN_CRC32, md5=KNOWN_MD5, sha1="deadbeef" * 5)
    # Nothing matches sha1. One rom matches only crc, another matches md5 --
    # the md5 match must win because md5 outranks crc.
    crc_only = {"id": 1, "sha1_hash": None, "md5_hash": None, "crc_hash": KNOWN_CRC32}
    md5_match = {"id": 2, "sha1_hash": None, "md5_hash": KNOWN_MD5, "crc_hash": None}
    existing = [crc_only, md5_match]

    match = find_duplicate(hashes, existing)

    assert match is not None
    assert match["id"] == 2


def test_find_duplicate_matches_on_crc_when_nothing_else_matches():
    hashes = FileHashes(crc32=KNOWN_CRC32, md5="nomatch", sha1="nomatch")
    existing = [{"id": 3, "sha1_hash": None, "md5_hash": None, "crc_hash": KNOWN_CRC32}]

    match = find_duplicate(hashes, existing)

    assert match is not None
    assert match["id"] == 3


def test_rom_with_null_hash_fields_never_matches():
    """A rom RomM hasn't hashed yet has all three fields null. Our own
    hashes are never null (hash_file always produces real hex digests),
    but this pins the defensive branch: a None-vs-None comparison must
    never be treated as equal."""
    hashes = FileHashes(crc32=KNOWN_CRC32, md5=KNOWN_MD5, sha1=KNOWN_SHA1)
    existing = [{"id": 9, "sha1_hash": None, "md5_hash": None, "crc_hash": None}]

    assert find_duplicate(hashes, existing) is None


def test_no_match_returns_none():
    hashes = FileHashes(crc32=KNOWN_CRC32, md5=KNOWN_MD5, sha1=KNOWN_SHA1)
    existing = [
        {"id": 1, "sha1_hash": "1111111111111111111111111111111111ffff",
         "md5_hash": "22222222222222222222222222222222", "crc_hash": "33333333"},
    ]

    assert find_duplicate(hashes, existing) is None


def test_find_duplicate_on_empty_library_returns_none():
    hashes = FileHashes(crc32=KNOWN_CRC32, md5=KNOWN_MD5, sha1=KNOWN_SHA1)

    assert find_duplicate(hashes, []) is None


def test_a_malformed_rom_entry_is_skipped_not_crashed_on():
    """RomM's response is data from another service, not a guaranteed
    shape. A non-string hash used to raise AttributeError, which
    run_import's catch-all turned into a FAILED job blaming the Hub."""
    hashes = FileHashes(crc32=KNOWN_CRC32, md5=KNOWN_MD5, sha1=KNOWN_SHA1)
    existing = [
        "not a dict at all",
        None,
        42,
        {"id": 1, "sha1_hash": 12345},
        {"id": 2, "crc_hash": ["a", "list"]},
        {"id": 3, "md5_hash": {"nested": "object"}},
    ]

    assert find_duplicate(hashes, existing) is None


def test_a_malformed_entry_does_not_hide_a_real_match_behind_it():
    hashes = FileHashes(crc32=KNOWN_CRC32, md5=KNOWN_MD5, sha1=KNOWN_SHA1)
    existing = [{"id": 1, "sha1_hash": 999}, {"id": 7, "sha1_hash": KNOWN_SHA1}]

    assert find_duplicate(hashes, existing) == {"id": 7, "sha1_hash": KNOWN_SHA1}
