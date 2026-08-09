"""Where the host is allowed to write a name a plugin chose.

Split out of `importer.py` because it is no longer only the import
pipeline that takes a filename from an untrusted process and opens it for
writing: `metadata` writes artwork the plugin named, and `cores` writes
core artifacts the plugin named. Three call sites, one check -- a second
copy of this reasoning would be a second place for it to be subtly
different, which is exactly how a containment check stops containing.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafeDestination(Exception):
    """A plugin-supplied filename would land outside the directory for it."""


def _refuse_nul(name: str, value: str) -> None:
    """Refuse an embedded NUL by looking for it, not by hoping something does.

    Both functions below used to catch this only as a side effect: `resolve()`
    raised `ValueError` on a NUL, so the `except` around it happened to cover
    the case, and a comment said so. That held until Python 3.13 on Windows,
    where `resolve()` stopped raising -- and the guard silently stopped
    guarding. The tests caught it; nothing else would have.

    A containment check must not depend on a library raising incidentally.
    The rule is the same one the drive-letter gap taught: refuse the dangerous
    thing *by name*, rather than inferring it from a side effect that a
    future release is free to change.
    """
    if "\x00" in value:
        raise UnsafeDestination(
            f"refusing to write {value!r}: {name} contains an embedded NUL "
            f"byte, which cannot appear in a legitimate filename and which "
            f"truncates the path in any C API it reaches"
        )


def dest_in_job_dir(job_dir: Path, filename: str) -> Path:
    """Join `filename` onto `job_dir`, refusing anything that lands outside.

    `FetchFile`'s validator is supposed to make this unreachable, and it is
    the layer that gives a plugin author a legible error. This is the layer
    that has to hold when that one has a gap -- a validator bug must not be
    able to become a filesystem write. It is deliberately kept even though
    nothing should reach it: the previous validator looked complete too,
    and `job_dir / "C:evil.zip"` still wrote outside the job directory.

    Both path flavours are consulted so the answer does not depend on the
    host OS: `"C:evil.zip"` is one bare name to POSIX and a drive-relative
    path to Windows, and it must be refused either way.
    """
    job_dir = Path(job_dir)
    _refuse_nul("the filename", filename)
    if PureWindowsPath(filename).parts != (filename,) or PurePosixPath(
        filename
    ).parts != (filename,):
        raise UnsafeDestination(
            f"refusing to write {filename!r}: it is not a bare name, so it "
            f"could land outside the job directory {str(job_dir)!r}"
        )

    dest = job_dir / filename
    try:
        # resolve() also collapses "..", and follows any symlink already
        # standing in the job directory.
        root = job_dir.resolve()
        resolved = dest.resolve()
    except (OSError, ValueError) as exc:
        # Kept as a backstop for whatever else resolve() may object to on a
        # given platform. NUL is handled above rather than here, because
        # relying on this except to catch it is exactly what broke.
        raise UnsafeDestination(
            f"refusing to write {filename!r} outside the job directory "
            f"{str(job_dir)!r} -- the name could not be resolved at all "
            f"({type(exc).__name__}: {exc})"
        ) from exc

    if resolved.parent != root:
        raise UnsafeDestination(
            f"refusing to write {filename!r} outside the job directory "
            f"{str(job_dir)!r} -- it would land at {str(resolved)!r}"
        )
    return dest


def dest_under_dir(root: Path, relative: str) -> Path:
    """Join a relative path onto `root`, refusing anything that escapes it.

    `dest_in_job_dir` for a destination that is allowed to be *nested*.
    Some things a plugin ships are bundles whose internal layout is part
    of the format -- a RetroArch overlay's `.cfg` names its sprites as
    `img/dpad-left.png` -- and flattening those produces files nothing
    loads. So `assets` installs may nest, and this is the check that keeps
    nesting from becoming escaping.

    The requirement is not "a bare name"; it is **the resolved path is
    inside the resolved root**, which is what `dest_in_job_dir` asserts
    too. It just asserts it one level down (`resolved.parent == root`)
    because a flat destination has only one level. Here the containment is
    stated directly, and every component is separately required to be a
    bare name under *both* path flavours first -- so `..`, a drive letter,
    a UNC prefix and a backslash are all refused before any join happens,
    on every platform, rather than depending on which OS the Hub runs on.

    `types.relative_subdir` is supposed to make the component checks here
    unreachable, and this is the layer that has to hold if it ever has a
    gap. Kept for the reason `dest_in_job_dir` is kept: the previous
    filename validator looked complete too.
    """
    root = Path(root)
    if not relative:
        raise UnsafeDestination("refusing to write an empty destination")
    _refuse_nul("the destination", relative)

    parts = relative.split("/")
    for part in parts:
        if not part:
            raise UnsafeDestination(
                f"refusing to write {relative!r}: it has an empty path "
                f"component, so it is not a relative path of bare names"
            )
        windows = PureWindowsPath(part)
        posix = PurePosixPath(part)
        # `parts` alone is not enough here, and the gap is specific: a
        # component of exactly `"C:"` has `parts == ("C:",)`, so it passes
        # a parts-equality test the way `"C:evil.zip"` does not -- and
        # `Path(root).joinpath("C:", "evil.zip")` then anchors against
        # drive C:'s current directory instead of `root`. Splitting on
        # "/" is what makes a bare drive reachable as a component at all,
        # which `dest_in_job_dir` never had to consider. So the drive and
        # the anchor are refused by name rather than inferred.
        if (
            windows.drive
            or windows.root
            or posix.root
            or windows.parts != (part,)
            or posix.parts != (part,)
        ):
            raise UnsafeDestination(
                f"refusing to write {relative!r}: the component {part!r} is "
                f"not a bare name -- no drive, anchor, UNC prefix or path "
                f"separator, under either Windows or POSIX path rules"
            )
        if part in (".", ".."):
            # Unreachable above (both are bare names to both flavours) and
            # the whole point of this function, so it is stated rather than
            # left to resolve() to collapse.
            raise UnsafeDestination(
                f"refusing to write {relative!r}: {part!r} is a traversal, "
                f"not a directory name"
            )

    dest = root.joinpath(*parts)
    try:
        # resolve() collapses what is left and follows any symlink already
        # standing inside the destination directory -- an `img` that is a
        # link to /etc resolves out of the root and is refused below.
        root_resolved = root.resolve()
        resolved = dest.resolve()
    except (OSError, ValueError) as exc:
        # Backstop only; see the note in `dest_in_job_dir`. NUL is refused
        # by name at the top of this function.
        raise UnsafeDestination(
            f"refusing to write {relative!r} outside {str(root)!r} -- the "
            f"path could not be resolved at all ({type(exc).__name__}: {exc})"
        ) from exc

    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise UnsafeDestination(
            f"refusing to write {relative!r} outside {str(root)!r} -- it "
            f"would land at {str(resolved)!r}"
        )
    return dest


def flat_destination_only(entry, *, what: str) -> None:
    """Refuse a plan entry asking to nest where nesting is not offered.

    `FetchFile.subdir` is honoured by exactly one capability. The other
    three consume single files -- a ROM, a core, a BIOS -- and have no
    layout to preserve, so a `subdir` reaching them means the plugin
    believes something about where its bytes go that is not true.

    Refused rather than ignored. A field silently dropped by three of four
    consumers is a field somebody will eventually make the fourth consumer
    obey by accident.
    """
    subdir = getattr(entry, "subdir", None)
    if subdir:
        raise UnsafeDestination(
            f"refusing to write {getattr(entry, 'filename', '?')!r} into "
            f"{subdir!r}: {what} installs single files into one directory "
            f"and does not offer a subdirectory. Only the `assets` "
            f"capability honours FetchFile.subdir."
        )
