"""Host-side HTTP. The only component in the system that opens a socket
on a plugin's behalf, and it is called only after netpolicy has approved
the URL.

Approving the *host* says nothing about the *size* of what that host sends
back. An allowed host can legitimately serve multi-GB archives, and the body
is buffered in host memory on the plugin's behalf, so there is a hard byte
budget and it fails closed past it. The budget is deliberately under the
protocol's per-message cap, because whatever comes back here still has to fit
in one JSON frame on its way to the plugin.
"""

import os
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from rom_hub.flaresolverr import build_transport
from rom_hub.types import validate_headers

USER_AGENT = "rom-hub/0.1 (+https://github.com/rommapp/romm)"

#: The one host a GitHub token is ever sent to, and only on GET. Anonymous
#: API use is 60 requests an hour per IP, which a few release listings use
#: up; a token raises it to 5,000. The token is the operator's, set as
#: ROM_HUB_GITHUB_TOKEN, and must carry NO scopes: every plugin allowed
#: api.github.com can read whatever it can read.
GITHUB_API_HOST = "api.github.com"


def _github_auth(method: str, url: str) -> dict[str, str]:
    token = (os.environ.get("ROM_HUB_GITHUB_TOKEN") or "").strip()
    if not token or method != "GET":
        return {}
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return {}
    return {"Authorization": f"Bearer {token}"} if host == GITHUB_API_HOST else {}


# Under protocol.MAX_MESSAGE_CHARS (8 MiB): the body is JSON-escaped into a
# reply frame, which only ever grows it.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class Fetcher(Protocol):
    def get(
        self, url: str, params: dict, *, headers: dict[str, str] | None = None
    ) -> tuple[int, str]: ...


class ResponseTooLarge(Exception):
    """An allowed host returned more than the host is willing to buffer."""


class HttpxFetcher:
    def __init__(
        self,
        timeout: float = 30.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
        flaresolverr_url: str | None = None,
    ):
        self.max_response_bytes = max_response_bytes
        effective_transport = build_transport(transport, flaresolverr_url=flaresolverr_url)
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # a redirect could escape the allowlist
            headers={"User-Agent": USER_AGENT},
            transport=effective_transport,
        )

    def _too_large(self, url: str, size: int) -> ResponseTooLarge:
        return ResponseTooLarge(
            f"response from {url!r} exceeded the {self.max_response_bytes}-byte "
            f"limit at {size} bytes; bulk transfer is a host concern, not a "
            f"ctx.http one"
        )

    def _stream_request(self, method: str, url: str, **kwargs) -> tuple[int, str]:
        # One HttpxFetcher is shared by every plugin in a fan-out, so a
        # Set-Cookie from one plugin's request would ride along on another
        # plugin's request to the same allowed host: a side channel between
        # plugins that are supposed to be isolated. Nothing in the brokered
        # API can set or read a cookie, so there is nothing to preserve.
        self._client.cookies.clear()
        with self._client.stream(method, url, **kwargs) as resp:
            # Believe Content-Length when it is offered: refusing before a
            # single body byte is pulled is strictly cheaper. It is a hint,
            # not a guarantee, so the streaming budget below still applies.
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.max_response_bytes:
                raise self._too_large(url, int(declared))

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise self._too_large(url, total)
                chunks.append(chunk)

            status_code = resp.status_code
            encoding = resp.charset_encoding or "utf-8"

        try:
            text = b"".join(chunks).decode(encoding, errors="replace")
        except LookupError:
            text = b"".join(chunks).decode("utf-8", errors="replace")
        return status_code, text

    def get(
        self,
        url: str,
        params: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        safe_headers = validate_headers(headers) if headers else None
        auth = _github_auth("GET", url)
        if auth and not any(k.lower() == "authorization" for k in (safe_headers or {})):
            safe_headers = {**(safe_headers or {}), **auth}
        return self._stream_request(
            "GET", url, params=params or None, headers=safe_headers
        )

    def post(
        self,
        url: str,
        *,
        params: dict | None = None,
        json: Any = None,
        data: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        safe_headers = validate_headers(headers) if headers else None
        return self._stream_request(
            "POST",
            url,
            params=params or None,
            json=json,
            data=data,
            headers=safe_headers,
        )

    def close(self) -> None:
        self._client.close()
