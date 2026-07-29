"""Hash-based dedup against RomM's existing library.

`GET /api/roms` has no hash filter (its params are `search_term`,
`platform_ids`, `collection_id`, ...), so dedup is client-side: hash the
downloaded file locally, then compare against `SimpleRomSchema`'s
`crc_hash` / `md5_hash` / `sha1_hash` fields.

`hash_file` makes one streaming pass over the file, updating all three
digests together -- a ROM can be multiple GB, so neither reading the
whole file into memory nor walking it three times is acceptable.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass
from pathlib import Path

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class FileHashes:
    crc32: str
    md5: str
    sha1: str


def hash_file(path: Path, chunk_size: int = _READ_CHUNK_SIZE) -> FileHashes:
    """Stream `path` once, computing crc32/md5/sha1 together.

    Returns lowercase hex for all three -- RomM's hash fields are
    compared case-insensitively, but this side always normalizes so
    callers never have to think about case.
    """
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            md5.update(chunk)
            sha1.update(chunk)
    return FileHashes(
        crc32=format(crc & 0xFFFFFFFF, "08x"),
        md5=md5.hexdigest(),
        sha1=sha1.hexdigest(),
    )


def find_duplicate(hashes: FileHashes, existing_roms: list[dict]) -> dict | None:
    """Return the first rom in `existing_roms` whose hash matches `hashes`,
    or `None` if none does.

    Matching is tried sha1 -> md5 -> crc, in that priority order: every
    rom is checked against the highest-priority hash field first, and
    only if nothing matches there do lower-priority fields get a look.
    Comparison is case-insensitive (RomM may store uppercase hex).

    A rom's hash field of `None` never matches, even if `hashes` somehow
    carried a `None` too -- a null-vs-null "match" would silently dedup
    two unrelated, not-yet-hashed ROMs into one.
    """
    for field, value in (
        ("sha1_hash", hashes.sha1),
        ("md5_hash", hashes.md5),
        ("crc_hash", hashes.crc32),
    ):
        if not value:
            continue
        for rom in existing_roms:
            rom_value = rom.get(field)
            if not rom_value:
                continue
            if rom_value.lower() == value.lower():
                return rom
    return None
