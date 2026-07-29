"""Environment settings, and the `ROMM_HUB_*` names they used to have.

The project was `romm-hub` before it learned to talk to more than one
library server, so every `ROM_HUB_*` variable below spent its first life
spelled `ROMM_HUB_*`. Those names are already written into shell profiles
and systemd units on the deployment target, and a rename that silently
stops reading them is a rename that silently relocates the plugin
directory and the job queue -- the operator's plugins "disappear" and
nothing says why.

So the old spelling keeps working, and says so exactly once per variable
per process. Precedence is unambiguous: the new name wins whenever it is
set to a non-empty value, so a host part-way through migrating is never
ambiguous about which one it is obeying.

This deliberately does **not** cover `ROMM_URL`, `ROMM_USER` and
`ROMM_PASSWORD`. Those are not the Hub's name -- they are *RomM's*, they
belong to one backend, and they are correct as they stand. See
`rom_hub.backends.romm`.
"""

from __future__ import annotations

import os
import sys
import warnings

CURRENT_PREFIX = "ROM_HUB_"
DEPRECATED_PREFIX = "ROMM_HUB_"

# One notice per variable per process. A CLI that reprinted this on every
# `default_root()` call would bury its own output.
_announced: set[str] = set()


def deprecated_name(name: str) -> str | None:
    """The pre-rename spelling of `name`, or None if it never had one."""
    if name.startswith(CURRENT_PREFIX):
        return DEPRECATED_PREFIX + name[len(CURRENT_PREFIX) :]
    return None


def get(name: str, default: str = "") -> str:
    """`name` from the environment, falling back to its `ROMM_HUB_*` spelling.

    An empty value counts as unset, matching how the rest of the CLI reads
    these: `ROM_HUB_HOME=` is a shell mistake, not a request to use the
    empty path.
    """
    value = os.environ.get(name)
    if value:
        return value

    old = deprecated_name(name)
    if old:
        legacy = os.environ.get(old)
        if legacy:
            _announce(old, name)
            return legacy
    return default


def _announce(old: str, new: str) -> None:
    message = (
        f"{old} is the old name for {new} and still works, but it will be "
        f"removed; set {new} instead."
    )
    warnings.warn(message, DeprecationWarning, stacklevel=3)
    if old in _announced:
        return
    _announced.add(old)
    # stderr as well as `warnings`, because DeprecationWarning is invisible
    # by default outside __main__ and the operator running the CLI is
    # exactly the person who needs to see this.
    print(f"note: {message}", file=sys.stderr)


def reset_announcements() -> None:
    """Forget which notices have been printed. For tests only."""
    _announced.clear()
