"""Deprecated alias for `rom_hub`.

The host package was `romm_hub` before the rename. Importing it, or any
module under it, still works and yields the *same* module objects as
`rom_hub` -- see `rom_hub._compat` for why that identity matters. It
emits a DeprecationWarning, and it will be removed.

`romm_hub.romm.*` also resolves, to `rom_hub.backends.romm.*`, since the
RomM client moved behind the `LibraryBackend` seam at the same time.
"""

from rom_hub._compat import alias_package

_hub = alias_package(__name__, "rom_hub")


def __getattr__(name: str):
    return getattr(_hub, name)


def __dir__():
    return sorted(dir(_hub))
