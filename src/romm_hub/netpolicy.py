"""Allowlist enforcement for plugin-initiated HTTP.

Every plugin request passes through check_url() before a socket is opened.
If this module is wrong, the manifest's `network` declaration is decoration.
"""

import re
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


def url_allowed(url: str, patterns: list[str]) -> bool:
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


def check_url(url: str, patterns: list[str]) -> None:
    if not url_allowed(url, patterns):
        raise PolicyViolation(
            f"blocked request to {url!r}: not permitted by manifest "
            f"network allowlist {patterns!r}"
        )
