"""The plugin's view of the world.

This API offers no socket. A plugin calls ctx.http, which is an RPC back to
the host; the host checks the manifest allowlist before fetching anything.
The shape deliberately mirrors `requests` so the idiom is familiar.

Phase 1 does not sandbox the plugin subprocess, so this is the supported path
rather than the only possible one — a hostile plugin can still `import socket`
and skip the broker. See "Security: the broker model" in docs/DESIGN.md.
"""

import json
from dataclasses import dataclass
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


@dataclass
class PluginContext:
    config: dict
    http: HttpClient | None
