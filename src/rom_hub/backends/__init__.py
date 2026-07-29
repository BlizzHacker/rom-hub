"""Which library backend the Hub is talking to, chosen by configuration.

`ROM_HUB_BACKEND` names it; `romm` is the default because it is the one
that exists and the one every deployment on the estate is already
pointing at. Adding a backend is adding an entry to `_BACKENDS` and a
package beside `romm/` -- nothing above this module gains a branch.

Entries are imported lazily. A backend's package may pull in a client
library that nothing else needs, and `rom-hub search` -- which never
touches a library server at all -- should not pay for it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from .base import (
    ALL_CAPABILITIES,
    ARTWORK,
    CAPABILITY_HELP,
    COLLECTIONS,
    IMPORT,
    METADATA,
    SCAN,
    BackendError,
    BackendNotConfigured,
    CapabilityUnsupported,
    LibraryBackend,
    Scanner,
    UnknownBackend,
    capabilities_of,
    require,
)

DEFAULT_BACKEND = "romm"

# name -> "module:attribute". The attribute is a class with a `from_env()`
# classmethod and a `capabilities()` method.
_BACKENDS: dict[str, str] = {
    "romm": "rom_hub.backends.romm.backend:RommBackend",
}


@dataclass(frozen=True)
class BackendInfo:
    """What `rom-hub backend info` prints, without opening a connection."""

    name: str
    capabilities: frozenset[str]
    settings: tuple[str, ...]
    summary: str


def available() -> list[str]:
    return sorted(_BACKENDS)


def backend_class(name: str):
    """The class implementing `name`, imported on demand."""
    try:
        target = _BACKENDS[name]
    except KeyError:
        raise UnknownBackend(
            f"unknown backend {name!r}; ROM_HUB_BACKEND must be one of: "
            f"{', '.join(available())}"
        ) from None
    module_name, _, attribute = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


def describe(name: str) -> BackendInfo:
    """A backend's declared capabilities and settings, without connecting.

    Deliberately connectionless. An operator asking "what can this thing
    do" is often asking precisely *because* the connection is not working
    yet, and a command that needs a live server to answer is no use then.
    """
    cls = backend_class(name)
    settings = tuple(
        primary for primary, _alias in getattr(cls, "SETTING_NAMES", ())
    )
    return BackendInfo(
        name=name,
        capabilities=frozenset(getattr(cls, "CAPABILITIES", ())),
        settings=settings,
        summary=(getattr(cls, "__doc__", "") or "").strip().splitlines()[0]
        if (getattr(cls, "__doc__", "") or "").strip()
        else "",
    )


def load(name: str, **kwargs) -> LibraryBackend:
    """Build the named backend from the environment.

    Raises `UnknownBackend` for a name that is not installed and
    `BackendNotConfigured` for one that is but has nothing to connect to.
    Neither opens a socket: authentication is lazy, exactly as it was
    when this was a bare `RommClient`.
    """
    return backend_class(name).from_env(**kwargs)


__all__ = [
    "ALL_CAPABILITIES",
    "ARTWORK",
    "CAPABILITY_HELP",
    "COLLECTIONS",
    "DEFAULT_BACKEND",
    "IMPORT",
    "METADATA",
    "SCAN",
    "BackendError",
    "BackendInfo",
    "BackendNotConfigured",
    "CapabilityUnsupported",
    "LibraryBackend",
    "Scanner",
    "UnknownBackend",
    "available",
    "backend_class",
    "capabilities_of",
    "describe",
    "load",
    "require",
]
