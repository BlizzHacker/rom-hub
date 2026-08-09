"""`dest_under_dir`: the layer that has to hold when the validator does not.

`types.relative_subdir` is supposed to make every refusal here
unreachable. That is exactly why these tests exist and why they use the
same hostile-input list: the previous filename validator looked complete
too, and `job_dir / "C:evil.zip"` still wrote outside the job directory.
A containment check with no tests of its own is a containment check
nobody will notice has stopped containing.

One of these found a real gap while it was being written. A component of
exactly `"C:"` has `PureWindowsPath("C:").parts == ("C:",)`, so it passes
the parts-equality test that catches `"C:evil.zip"` -- and
`Path(root).joinpath("C:", "evil.zip")` then anchors against drive C:'s
current directory instead of `root`. Splitting a path on "/" is what makes
a bare drive reachable as a component at all, which the flat check never
had to consider.

No test here opens a socket or writes outside `tmp_path`.
"""

from pathlib import Path

import pytest

from rom_hub.paths import (
    UnsafeDestination,
    dest_in_job_dir,
    dest_under_dir,
    flat_destination_only,
)
from rom_hub.types import FetchFile

HOSTILE = [
    "../x.zip",
    "../../etc/passwd",
    "img/../../x",
    "img/..",
    "..",
    "/etc/passwd",
    "img/",
    "/img/x",
    "img//x.png",
    ".",
    "./x",
    "img/./y",
    "C:evil.zip",
    "C:",
    "C:/evil.zip",
    "C:\\evil.zip",
    r"\\server\share\x",
    r"img\x.png",
    "\x00",
    "img/a\x00b",
    "",
]


@pytest.mark.parametrize("evil", HOSTILE)
def test_nothing_escapes_the_target_directory(tmp_path, evil):
    root = tmp_path / "overlays" / "plug"
    root.mkdir(parents=True)
    with pytest.raises(UnsafeDestination):
        dest_under_dir(root, evil)


@pytest.mark.parametrize(
    "good",
    ["a.cfg", "img/a.png", "gamepads/flat/img/dpad.png", "Nintendo - SNES/bezel.png"],
)
def test_a_relative_path_of_bare_names_lands_inside(tmp_path, good):
    root = tmp_path / "overlays" / "plug"
    root.mkdir(parents=True)
    dest = dest_under_dir(root, good)
    # The property, stated the way the function states it: resolved, and
    # strictly below the resolved root.
    assert root.resolve() in dest.resolve().parents
    assert dest.resolve() != root.resolve()


def test_a_symlink_standing_in_the_target_is_resolved_through(tmp_path):
    """`resolve()` follows a link that is already there, so a directory
    replaced by a link out of the tree is caught rather than written
    through. Skipped where the OS will not make one -- Windows needs
    either developer mode or an elevated process."""
    root = tmp_path / "overlays" / "plug"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "img").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this OS will not create a symlink without elevation")
    with pytest.raises(UnsafeDestination):
        dest_under_dir(root, "img/x.png")


def test_the_target_directory_itself_is_not_a_destination(tmp_path):
    root = tmp_path / "overlays" / "plug"
    root.mkdir(parents=True)
    with pytest.raises(UnsafeDestination):
        dest_under_dir(root, "")


# --- the three capabilities that do not offer nesting -------------------


def test_a_flat_consumer_refuses_a_subdir():
    """Refused, not ignored. A field silently dropped by three of four
    consumers is a field somebody will eventually make the fourth obey by
    accident."""
    entry = FetchFile(url="https://x.example/a", filename="a.zip", subdir="img")
    with pytest.raises(UnsafeDestination, match="does not offer a subdirectory"):
        flat_destination_only(entry, what="a ROM import")


def test_a_flat_consumer_passes_a_flat_entry():
    entry = FetchFile(url="https://x.example/a", filename="a.zip")
    assert flat_destination_only(entry, what="a ROM import") is None


def test_the_message_names_the_capability_that_does_offer_it():
    entry = FetchFile(url="https://x.example/a", filename="a.zip", subdir="img")
    with pytest.raises(UnsafeDestination) as exc:
        flat_destination_only(entry, what="a core install")
    assert "a core install" in str(exc.value)
    assert "assets" in str(exc.value)


def test_a_dest_under_dir_result_is_the_unresolved_join(tmp_path):
    """The returned path is the one to open, not the resolved one: an
    operator who pointed the assets directory at a symlinked drive should
    get files at the path they configured."""
    root = tmp_path / "plug"
    root.mkdir()
    assert dest_under_dir(root, "img/a.png") == root / "img" / "a.png"


def test_nesting_and_flatness_agree_on_a_bare_name(tmp_path):
    """A one-component relative path is a bare name, and both functions
    must answer the same thing about it -- otherwise there are two
    containment rules and one of them is the weaker."""
    from rom_hub.paths import dest_in_job_dir

    root = tmp_path / "plug"
    root.mkdir()
    assert dest_under_dir(root, "a.cfg") == dest_in_job_dir(root, "a.cfg")


def test_every_hostile_input_is_refused_by_both_layers(tmp_path):
    """The two layers are checked against one list, not two.

    `relative_subdir` gives a plugin author a legible error;
    `dest_under_dir` is what holds if that validator ever has a gap. They
    are only two layers if they agree, so the agreement is asserted rather
    than assumed."""
    from rom_hub.types import relative_subdir

    root = tmp_path / "plug"
    root.mkdir()
    for evil in HOSTILE:
        with pytest.raises(ValueError):
            relative_subdir(evil)
        with pytest.raises(UnsafeDestination):
            dest_under_dir(root, evil)


def test_the_bound_on_depth_is_enforced_at_the_type(tmp_path):
    """`dest_under_dir` deliberately does not re-check depth: it is a
    containment check, and nine nested directories are contained. The
    bound is a resource limit and lives on the wire type."""
    from rom_hub.types import MAX_SUBDIR_COMPONENTS, relative_subdir

    deep = "/".join(["a"] * (MAX_SUBDIR_COMPONENTS + 1))
    with pytest.raises(ValueError):
        relative_subdir(deep)

    root = tmp_path / "plug"
    root.mkdir()
    assert root.resolve() in dest_under_dir(root, deep + "/x.png").resolve().parents


def test_a_path_is_returned_not_created(tmp_path):
    """Nothing here touches the filesystem: `install_asset` creates the
    directory after the check, on a path already proven contained."""
    root = tmp_path / "plug"
    root.mkdir()
    dest = dest_under_dir(root, "img/a.png")
    assert not dest.exists()
    assert not (root / "img").exists()


def test_a_missing_root_is_still_checked(tmp_path):
    """The containment answer must not depend on whether the destination
    directory has been created yet -- `install_asset` checks before it
    mkdirs, deliberately."""
    root = tmp_path / "not" / "yet"
    assert not root.exists()
    with pytest.raises(UnsafeDestination):
        dest_under_dir(root, "../escape.png")
    assert isinstance(dest_under_dir(root, "img/a.png"), Path)


def test_a_nul_byte_is_refused_by_name_and_not_by_resolve(tmp_path):
    """The NUL refusal must not depend on `resolve()` raising for us.

    Both guards used to catch an embedded NUL only as a side effect: on the
    Pythons of the day `resolve()` raised `ValueError`, the `except` around
    it happened to cover the case, and a comment recorded that as the
    mechanism. Python 3.13 on Windows stopped raising, and the guard
    silently stopped guarding -- it was still *called*, still *passed*, and
    still returned a destination containing a NUL.

    So this asserts the property rather than the symptom: refused on every
    platform, at both layers, without the filesystem being consulted at all.
    A root that does not exist is used deliberately -- if the answer needed
    `resolve()` to object, it could not be given here.
    """
    missing = tmp_path / "never" / "created"
    assert not missing.exists()

    for hostile in ("\x00", "a\x00b", "\x00.png", "img\x00"):
        with pytest.raises(UnsafeDestination, match="NUL"):
            dest_in_job_dir(missing, hostile)
        with pytest.raises(UnsafeDestination, match="NUL"):
            dest_under_dir(missing, hostile)

    # Nested, where the NUL is in a component rather than the whole name.
    for hostile in ("img/a\x00b", "a\x00b/img.png", "img/sub/\x00"):
        with pytest.raises(UnsafeDestination, match="NUL"):
            dest_under_dir(missing, hostile)
