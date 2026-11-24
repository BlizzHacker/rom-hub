"""Allowlist enforcement for plugin-initiated HTTP.

Every plugin request passes through check_url() before a socket is opened.
If this module is wrong, the manifest's `network` declaration is decoration.
"""

import os
import re
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"https"})

# urlsplit does not validate what it hands back as `.hostname`: it keeps a
# backslash, a space or a tab inside it, and the wildcard suffix test then
# matched happily, so this module answered "permitted" for
# `evil.example\.archive.org`. Nothing was exploitable -- httpx carries the
# backslash through and resolution fails -- but a policy layer must not be
# the thing saying yes to a string that is not a hostname. Punycode
# (`xn--...`) is plain ASCII and still passes.
_DNS_NAME = re.compile(r"[A-Za-z0-9._-]+")


class PolicyViolation(Exception):
    """A plugin asked for a URL its manifest does not permit."""


def host_matches(host: str, pattern: str) -> bool:
    host = host.lower().strip(".")
    pattern = pattern.lower().strip(".")
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[2:]
        if not suffix:
            return False
        # A wildcard covers exactly one or more leading labels, never the
        # bare domain, and never a domain that merely contains the suffix.
        return host.endswith("." + suffix)
    return host == pattern


# HTTP has no server identity. Restrict it to explicit literal LAN origins.
_LAN = tuple(map(ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "fc00::/7", "::1/128",
)))


@dataclass(frozen=True)
class NetworkPolicy:
    """Declared HTTPS hosts plus grants from the operator's environment."""

    declared: tuple[str, ...]
    https_hosts: tuple[str, ...]
    http_origins: frozenset[tuple[str, int]]


def _origin(url: str) -> tuple[str, str, int]:
    """Parse an origin without userinfo or ambiguous authorities."""
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return "", "", 0
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.username is not None or not host or "%" in host or not parts.netloc:
            return "", "", 0
        try:
            host = str(ip_address(host))
        except ValueError:
            if not _DNS_NAME.fullmatch(host):
                return "", "", 0
        port = parts.port
        return parts.scheme, host, port if port is not None else (
            443 if parts.scheme == "https" else 80)
    except ValueError:
        return "", "", 0


def network_policy(manifest, environ=None) -> list[str] | NetworkPolicy:
    """Apply host environment grants only to manifests that explicitly opt in."""
    declared = list(getattr(manifest, "network", []) or [])
    if not getattr(manifest, "network_from_env", False):
        return declared
    env = os.environ if environ is None else environ
    key = "ROM_HUB_NETWORK_" + manifest.slug.upper().replace("-", "_")
    hosts = []
    origins = set()
    for entry in env.get(key, "").split(","):
        value = entry.strip()
        if value.startswith("http://"):
            scheme, host, port = _origin(value)
            if scheme != "http" or port == 0:
                continue
            parts = urlsplit(value)
            if parts.path not in ("", "/") or parts.query or parts.fragment or parts.port is None:
                continue
            try:
                address = ip_address(host)
            except ValueError:
                continue
            if any(address in network for network in _LAN):
                origins.add((str(address), port))
            continue
        try:
            hosts.append(str(ip_address(value.strip("[]"))))
        except ValueError:
            bare = value.removeprefix("*.")
            if bare and _DNS_NAME.fullmatch(bare) and all(bare.strip(".").split(".")):
                hosts.append(value.lower())
    return NetworkPolicy(tuple(declared), tuple(hosts), frozenset(origins))


def url_allowed(url: str, patterns: list[str] | NetworkPolicy) -> bool:
    if isinstance(patterns, NetworkPolicy):
        scheme, host, port = _origin(url)
        if scheme == "http":
            return (host, port) in patterns.http_origins
        if scheme == "https" and port > 0:
            return any(host_matches(host, p) for p in patterns.declared + patterns.https_hosts)
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False
    # .hostname strips userinfo and port, which is what defeats
    # https://archive.org@evil.com/
    try:
        host = parts.hostname
    except ValueError:
        return False
    if not host:
        return False
    if not _DNS_NAME.fullmatch(host):
        return False
    return any(host_matches(host, p) for p in patterns)


def check_url(url: str, patterns: list[str] | NetworkPolicy) -> None:
    if not url_allowed(url, patterns):
        if isinstance(patterns, NetworkPolicy):
            raise PolicyViolation("blocked request: destination is outside the plugin's network allowlist")
        raise PolicyViolation(
            f"blocked request to {url!r}: not permitted by manifest "
            f"network allowlist {patterns!r}"
        )
