import pytest
from pydantic import ValidationError

from romm_hub.types import FetchFile, FetchPlan


def test_minimal_plan():
    p = FetchPlan(
        files=[FetchFile(url="https://archive.org/download/x/g.zip", filename="g.zip")],
        platform="dos",
    )
    assert p.files[0].filename == "g.zip"
    assert p.collection is None


def test_plan_requires_at_least_one_file():
    with pytest.raises(ValidationError):
        FetchPlan(files=[], platform="dos")


@pytest.mark.parametrize(
    "evil",
    ["../escape.zip", "a/b.zip", "a\\b.zip", "/abs.zip", "..", "", "."],
)
def test_filename_must_be_a_bare_name(evil):
    """A plugin must not be able to steer the host's writes with a filename."""
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename=evil)


# The names below carry no path separator, so a basename-only check lets
# them through -- and on Windows `job_dir / "C:evil.zip"` then discards the
# job directory entirely and resolves against C:'s current directory. These
# must be refused on every platform: a plugin that behaves differently
# depending on which OS the Hub happens to run on is a plugin that can pick
# its target. None of this needs a hostile plugin -- the shipped Archive.org
# importer takes `filename` straight from third-party item metadata.
@pytest.mark.parametrize(
    "evil",
    [
        # drive-relative: no separator, and the join drops the job dir
        "C:evil.zip",
        "D:evil.zip",
        "Z:evil.zip",
        "c:evil.zip",
        # drive-absolute and UNC
        "C:/evil.zip",
        "C:\\evil.zip",
        "\\\\server\\share\\evil.zip",
        "//server/share/evil.zip",
        # Windows reserved device names, with and without an extension.
        # NUL swallows the bytes, so the ROM hashes as empty and the
        # upload fails with a misleading "cannot upload empty file".
        "NUL",
        "nul",
        "CON",
        "PRN",
        "AUX",
        "COM1",
        "COM9",
        "LPT1",
        "LPT9",
        "NUL.zip",
        "con.txt",
        # dots and spaces only: "..." resolves to the *directory*, so
        # dest.exists() is true and st_size seeds a bogus resume offset
        "...",
        " ",
        " . ",
        # Windows silently strips a trailing dot or space, so these
        # collide with an existing g.zip
        "g.zip.",
        "g.zip ",
        "g.zip\t",
        # NUL byte: Path.resolve() raises ValueError on it
        "a\x00b.zip",
        "\x00",
        # other control characters and reserved punctuation
        "a\nb.zip",
        "a\rb.zip",
        'a"b.zip',
        "a<b.zip",
        "a>b.zip",
        "a|b.zip",
        "a?b.zip",
        "a*b.zip",
        "a:b.zip",
        # unbounded length
        "x" * 300,
    ],
)
def test_filename_rejects_windows_specific_escapes_on_every_platform(evil):
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename=evil)


@pytest.mark.parametrize(
    "ok",
    [
        "g.zip",
        "Super Mario Bros. (USA).zip",
        "Legend of Zelda, The [!].nes",
        "game_v1.1-rev+b.rom",
        "Chip & Dale.zip",
        "Pokemon~1.gb",
        "sonic's revenge!.bin",
        "\u30c9\u30e9\u30af\u30a8.zip",  # a real Archive.org name is not always ASCII
        "disc 1 of 2.cue",
    ],
)
def test_ordinary_rom_filenames_are_still_accepted(ok):
    """The tightening must not refuse the names real ROMs actually have."""
    assert FetchFile(url="https://archive.org/x", filename=ok).filename == ok


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename="g.zip", size_bytes=-1)
