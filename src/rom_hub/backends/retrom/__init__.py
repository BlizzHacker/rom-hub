"""Retrom as a library backend.

Retrom (https://github.com/jmberesford/retrom) is a self-hosted game
library service. It is not a second RomM, and this package exists mostly
to record where the two differ:

* its library is **derived from the filesystem** -- there is no upload
  API, and a ROM is imported by writing a file where a scan will find it
  (`upload`);
* its writes are **gRPC only**; the REST service is three read routes
  (`grpcweb`);
* it has **no authentication** and **no collections** (`client`, `backend`).

Nothing outside this package knows any of that.
"""

from .backend import CAPABILITIES, SETTING_NAMES, RetromBackend, settings_from_env
from .client import RetromClient, RetromError
from .grpcweb import GrpcError, GrpcWebChannel
from .upload import WebDavClient

__all__ = [
    "CAPABILITIES",
    "SETTING_NAMES",
    "GrpcError",
    "GrpcWebChannel",
    "RetromBackend",
    "RetromClient",
    "RetromError",
    "WebDavClient",
    "settings_from_env",
]
