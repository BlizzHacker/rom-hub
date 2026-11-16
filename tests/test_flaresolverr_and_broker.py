"""Tests for host-side FlareSolverrTransport and broker HTTP headers/POST support."""
from __future__ import annotations

import json

import httpx
import pytest

from rom_hub.broker.fetcher import HttpxFetcher
from rom_hub.flaresolverr import (
    FlareSolverrTransport,
    build_transport,
    is_cloudflare_challenge,
    normalize_flaresolverr_url,
)


def test_normalize_flaresolverr_url_accepts_http_https_and_rejects_bad_schemes():
    assert normalize_flaresolverr_url("") is None
    assert normalize_flaresolverr_url("  http://localhost:8191/  ") == "http://localhost:8191"
    assert normalize_flaresolverr_url("https://solver.example.lan:8191/v1") == "https://solver.example.lan:8191/v1"
    with pytest.raises(ValueError):
        normalize_flaresolverr_url("ftp://localhost:8191")
    with pytest.raises(ValueError):
        normalize_flaresolverr_url("javascript:alert(1)")


def test_flaresolverr_transport_solves_challenge_and_caches_clearance_per_host():
    seen_target_headers: list[dict[str, str]] = []
    solver_calls: list[dict] = []

    def target_handler(request: httpx.Request) -> httpx.Response:
        seen_target_headers.append(dict(request.headers))
        if "cf_clearance=ok123" in request.headers.get("cookie", ""):
            return httpx.Response(200, text="<html><pre>Solved Guide</pre></html>")
        return httpx.Response(
            403,
            headers={"cf-mitigated": "challenge"},
            text="<html>Just a moment... challenges.cloudflare.com</html>",
        )

    def solver_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        solver_calls.append(payload)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "status": 200,
                    "response": "<html><pre>Solved via Solver</pre></html>",
                    "userAgent": "SolverChrome/125",
                    "cookies": [{"name": "cf_clearance", "value": "ok123"}],
                },
            },
        )

    transport = FlareSolverrTransport(
        "http://localhost:8191",
        inner=httpx.MockTransport(target_handler),
        solver_transport=httpx.MockTransport(solver_handler),
    )
    fetcher = HttpxFetcher(transport=transport)
    try:
        status1, text1 = fetcher.get("https://gamefaqs.gamespot.com/search", {"game": "chrono"})
        assert status1 == 200
        assert "Solved via Solver" in text1
        assert len(solver_calls) == 1
        assert solver_calls[0]["cmd"] == "request.get"

        # Second request reuses cached cf_clearance without hitting FlareSolverr again.
        status2, text2 = fetcher.get("https://gamefaqs.gamespot.com/snes/563538/faqs/1")
        assert status2 == 200
        assert "Solved Guide" in text2
        assert len(solver_calls) == 1
        assert seen_target_headers[1]["user-agent"] == "SolverChrome/125"
    finally:
        fetcher.close()


def test_flaresolverr_challenge_detector_ignores_ordinary_403_and_non_cf_503():
    assert not is_cloudflare_challenge(403, httpx.Headers({}), b"Access denied")
    assert not is_cloudflare_challenge(200, httpx.Headers({"cf-mitigated": "challenge"}), b"")
    assert is_cloudflare_challenge(403, httpx.Headers({"cf-mitigated": "challenge"}), b"")
    assert is_cloudflare_challenge(503, httpx.Headers({}), b"Just a moment...")


def test_fetcher_post_and_validated_headers_block_forbidden_headers():
    seen: list[tuple[str, dict[str, str], bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, dict(request.headers), request.content))
        return httpx.Response(200, json={"ok": True})

    fetcher = HttpxFetcher(transport=httpx.MockTransport(handler))
    try:
        status, body = fetcher.post(
            "https://api.nexusmods.com/v2/graphql",
            json={"query": "{ mods { totalCount } }"},
            headers={"x-api-key": "test-key"},
        )
        assert status == 200
        assert json.loads(body) == {"ok": True}
        assert seen[0][0] == "POST"
        assert seen[0][1]["x-api-key"] == "test-key"

        with pytest.raises(ValueError):
            fetcher.get("https://example.com/", headers={"Cookie": "evil=1"})
    finally:
        fetcher.close()


def test_build_transport_returns_base_when_unset():
    base = httpx.MockTransport(lambda req: httpx.Response(200))
    assert build_transport(base, flaresolverr_url="") is base
