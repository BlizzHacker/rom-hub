"""The Gaseous `LibraryBackend`: the seam's second implementation.

Cookie auth, platform resolution, a whole-file multipart upload, and the
background-queue wait that is what actually registers a ROM. Nothing
outside this package names Gaseous.
"""

from .backend import BACKEND_NAME, CAPABILITIES, GaseousBackend, settings_from_env
from .client import UNKNOWN_PLATFORM_ID, GaseousClient, GaseousError
from .imports import ImportWaiter

__all__ = [
    "BACKEND_NAME",
    "CAPABILITIES",
    "UNKNOWN_PLATFORM_ID",
    "GaseousBackend",
    "GaseousClient",
    "GaseousError",
    "ImportWaiter",
    "settings_from_env",
]
