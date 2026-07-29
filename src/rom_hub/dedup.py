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

import fnmatch
import hashlib
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# --- mirrored RomM constants ------------------------------------------------
#
# These three are forks of upstream values. They are duplicated here because
# RomM exposes no endpoint that reports them, and dedup cannot work without
# reproducing the server's digest exactly. Each is named with its upstream
# source so the coupling is visible when RomM is upgraded -- if any of these
# drift, archives stop deduping silently, which is the failure mode this
# whole module exists to prevent.
#
# Verified by reading RomM 4.9.2 in the running container, not from memory.

# Upstream: backend/config/config_manager.py :: DEFAULT_EXCLUDED_EXTENSIONS
# Compared against a member's lowercased basename as `.<ext>`.
ROMM_EXCLUDED_EXTENSIONS = (
    "db",
    "ini",
    "tmp",
    "bak",
    "lock",
    "log",
    "cache",
    "crdownload",
)

# Upstream: backend/config/config_manager.py :: DEFAULT_EXCLUDED_FILES
# Compared against a member's basename by equality *and* by fnmatch, so a
# glob in this list works the way upstream's does.
ROMM_EXCLUDED_FILES = (
    ".DS_Store",
    ".localized",
    ".Trashes",
    ".stfolder",
    "@SynoResource",
    "gamelist.xml",
    "metadata.pegasus.txt",
)

# Upstream: backend/handler/filesystem/roms_handler.py :: ARCHIVE_READERS
# Every extension RomM hashes member-wise rather than raw. Listed in full,
# including the formats this module cannot open, so `archive_extension` can
# still classify them correctly.
ARCHIVE_EXTENSIONS = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".7z",
    ".rar",
)

# What this module can open with the standard library alone. `.7z` and
# `.rar` are deliberately absent: reading them needs py7zr/rarfile (and, for
# RomM, an external 7zz binary). They fall through to the raw-file hash,
# which costs a missed dedup -- the file is uploaded again -- and never a
# false one, which would wrongly skip a ROM the user does not have.
_STDLIB_READABLE = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                    ".tar.xz", ".txz")


@dataclass(frozen=True)
class FileHashes:
    crc32: str
    md5: str
    sha1: str


def archive_extension(name: str | Path) -> str | None:
    """The `ARCHIVE_EXTENSIONS` entry `name` ends with, or `None`.

    Longest match wins, so `.tar.gz` is not mistaken for `.tar` -- opening
    a gzipped tar as an uncompressed one yields a different member list and
    therefore a different, useless digest.
    """
    lowered = str(name).lower()
    matches = [ext for ext in ARCHIVE_EXTENSIONS if lowered.endswith(ext)]
    if not matches:
        return None
    return max(matches, key=len)


def _is_excluded_member(name: str) -> bool:
    """Whether RomM would skip this archive member when hashing.

    Mirrors the filter in backend/utils/archives.py
    (`read_zip_archive_files` / `read_tar_archive_files`): both tests are
    applied to the *basename*, the extension test case-insensitively.
    """
    base_name = PurePosixPath(name).name
    lower = base_name.lower()
    if any(lower.endswith("." + ext) for ext in ROMM_EXCLUDED_EXTENSIONS):
        return True
    return any(
        base_name == exc or fnmatch.fnmatch(base_name, exc)
        for exc in ROMM_EXCLUDED_FILES
    )


def _zip_members(path: Path, chunk_size: int) -> Iterator[bytes]:
    """Decompressed bytes of every hashable member, in ASCII filename order.

    The sort is upstream's (`sorted(z.infolist(), key=lambda e: e.filename)`)
    and it is load-bearing: the digests accumulate across members, so a
    different order is a different hash.
    """
    with zipfile.ZipFile(path, "r") as z:
        for entry in sorted(z.infolist(), key=lambda e: e.filename):
            if entry.is_dir() or _is_excluded_member(entry.filename):
                continue
            with z.open(entry, "r") as fh:
                while chunk := fh.read(chunk_size):
                    yield chunk


def _tar_members(path: Path, chunk_size: int) -> Iterator[bytes]:
    """As `_zip_members`, for the tar family. Upstream opens with `r:*`,
    which sniffs the compression, so one reader covers every tar variant."""
    with tarfile.open(path, "r:*") as tf:
        members = sorted(
            (m for m in tf.getmembers() if m.isfile()), key=lambda m: m.name
        )
        for member in members:
            if _is_excluded_member(member.name):
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            with extracted as fh:
                while chunk := fh.read(chunk_size):
                    yield chunk


def _raw_chunks(path: Path, chunk_size: int) -> Iterator[bytes]:
    with Path(path).open("rb") as fh:
        while chunk := fh.read(chunk_size):
            yield chunk


def _digest(chunks: Iterator[bytes]) -> tuple[FileHashes, bool]:
    """Fold `chunks` into all three digests in one pass.

    Returns the hashes and whether any bytes were seen at all, which is how
    the caller distinguishes "archive with no hashable members" (fall back
    to the raw file, as RomM does) from a genuinely empty file.
    """
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    saw_any = False
    for chunk in chunks:
        saw_any = True
        crc = zlib.crc32(chunk, crc)
        md5.update(chunk)
        sha1.update(chunk)
    return (
        FileHashes(
            crc32=format(crc & 0xFFFFFFFF, "08x"),
            md5=md5.hexdigest(),
            sha1=sha1.hexdigest(),
        ),
        saw_any,
    )


def hash_file(path: Path, chunk_size: int = _READ_CHUNK_SIZE) -> FileHashes:
    """The digests RomM would store for `path`, as lowercase hex.

    For an archive that is **not** the hash of the file's own bytes. RomM
    hashes the concatenated decompressed members, in filename order, minus
    its excluded names and extensions -- so hashing a zip raw produces a
    value that can never match the library. Measured on the same file:

        RomM's stored sha1 = 5dbbca36a7106210e79c993631b494d53e1214b0
        the raw file sha1  = 4f7396a71145a83f477e2dae84cf0235b7fee444

    Everything else is hashed raw, which is also RomM's behaviour, and is
    the fallback whenever an archive cannot be read or yields no hashable
    members -- upstream does exactly the same rather than store nothing.

    Still a single streaming pass that updates crc32/md5/sha1 together: a
    ROM can be multiple GB, so neither reading it whole nor walking it
    three times is acceptable, and that holds for the decompressed stream
    as much as for the raw one.
    """
    path = Path(path)
    ext = archive_extension(path.name)

    if ext in _STDLIB_READABLE:
        reader = _tar_members if ext != ".zip" else _zip_members
        try:
            hashes, saw_any = _digest(reader(path, chunk_size))
            if saw_any:
                return hashes
        except (
            zipfile.BadZipFile,
            tarfile.TarError,
            EOFError,
            RuntimeError,
            zlib.error,
            OSError,
        ):
            # Upstream swallows the same class of failure and hashes the raw
            # file instead. A corrupt or unreadable archive must not fail an
            # import that could otherwise have proceeded.
            pass

    return _digest(_raw_chunks(path, chunk_size))[0]


def find_by_filename(filename: str, existing_roms: list[dict]) -> dict | None:
    """The first rom in `existing_roms` whose `fs_name` is `filename`.

    This is the cheap pre-download check. Within one platform a filename is
    unique -- RomM assembles every upload to
    `roms/<platform_fs_slug>/<filename>`, so a second file of that name
    would overwrite the first -- which makes a name match on the same
    platform a reliable "already imported" signal, available before a
    single byte is fetched.

    Deliberately case-sensitive. RomM runs on Linux, where `Game.zip` and
    `game.zip` are two different ROMs; a case-insensitive match would skip
    an import the user actually wanted.
    """
    for rom in existing_roms:
        if not isinstance(rom, dict):
            continue
        value = rom.get("fs_name")
        if isinstance(value, str) and value == filename:
            return rom
    return None


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
            # This list came back from RomM over HTTP, so its shape is not
            # this codebase's to guarantee. A non-dict entry or a non-string
            # hash used to raise AttributeError, which run_import's catch-all
            # turned into a FAILED job whose message pointed at the Hub
            # rather than at the response that was actually malformed.
            if not isinstance(rom, dict):
                continue
            rom_value = rom.get(field)
            if not isinstance(rom_value, str) or not rom_value:
                continue
            if rom_value.lower() == value.lower():
                return rom
    return None
