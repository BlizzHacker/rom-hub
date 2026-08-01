import pytest
from pydantic import ValidationError

from rom_hub.types import (
    MAX_FILES_PER_PLAN,
    MAX_SUBDIR_CHARS,
    FetchFile,
    FetchPlan,
)


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


def _file(name, url=None):
    return FetchFile(url=url or f"https://archive.org/download/x/{name}", filename=name)


@pytest.mark.parametrize(
    "names",
    [
        ["g.zip", "g.zip"],
        # Windows opens these as one file, so two entries that look
        # distinct would still collide on disk. Refused on both platforms.
        ["g.zip", "G.zip"],
        ["a.zip", "b.zip", "a.zip"],
    ],
)
def test_two_files_in_a_plan_may_not_share_a_filename(names):
    """Two entries writing to one path is not a naming quirk, it is a
    corrupt ROM: the second download sees the first file already there,
    resumes with a Range header, and appends its body to the first one's.
    The result hashes fine, uploads twice, and reports DONE."""
    with pytest.raises(ValidationError):
        FetchPlan(files=[_file(n) for n in names], platform="dos")


def test_distinct_filenames_are_fine():
    plan = FetchPlan(files=[_file("a.zip"), _file("b.zip")], platform="dos")
    assert [f.filename for f in plan.files] == ["a.zip", "b.zip"]


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename="g.zip", size_bytes=-1)


def test_a_plan_may_not_carry_an_unbounded_number_of_files():
    """Default-deny everywhere else in this codebase; the only bound on
    this list was an indirect one -- protocol.MAX_MESSAGE_CHARS caps the
    reply frame at 8 MiB, so roughly 10^5 entries."""
    files = [_file(f"g{i}.zip") for i in range(MAX_FILES_PER_PLAN + 1)]
    with pytest.raises(ValidationError):
        FetchPlan(files=files, platform="dos")


def test_a_plan_at_the_limit_is_still_accepted():
    files = [_file(f"g{i}.zip") for i in range(MAX_FILES_PER_PLAN)]
    assert len(FetchPlan(files=files, platform="dos").files) == MAX_FILES_PER_PLAN


# --- subdir: a second, separately validated way to name a destination ---
#
# `subdir` exists because a RetroArch overlay is a bundle whose internal
# layout is part of the format. It is not a widening of `filename`: the
# tests above still hold verbatim, and the ones below hold the new field
# to the same list of hostile inputs, because a rule that is looser on
# one of two fields is the looser rule.


def test_subdir_defaults_to_absent():
    """Flat is the default and every other capability requires it."""
    f = _file("g.zip")
    assert f.subdir is None
    assert f.relative_path() == "g.zip"


def test_a_valid_subdir_becomes_the_destination():
    f = FetchFile(
        url="https://archive.org/x", filename="dpad-left.png", subdir="gamepads/flat/img"
    )
    assert f.relative_path() == "gamepads/flat/img/dpad-left.png"


@pytest.mark.parametrize(
    "evil",
    [
        # traversal, in every position
        "..",
        "../..",
        "img/../..",
        "img/..",
        # absolute, empty and trailing components
        "/etc",
        "img/",
        "/img",
        "img//deep",
        "",
        # "." is a directory that is not a directory name
        ".",
        "img/.",
        "...",
        # drive-relative and UNC, which carry no separator at all and are
        # the breach `bare_filename` was rewritten for
        "C:evil.zip",
        "C:",
        "C:/evil",
        r"\\server\share",
        # a backslash is a separator under Windows path rules
        r"img\deep",
        # Windows device names, in any component
        "NUL",
        "img/NUL",
        "img/CON",
        "COM1",
        "LPT9",
        # NUL byte
        "img\x00",
        "img/a\x00b",
        # Windows silently strips these, so two names collide on disk
        "img/trailing.",
        "img/trailing ",
        # bounds
        "a/a/a/a/a/a/a/a/a",
        "x" * (MAX_SUBDIR_CHARS + 1),
    ],
)
def test_a_hostile_subdir_is_refused(evil):
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename="g.png", subdir=evil)


def test_two_files_may_share_a_name_in_different_subdirectories():
    """An overlay pack really does this: `img/a.png` beside `alt/a.png`.
    The collision the distinctness rule exists to stop is two entries
    landing on one *path*, which these do not."""
    plan = FetchPlan(
        files=[
            FetchFile(url="https://x.example/1", filename="a.png", subdir="img"),
            FetchFile(url="https://x.example/2", filename="a.png", subdir="alt"),
        ],
        platform="dos",
    )
    assert [f.relative_path() for f in plan.files] == ["img/a.png", "alt/a.png"]


@pytest.mark.parametrize("second", ["img", "IMG"])
def test_two_files_may_not_share_a_destination(second):
    """Same rule as two bare filenames, applied to the whole path -- and
    case-insensitively, because Windows opens `img/a.png` and `IMG/a.png`
    as one file."""
    with pytest.raises(ValidationError):
        FetchPlan(
            files=[
                FetchFile(url="https://x.example/1", filename="a.png", subdir="img"),
                FetchFile(url="https://x.example/2", filename="a.png", subdir=second),
            ],
            platform="dos",
        )
