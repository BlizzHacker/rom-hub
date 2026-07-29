"""The RomM `LibraryBackend`: everything the plugin never sees.

Auth, platform resolution, chunked upload, the socket.io scan that
actually registers it, and the partial metadata write. Nothing outside
this package names RomM.
"""

from .backend import BACKEND_NAME, CAPABILITIES, RommBackend, settings_from_env
from .client import RommClient, RommError

__all__ = [
    "BACKEND_NAME",
    "CAPABILITIES",
    "RommBackend",
    "RommClient",
    "RommError",
    "settings_from_env",
]
