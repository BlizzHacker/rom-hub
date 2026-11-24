"""A fake `ctx` whose `http` answers from a table of synthetic responses."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rom_hub_sdk import HttpResponse, PluginContext

USER = "dl-user"
PASSWORD = "s3cret:pa@ss"
# The password as it is written in a URL, percent-encoded.
PASSWORD_IN_URL = "s3cret%3Apa%40ss"

ONE_MIB = 1024 * 1024


class FakeHTTP:
    """Routes a URL to `(status, text)`, or to an exception to raise.

    An unrouted URL answers 404, which is what a shop without a sections
    API does.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        answer = self.routes.get(url, (404, "not found"))
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        if not isinstance(body, str):
            body = json.dumps(body)
        return HttpResponse(status_code=status, text=body)

    def urls(self):
        return [url for url, _ in self.calls]

    def headers_for(self, url):
        return [headers for called, headers in self.calls if called == url]


def ctx(routes=None, sources=(), titledb=False):
    config = {"sources": list(sources), "titledb": titledb}
    return PluginContext(config=config, http=FakeHTTP(routes))


def too_large(url):
    # The message rom-hub's broker sends back for a body over its cap.
    return RuntimeError(
        f"ResponseTooLarge: response from {url!r} exceeded the {4 * ONE_MIB}-byte "
        f"limit at {4 * ONE_MIB + 1} bytes; bulk transfer is a host concern, not a "
        "ctx.http one"
    )


def blocked():
    # The message rom-hub sends for a URL outside a NetworkPolicy.
    return RuntimeError("blocked request: destination is outside the plugin's network allowlist")


def sections(*items, section_id="all"):
    return {
        "success": "hello",
        "sections": [{"id": section_id, "title": "All", "items": list(items)}],
    }


BASE = "Fixture Quest [0100F1C7A0B10000][v0].nsp"
UPDATE = "Fixture Quest [0100F1C7A0B10800][v131072].nsz"
DLC = "Fixture Quest Extra [0100F1C7A0B11001][v0].nsp"
OTHER = "Sample Racer [0100F1C7A0B20000][v0].xci"
