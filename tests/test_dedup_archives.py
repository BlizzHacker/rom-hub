"""Archive-aware hashing, so dedup can match an archive already in RomM.

RomM does **not** store the hash of an archive's own bytes. For anything
in its `ARCHIVE_READERS` map it decompresses the archive and accumulates
crc32/md5/sha1 over the concatenated member payloads, in ASCII filename
order, skipping `DEFAULT_EXCLUDED_FILES` and `DEFAULT_EXCLUDED_EXTENSIONS`.

Measured live against RomM 4.9.2, for the same `rubik.zip`:

    RomM's stored sha1 = 5dbbca36a7106210e79c993631b494d53e1214b0
    the raw file sha1  = 4f7396a71145a83f477e2dae84cf0235b7fee444

So a raw hash can never match an archive that is already in the library:
the ROM gets re-uploaded on every run, and the post-upload confirmation
can never find what it just uploaded.

The fixtures here are built in the test, with more than one member, and
the expected digest is computed independently by `_digest_of`. A test
that passed for a single-member zip would prove almost nothing -- it
would not catch a wrong member order, and it would not catch exclusions
being ignored.
"""

import hashlib
import io
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest

from romm_hub.dedup import (
    ARCHIVE_EXTENSIONS,
    ROMM_EXCLUDED_EXTENSIONS,
    ROMM_EXCLUDED_FILES,
    FileHashes,
    archive_extension,
    find_by_filename,
    hash_file,
)

MEMBER_A = b"AAAA-first-member" * 40
MEMBER_B = b"BBBB-second-member" * 40
MEMBER_C = b"CCCC-third-member" * 40


def _digest_of(payload: bytes) -> FileHashes:
    """The oracle: the three digests of a byte string, computed without
    using anything from romm_hub.dedup."""
    return FileHashes(
        crc32=format(zlib.crc32(payload) & 0xFFFFFFFF, "08x"),
        md5=hashlib.md5(payload).hexdigest(),
        sha1=hashlib.sha1(payload).hexdigest(),
    )


def _zip(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)
    return path


# -- the algorithm --------------------------------------------------------


def test_an_archive_is_hashed_as_its_members_not_as_its_own_bytes(tmp_path):
    """The blocker itself."""
    path = _zip(tmp_path / "game.zip", [("a.bin", MEMBER_A)])

    assert hash_file(path) == _digest_of(MEMBER_A)
    assert hash_file(path).sha1 != hashlib.sha1(path.read_bytes()).hexdigest()


def test_members_are_concatenated_in_filename_order_not_stored_order(tmp_path):
    """Ordering is load-bearing: concatenation is not commutative, so the
    wrong order yields a digest that silently never matches.

    This zip stores its members in reverse, so an implementation that
    walked the archive as-stored would produce the b-then-a digest.
    """
    path = _zip(tmp_path / "game.zip", [("b.bin", MEMBER_B), ("a.bin", MEMBER_A)])

    assert hash_file(path) == _digest_of(MEMBER_A + MEMBER_B)
    assert hash_file(path) != _digest_of(MEMBER_B + MEMBER_A)


def test_the_stored_order_does_not_change_the_hash(tmp_path):
    """Two archives holding the same members are the same ROM to RomM,
    whichever order they happen to be written in."""
    forward = _zip(tmp_path / "f.zip", [("a.bin", MEMBER_A), ("b.bin", MEMBER_B)])
    reverse = _zip(tmp_path / "r.zip", [("b.bin", MEMBER_B), ("a.bin", MEMBER_A)])

    assert hash_file(forward) == hash_file(reverse)


def test_three_members_accumulate_across_the_whole_archive(tmp_path):
    path = _zip(
        tmp_path / "game.zip",
        [("c.bin", MEMBER_C), ("a.bin", MEMBER_A), ("b.bin", MEMBER_B)],
    )
    assert hash_file(path) == _digest_of(MEMBER_A + MEMBER_B + MEMBER_C)


def test_directory_entries_are_skipped(tmp_path):
    path = tmp_path / "g.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("sub/", b"")
        z.writestr("sub/a.bin", MEMBER_A)
    assert hash_file(path) == _digest_of(MEMBER_A)


def test_a_tar_is_hashed_by_members_too(tmp_path):
    path = tmp_path / "g.tar"
    with tarfile.open(path, "w") as tf:
        for name, data in (("b.bin", MEMBER_B), ("a.bin", MEMBER_A)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    assert hash_file(path) == _digest_of(MEMBER_A + MEMBER_B)


# -- exclusions -----------------------------------------------------------


@pytest.mark.parametrize("excluded", ROMM_EXCLUDED_FILES)
def test_an_excluded_filename_does_not_contribute_to_the_hash(tmp_path, excluded):
    """Exclusions are not cosmetic. A `.DS_Store` a Mac dropped into the
    zip would otherwise change the digest and break the match against the
    very same ROM already sitting in RomM."""
    clean = _zip(tmp_path / "clean.zip", [("a.bin", MEMBER_A)])
    dirty = _zip(
        tmp_path / "dirty.zip",
        [("a.bin", MEMBER_A), (excluded, b"junk that must not be hashed")],
    )

    assert hash_file(dirty) == hash_file(clean) == _digest_of(MEMBER_A)


@pytest.mark.parametrize("ext", ROMM_EXCLUDED_EXTENSIONS)
def test_an_excluded_extension_does_not_contribute_to_the_hash(tmp_path, ext):
    clean = _zip(tmp_path / "clean.zip", [("a.bin", MEMBER_A)])
    dirty = _zip(
        tmp_path / "dirty.zip", [("a.bin", MEMBER_A), (f"sidecar.{ext}", b"junk")]
    )

    assert hash_file(dirty) == hash_file(clean)


def test_exclusion_matches_on_the_basename_inside_a_subdirectory(tmp_path):
    """RomM applies the exclusions to `Path(name).name`, so a nested
    `.DS_Store` is excluded exactly as a top-level one is."""
    clean = _zip(tmp_path / "clean.zip", [("sub/a.bin", MEMBER_A)])
    dirty = _zip(
        tmp_path / "dirty.zip", [("sub/a.bin", MEMBER_A), ("sub/.DS_Store", b"junk")]
    )

    assert hash_file(dirty) == hash_file(clean) == _digest_of(MEMBER_A)


def test_an_excluded_extension_is_matched_case_insensitively(tmp_path):
    clean = _zip(tmp_path / "clean.zip", [("a.bin", MEMBER_A)])
    dirty = _zip(tmp_path / "dirty.zip", [("a.bin", MEMBER_A), ("NOTES.INI", b"x")])

    assert hash_file(dirty) == hash_file(clean)


def test_a_suffix_that_merely_ends_with_an_excluded_one_is_still_hashed(tmp_path):
    """`.mydb` is not `.db`. RomM matches a full dotted suffix, so an
    over-eager `endswith("db")` would silently drop a real member and
    produce a hash that matches nothing."""
    path = _zip(tmp_path / "g.zip", [("a.bin", MEMBER_A), ("b.mydb", MEMBER_B)])
    assert hash_file(path) == _digest_of(MEMBER_A + MEMBER_B)


# -- the raw-file fallbacks RomM also has ---------------------------------


def test_an_empty_archive_falls_back_to_the_raw_file_hash(tmp_path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w"):
        pass
    assert hash_file(path) == _digest_of(path.read_bytes())


def test_an_all_excluded_archive_falls_back_to_the_raw_file_hash(tmp_path):
    path = _zip(tmp_path / "junk.zip", [(".DS_Store", b"junk"), ("x.tmp", b"junk")])
    assert hash_file(path) == _digest_of(path.read_bytes())


def test_a_corrupt_archive_falls_back_to_the_raw_file_hash(tmp_path):
    """Named `.zip`, is not a zip. Must not raise: a plugin can hand us
    anything, and a crash fails an import that could have proceeded."""
    path = tmp_path / "broken.zip"
    path.write_bytes(b"this is definitely not a zip file")
    assert hash_file(path) == _digest_of(path.read_bytes())


def test_an_unreadable_archive_format_falls_back_rather_than_crashing(tmp_path):
    """`.rar` and `.7z` are archives to RomM but need tooling the Hub does
    not ship. Falling back to a raw hash costs a missed dedup (a
    re-upload); guessing would risk a false one (a wrongly skipped ROM)."""
    path = tmp_path / "g.rar"
    path.write_bytes(b"Rar!\x1a\x07\x00 not really a rar")
    assert hash_file(path) == _digest_of(path.read_bytes())


def test_a_non_archive_is_still_hashed_raw(tmp_path):
    path = tmp_path / "game.rom"
    path.write_bytes(b"hello world")
    assert hash_file(path).sha1 == "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"


# -- extension classification ---------------------------------------------


def test_the_archive_extension_set_mirrors_romms_archive_readers():
    """If RomM gains a reader, this set must grow with it -- otherwise that
    format silently reverts to raw hashing and stops deduping."""
    assert set(ARCHIVE_EXTENSIONS) == {
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
    }


@pytest.mark.parametrize(
    "name,expected",
    [
        ("g.zip", ".zip"),
        ("g.ZIP", ".zip"),
        ("g.tar", ".tar"),
        # The compound suffix must win over the bare one, or a .tar.gz is
        # opened as an uncompressed tar.
        ("g.tar.gz", ".tar.gz"),
        ("g.tar.bz2", ".tar.bz2"),
        ("g.rom", None),
        ("g", None),
        ("notzip", None),
    ],
)
def test_archive_extension_classification(name, expected):
    assert archive_extension(name) == expected


# -- the cheap pre-download check -----------------------------------------


def test_find_by_filename_matches_a_rom_on_its_fs_name():
    roms = [{"id": 1, "fs_name": "other.zip"}, {"id": 2, "fs_name": "rubik.zip"}]
    assert find_by_filename("rubik.zip", roms)["id"] == 2


def test_find_by_filename_returns_none_when_nothing_matches():
    assert find_by_filename("rubik.zip", [{"id": 1, "fs_name": "other.zip"}]) is None


def test_find_by_filename_skips_malformed_entries():
    roms = ["not a dict", {"fs_name": None}, {}, {"id": 9, "fs_name": "rubik.zip"}]
    assert find_by_filename("rubik.zip", roms)["id"] == 9


def test_find_by_filename_on_an_empty_library_returns_none():
    assert find_by_filename("rubik.zip", []) is None
