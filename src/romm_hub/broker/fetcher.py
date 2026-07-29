"""Host-side HTTP. The only component in the system that opens a socket
on a plugin's behalf, and it is called only after netpolicy has approved
the URL.
"""

from typing import Protocol

import httpx

USER_AGENT = "romm-hub/0.1 (+https://github.com/rommapp/romm)"


class Fetcher(Protocol):
    def get(self, url: str, params: dict) -> tuple[int, str]: ...


class HttpxFetcher:
    def __init__(self, timeout: float = 30.0):
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # a redirect could escape the allowlist
            headers={"User-Agent": USER_AGENT},
        )

    def get(self, url: str, params: dict) -> tuple[int, str]:
        resp = self._client.get(url, params=params or None)
        return resp.status_code, resp.text

    def close(self) -> None:
        self._client.close()
