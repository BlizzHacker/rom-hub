"""Archive.org login and ephemeral cookie sessions for ROM Hub host HTTP."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from .ia_auth import ArchiveAuthError, credentials

LOGIN_URL = "https://archive.org/services/xauthn/?op=login"
_HOST = re.compile(r"ia[0-9]+\.(?:us|ca)\.archive\.org\Z")
_COOKIE_NAMES = ("logged-in-user", "logged-in-sig")
_LOCK = threading.Lock()
_SESSIONS = {}


def _cookie(name, raw):
    if not isinstance(raw, str):
        raise ArchiveAuthError("Archive.org login returned invalid cookies")
    first, _, attrs = raw.partition(";")
    value = first.strip()
    if value.startswith(name + "="):
        value = value[len(name) + 1:]
    if not value or ";" in value or any(not 33 <= ord(c) <= 126 for c in value):
        raise ArchiveAuthError("Archive.org login returned invalid cookies")
    expiry = None
    for part in attrs.split(";"):
        key, sep, val = part.strip().partition("=")
        if not sep:
            continue
        if key.lower() == "max-age":
            try:
                expiry = time.time() + max(0, int(val))
            except ValueError:
                pass
        elif key.lower() == "expires" and expiry is None:
            try:
                expiry = parsedate_to_datetime(val.strip()).timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
    return value, expiry


class ArchiveOrgSession:
    """Shared by search and download; secret credentials never enter plugins."""

    def __init__(self, root: Path, login_transport=None, pair=None):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._header = None
        self._expires = None
        self._refresh_at = None
        self._login_transport = login_transport
        self._pair = pair

    def invalidate(self):
        with self._lock:
            self._header = self._expires = self._refresh_at = None

    def _login(self):
        pair = self._pair or credentials(self.root)
        if not pair:
            raise ArchiveAuthError("Archive.org login credentials are missing")
        try:
            # Fixed first-party endpoint; redirects are never followed.
            with httpx.Client(timeout=15, follow_redirects=False,
                              transport=self._login_transport) as client:
                with client.stream("POST", LOGIN_URL,
                                   data={"email": pair[0], "password": pair[1]}) as response:
                    if response.status_code != 200:
                        raise ArchiveAuthError("Archive.org login request failed")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 131072:
                            raise ArchiveAuthError("Archive.org login response is too large")
                    data = json.loads(body)
            if not data.get("success"):
                raise ArchiveAuthError("Archive.org rejected the configured login")
            raw = data["values"]["cookies"]
            parsed = [_cookie(key, raw[key]) for key in _COOKIE_NAMES]
        except ArchiveAuthError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            raise ArchiveAuthError("Archive.org authentication failed") from None
        # Only cookie values, not expiry attributes, go into the request.
        self._header = "; ".join(
            key + "=" + value for key, (value, _) in zip(_COOKIE_NAMES, parsed))
        expirations = [expiry for _, expiry in parsed if expiry is not None]
        self._expires = min(expirations) if expirations else None
        if self._expires is not None:
            remaining = max(0, self._expires - time.time())
            # Avoid a login loop for a cookie with less than five minutes TTL.
            self._refresh_at = self._expires - min(300, remaining * 0.1)
        else:
            self._refresh_at = None

    def header(self):
        with self._lock:
            if (self._header is None or
                (self._refresh_at is not None and time.time() >= self._refresh_at)):
                self._login()
            return self._header

    def renew_after_failure(self, used):
        with self._lock:
            if self._header == used:
                self.invalidate()
            return self.header()


def get_session(root: Path):
    key = str(Path(root).resolve())
    with _LOCK:
        pair = credentials(Path(root))
        fingerprint = hashlib.sha256(json.dumps(pair).encode()).digest()
        if (key not in _SESSIONS or
                _SESSIONS[key]._credential_fingerprint != fingerprint):
            _SESSIONS[key] = ArchiveOrgSession(Path(root))
            _SESSIONS[key]._credential_fingerprint = fingerprint
        return _SESSIONS[key]


def invalidate(root: Path):
    with _LOCK:
        s = _SESSIONS.get(str(Path(root).resolve()))
    if s is not None:
        s.invalidate()


class ArchiveOrgTransport(httpx.BaseTransport):
    """Attach cookies only to IA HTTPS URLs; retry 401/403 once."""

    def __init__(self, manager: ArchiveOrgSession, transport=None):
        self.manager = manager
        self.delegate = transport if transport is not None else httpx.HTTPTransport()

    def handle_request(self, request):
        host = (request.url.host or "").lower().rstrip(".")
        allowed = (request.url.scheme == "https" and
                   (host == "archive.org" or _HOST.fullmatch(host)))
        if not allowed:
            request.headers.pop("cookie", None)
            return self.delegate.handle_request(request)
        old = self.manager.header()
        request.headers["cookie"] = old
        response = self.delegate.handle_request(request)
        if response.status_code not in (401, 403):
            return response
        response.close()
        request.headers["cookie"] = self.manager.renew_after_failure(old)
        return self.delegate.handle_request(request)

    def close(self):
        self.delegate.close()


def archive_transport(slug: str, root: Path):
    if slug != "archive-org" or credentials(Path(root)) is None:
        return None
    return ArchiveOrgTransport(get_session(root))
