"""Checking a repository path, rather than repairing one.

This module used to sanitise: it took an upstream name, replaced whatever
the host would refuse, and handed back something that would install. That
was right while an overlay was a single flat `.cfg` beside flat images,
and it is wrong now.

An overlay's `.cfg` references its sprites **by name**:

    overlay0_desc0_overlay = img/dpad-left.png

Rename the sprite and the overlay stops working, silently, in a way that
looks like a broken download rather than a rename. So this plugin
installs an overlay's files verbatim or refuses the overlay, and this
module's job is to answer *which* -- before a plan is built, with a
message that names the offending path.

The rules being checked are the host's own, restated here only in the
sense that a plugin should fail with an explanation rather than have its
plan rejected. `rom_hub.types` validates all of it again on arrival and
`rom_hub.paths` again before anything is opened; nothing here is what
makes an install safe.
"""

import posixpath

# Mirrors `rom_hub.types._ALLOWED_PUNCTUATION`. Everything a path
# component may contain besides alphanumerics, which are tested with
# `str.isalnum` because it is unicode-aware -- this repository carries
# Japanese and accented names an ASCII allowlist would drop.
_ALLOWED_PUNCTUATION = frozenset(" .-_()[]+,'!&~@#=")

_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: `rom_hub.types._MAX_FILENAME_CHARS`.
MAX_COMPONENT_CHARS = 200

#: `rom_hub.types.MAX_SUBDIR_COMPONENTS` and `MAX_SUBDIR_CHARS`.
MAX_SUBDIR_COMPONENTS = 8
MAX_SUBDIR_CHARS = 240


class PathNotExpressible(Exception):
    """This repository path cannot be installed under its own name."""


def split_repo_path(path: str) -> tuple[str | None, str]:
    """A repository path split into `(subdir, filename)`.

    `subdir` is None for a file at the repository root, which is what
    `FetchFile` wants for a flat destination.
    """
    directory, _, name = path.rpartition("/")
    return (directory or None), name


def check_component(component: str) -> None:
    """One path component, against the rules the host applies to a name."""
    if not component:
        raise PathNotExpressible("a path component is empty")
    if len(component) > MAX_COMPONENT_CHARS:
        raise PathNotExpressible(
            f"{component!r} is longer than the {MAX_COMPONENT_CHARS} "
            f"characters a path component may be"
        )
    bad = sorted(
        {c for c in component if not (c.isalnum() or c in _ALLOWED_PUNCTUATION)}
    )
    if bad:
        raise PathNotExpressible(
            f"{component!r} contains characters a host will not write: {bad!r}"
        )
    if not component.strip(". "):
        raise PathNotExpressible(f"{component!r} is only dots and spaces")
    if component != component.rstrip(". "):
        raise PathNotExpressible(f"{component!r} ends in a dot or a space")
    if component.split(".")[0].upper() in _RESERVED_STEMS:
        raise PathNotExpressible(
            f"{component!r} is a Windows reserved device name"
        )


def check_repo_path(subdir: str | None, filename: str) -> None:
    """The whole destination: every directory component, then the name."""
    if subdir is not None:
        if len(subdir) > MAX_SUBDIR_CHARS:
            raise PathNotExpressible(
                f"the directory {subdir!r} is longer than the "
                f"{MAX_SUBDIR_CHARS} characters a subdirectory may be"
            )
        parts = subdir.split("/")
        if len(parts) > MAX_SUBDIR_COMPONENTS:
            raise PathNotExpressible(
                f"the directory {subdir!r} nests {len(parts)} deep, over the "
                f"{MAX_SUBDIR_COMPONENTS} a plugin may ask for"
            )
        for part in parts:
            check_component(part)
    check_component(filename)


def is_expressible(path: str) -> bool:
    """True when `path` can be installed verbatim. For a filter, not a gate."""
    try:
        check_repo_path(*split_repo_path(posixpath.normpath(path)))
    except PathNotExpressible:
        return False
    return True
