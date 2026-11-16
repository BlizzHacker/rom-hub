"""Host-side FlareSolverr fallback transport for Cloudflare-challenged sites.

Plugins never see the FlareSolverr URL, never hold sockets, and never receive
raw clearance cookies. When an allowed HTTPS origin answers a GET request with
a Cloudflare challenge (403/503), this transport optionally delegates the
challenge to the operator-configured FlareSolverr `/v1` endpoint and caches
per-host `cf_clearance` cookies strictly inside the host transport.
"""
from __future__ import annotations

import os
import threading
from urllib.parse import urlsplit

import httpx

_CHALLENGE_MARKERS = (
    b"Just a moment...",
    b"challenges.cloudflare.com",
    b"__cf_chl_",
    b"Attention Required! | Cloudflare",
)


def normalize_flaresolverr_url(raw: str | None) -> str | None:
    """Validate and normalize an operator-configured FlareSolverr base URL."""
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError(f"invalid FlareSolverr URL: {raw!r}")
    return text


def is_cloudflare_challenge(
    status_code: int,
    headers: httpx.Headers,
    body: bytes,
) -> bool:
    """Return True when an HTTP response is a Cloudflare challenge page."""
    if status_code not in (403, 503):
        return False
    cf_mitigated = str(headers.get("cf-mitigated") or "").lower()
    if "challenge" in cf_mitigated:
        return True
    sample = body[:65536]
    return any(marker in sample for marker in _CHALLENGE_MARKERS)


class FlareSolverrTransport(httpx.BaseTransport):
    """httpx transport that transparently solves Cloudflare challenges on GET."""

    def __init__(
        self,
        flaresolverr_url: str,
        inner: httpx.BaseTransport | None = None,
        *,
        solver_transport: httpx.BaseTransport | None = None,
        timeout: float = 65.0,
    ):
        normalized = normalize_flaresolverr_url(flaresolverr_url)
        if not normalized:
            raise ValueError("flaresolverr_url must not be empty")
        self.flaresolverr_url = normalized
        self._inner = inner or httpx.HTTPTransport()
        self._solver_transport = solver_transport
        self._timeout = timeout
        self._lock = threading.RLock()
        self._host_state: dict[str, tuple[str, str]] = {}

    def _endpoint(self) -> str:
        if self.flaresolverr_url.endswith("/v1"):
            return self.flaresolverr_url
        return self.flaresolverr_url + "/v1"

    def _apply_cached(self, request: httpx.Request) -> httpx.Request:
        host = (request.url.host or "").lower()
        with self._lock:
            cached = self._host_state.get(host)
        if not cached:
            return request
        cookie_header, ua = cached
        headers = httpx.Headers(request.headers)
        if cookie_header and "cookie" not in headers:
            headers["Cookie"] = cookie_header
        if ua:
            headers["User-Agent"] = ua
        return httpx.Request(
            method=request.method,
            url=request.url,
            headers=headers,
            content=request.content,
            extensions=request.extensions,
        )

    def _solve(self, url: str) -> tuple[int, str, dict[str, str]]:
        payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._solver_transport,
        ) as client:
            resp = client.post(self._endpoint(), json=payload)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "ok":
            raise RuntimeError(
                f"FlareSolverr failed for {url!r}: {data.get('message') or data.get('status')}"
            )
        sol = data.get("solution") or {}
        status = int(sol.get("status") or 200)
        html = str(sol.get("response") or "")
        ua = str(sol.get("userAgent") or "")
        cookies = sol.get("cookies") or []
        pairs = []
        for c in cookies:
            if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
                pairs.append(f"{c['name']}={c['value']}")
        host = (urlsplit(url).hostname or "").lower()
        cookie_header = "; ".join(pairs)
        if host and (cookie_header or ua):
            with self._lock:
                self._host_state[host] = (cookie_header, ua)
        resp_headers = {"content-type": "text/html; charset=utf-8"}
        return status, html, resp_headers

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        req = self._apply_cached(request)
        response = self._inner.handle_request(req)
        if request.method.upper() != "GET":
            return response
        if response.status_code not in (403, 503):
            return response
        body = response.read()
        if not is_cloudflare_challenge(response.status_code, response.headers, body):
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=body,
                request=req,
            )
        response.close()
        status, html, resp_headers = self._solve(str(request.url))
        return httpx.Response(
            status_code=status,
            headers=resp_headers,
            content=html.encode("utf-8", errors="replace"),
            request=request,
        )

    def close(self) -> None:
        self._inner.close()


def build_transport(
    base_transport: httpx.BaseTransport | None = None,
    *,
    flaresolverr_url: str | None = None,
    solver_transport: httpx.BaseTransport | None = None,
) -> httpx.BaseTransport | None:
    """Wrap `base_transport` with FlareSolverrTransport when a URL is configured."""
    raw = flaresolverr_url if flaresolverr_url is not None else os.environ.get("ROM_HUB_FLARESOLVERR_URL", "")
    normalized = normalize_flaresolverr_url(raw)
    if not normalized:
        return base_transport
    return FlareSolverrTransport(
        normalized,
        inner=base_transport,
        solver_transport=solver_transport,
    )
