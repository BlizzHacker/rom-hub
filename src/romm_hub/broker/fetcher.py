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

from typing import Protocol

import httpx

USER_AGENT = "romm-hub/0.1 (+https://github.com/rommapp/romm)"

# Under protocol.MAX_MESSAGE_BYTES (8 MiB): the body is JSON-escaped into a
# reply frame, which only ever grows it.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class Fetcher(Protocol):
    def get(self, url: str, params: dict) -> tuple[int, str]: ...


class ResponseTooLarge(Exception):
    """An allowed host returned more than the host is willing to buffer."""


class HttpxFetcher:
    def __init__(
        self,
        timeout: float = 30.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
    ):
        self.max_response_bytes = max_response_bytes
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # a redirect could escape the allowlist
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    def _too_large(self, url: str, size: int) -> ResponseTooLarge:
        return ResponseTooLarge(
            f"response from {url!r} exceeded the {self.max_response_bytes}-byte "
            f"limit at {size} bytes; bulk transfer is a host concern, not a "
            f"ctx.http one"
        )

    def get(self, url: str, params: dict) -> tuple[int, str]:
        with self._client.stream("GET", url, params=params or None) as resp:
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

    def close(self) -> None:
        self._client.close()
