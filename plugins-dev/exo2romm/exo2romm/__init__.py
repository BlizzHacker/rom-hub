"""eXo2romm ROM Hub plugin — repackage eXoDOS zips for standalone RomM playback.

Based on `eXo2romm` by derkommutator (https://github.com/derkommutator/eXo2romm, Unlicense).
This plugin was inspired to assist with more users having this workflow cleanly in ROM Hub and ROMarr.
"""

from .cores import Cores
from .platforms import PLATFORMS
from .repackager import (
    DEFAULT_DOS_DIR,
    DEFAULT_OUTPUT_DIR,
    INSPIRED_NOTE,
    UPSTREAM_AUTHOR,
    UPSTREAM_REPO,
    UPSTREAM_URL,
    BatchResult,
    ZipResult,
    inspect_exo_zip,
    repackage_batch,
    repackage_zip,
    rewrite_dosbox_conf_bytes,
)

__all__ = [
    "BatchResult",
    "Cores",
    "DEFAULT_DOS_DIR",
    "DEFAULT_OUTPUT_DIR",
    "INSPIRED_NOTE",
    "PLATFORMS",
    "UPSTREAM_AUTHOR",
    "UPSTREAM_REPO",
    "UPSTREAM_URL",
    "ZipResult",
    "inspect_exo_zip",
    "repackage_batch",
    "repackage_zip",
    "rewrite_dosbox_conf_bytes",
]
