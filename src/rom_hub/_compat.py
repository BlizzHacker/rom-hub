"""Import aliases for the pre-rename `romm_hub` / `romm_hub_sdk` packages.

`rom_hub_sdk` is what a plugin imports, and plugins are third-party git
repositories the Hub does not control. Renaming the package is therefore
a breaking change for every plugin already published against the old
name, and the only honest mitigation is to keep the old name resolving
while saying loudly that it is going away.

**The alias yields the identical module object, never a copy.** A second
`romm_hub_sdk.FetchPlan` class that merely looked the same would fail
every `isinstance` check the host makes against the real one, which is a
worse outcome than a clean ImportError. That is why this is a meta-path
finder handing back `sys.modules[<new name>]` rather than a package whose
`__path__` points at the same files: two `__path__`s over one directory
produce two distinct classes per module.

Only submodules go through the finder. `romm_hub/__init__.py` and
`romm_hub_sdk/__init__.py` are real (tiny) modules on disk, because
something has to run in order to install the finder in the first place.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

# Old top-level package -> new top-level package.
ALIASES = {
    "romm_hub_sdk": "rom_hub_sdk",
    "romm_hub": "rom_hub",
}

# Modules that also *moved* in the same change, applied after the
# top-level rename. Longest prefix first so a nested move cannot be
# shadowed by its own parent.
MOVES = (
    ("rom_hub.romm", "rom_hub.backends.romm"),
)

_finder_installed = False


def translate(fullname: str) -> str | None:
    """The new name for an old dotted module name, or None if it is not one."""
    for old, new in ALIASES.items():
        if fullname == old:
            return new
        if fullname.startswith(old + "."):
            return _moved(new + fullname[len(old) :])
    return None


def _moved(name: str) -> str:
    for old, new in MOVES:
        if name == old or name.startswith(old + "."):
            return new + name[len(old) :]
    return name


class _AliasLoader(Loader):
    """Loads nothing: it hands back the module that already exists."""

    def __init__(self, target: str):
        self._target = target

    def create_module(self, spec: ModuleSpec):
        return importlib.import_module(self._target)

    def exec_module(self, module) -> None:  # already executed under its real name
        pass


class _AliasFinder(MetaPathFinder):
    def find_spec(self, fullname: str, path=None, target=None):
        if fullname in ALIASES:
            # The shim packages themselves are on disk; let the normal
            # machinery find them, or this recurses into itself.
            return None
        new_name = translate(fullname)
        if new_name is None:
            return None
        try:
            module = importlib.import_module(new_name)
        except ImportError:
            # Not an alias for anything that exists. Let the normal
            # machinery produce the usual ModuleNotFoundError.
            return None
        spec = ModuleSpec(fullname, _AliasLoader(new_name))
        # A package alias must still be a package, or `import
        # romm_hub.broker.host` stops at `romm_hub.broker`.
        spec.submodule_search_locations = getattr(module, "__path__", None)
        return spec


def alias_package(old_name: str, new_name: str):
    """Wire `old_name` to `new_name` and return the real package.

    Called from the shim package's `__init__`, which is the only code that
    is guaranteed to run before anything tries to import a submodule.
    """
    global _finder_installed
    warnings.warn(
        f"{old_name} has been renamed to {new_name}. The old name still "
        f"works and returns the same objects, but it will be removed; "
        f"import {new_name} instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    if not _finder_installed:
        # Ahead of the path finders: `src/romm_hub/` exists on disk and
        # would otherwise answer for `romm_hub.types` with nothing in it.
        sys.meta_path.insert(0, _AliasFinder())
        _finder_installed = True
    return importlib.import_module(new_name)
