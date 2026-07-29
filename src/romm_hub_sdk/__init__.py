"""Deprecated alias for `rom_hub_sdk`.

`romm_hub_sdk` was the SDK's name before the Hub learned to serve more
than RomM. Importing it still works and yields the *same* objects as
`rom_hub_sdk` -- not copies -- so a plugin written against the old name
keeps passing the host's validation. It emits a DeprecationWarning, and
it will be removed.

Plugin authors: change `from romm_hub_sdk import ...` to
`from rom_hub_sdk import ...`. Nothing else about the contract changed;
`rpp_version = "1"` in your manifest is still correct.
"""

from rom_hub._compat import alias_package

_sdk = alias_package(__name__, "rom_hub_sdk")

__all__ = list(getattr(_sdk, "__all__", ()))


def __getattr__(name: str):
    return getattr(_sdk, name)


def __dir__():
    return sorted(set(__all__) | set(dir(_sdk)))
