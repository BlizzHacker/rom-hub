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
        # ValueError is what an embedded NUL byte raises here.
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
