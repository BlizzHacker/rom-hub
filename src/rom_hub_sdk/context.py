"""The plugin's view of the world.

This API offers no socket. A plugin calls ctx.http, which is an RPC back to
the host; the host checks the manifest allowlist before fetching anything.
The shape deliberately mirrors `requests` so the idiom is familiar.

Since Phase 1.5 this is not merely the supported path but the only one that
works: the subprocess confines itself with a seccomp filter before any plugin
module is imported, so `import socket` yields a PermissionError rather than a
connection. File reads are still unconfined -- seccomp cannot filter on a path.
See "Security: the broker model" in docs/DESIGN.md.

`ctx.data_assets` is the one thing here that is not an RPC. A plugin whose
source is a *file* rather than a service declares it in `[[data_assets]]`,
and the host fetches, verifies and caches it before this process starts --
so what arrives is a path to bytes that already match a declared sha256.
Open it read-only; it is a shared cache, not scratch space.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class Channel(Protocol):
    def send(self, msg: dict) -> None: ...
    def await_result(self, call_id: str) -> Any: ...


@dataclass
class HttpResponse:
    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient:
    def __init__(self, channel: Channel):
        self._channel = channel
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"p{self._counter}"

    def get(self, url: str, params: dict | None = None) -> HttpResponse:
        call_id = self._next_id()
        self._channel.send(
            {
                "kind": "call",
                "id": call_id,
                "method": "http.get",
                "params": {"url": url, "params": params or {}},
            }
        )
        result = self._channel.await_result(call_id)
        return HttpResponse(status_code=result["status_code"], text=result["text"])


class DataAssetUnavailable(Exception):
    """A declared data asset is not among the ones the host resolved."""


@dataclass
class PluginContext:
    config: dict
    http: HttpClient | None
    #: `{name: absolute path}` for every `[[data_assets]]` entry, already
    #: fetched and hash-verified by the host. Empty for a plugin that
    #: declares none -- and empty is not something to work around: a
    #: capability that needs an asset it was not given should say so.
    data_assets: dict[str, str] = field(default_factory=dict)

    def data_asset(self, name: str) -> str:
        """The verified path for one declared asset, or a legible refusal.

        Preferred over indexing `data_assets` directly, because the KeyError
        that would otherwise reach the operator says only the name.
        """
        path = self.data_assets.get(name)
        if not path:
            available = sorted(self.data_assets) or "(none)"
            raise DataAssetUnavailable(
                f"the data asset {name!r} was not provided by the host; it "
                f"resolved {available}. Declare it in manifest.toml under "
                f"[[data_assets]] -- with its url, sha256 and size_bytes -- "
                f"and reinstall the plugin so the new manifest is read."
            )
        return path
