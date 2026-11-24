"""Source lines from the `sources` config field.

Each line names one shop as `name|url[|protocol][|hauth]`. Credentials ride
in the URL as `https://user:pass@host`. They are split off here and kept on
the `Source` object only, since rom-hub refuses any URL carrying userinfo
and since nothing displayed or logged may contain them.
"""

import base64
import hashlib
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit, urlunsplit

PROTOCOLS = ("auto", "cyberfoil", "tinfoil")

# CyberFoil 1.5.1 reports `Version: 1.5` and `Revision: 1`, and a build
# without the closed Hauth blob sends "0" for both Hauth and Uauth.
CYBERFOIL_VERSION = "1.5"
CYBERFOIL_REVISION = "1"
THEME = "0" * 64


class SourceLineError(ValueError):
    """A source line that cannot be used. The message never holds credentials."""


@dataclass(frozen=True)
class Source:
    name: str
    #: The shop's base URL with userinfo and fragment removed and no
    #: trailing slash. Safe to display, log and send to rom-hub.
    url: str
    protocol: str = "auto"
    hauth: str = ""
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)

    @property
    def origin(self) -> tuple[str, str]:
        return origin_of(self.url)

    @property
    def uid(self) -> str:
        """A stable per-source stand-in for a console's device UID."""
        digest = hashlib.sha256((self.name + self.url).encode("utf-8"))
        return digest.hexdigest().upper()

    def authorization(self) -> str | None:
        if not self.username and not self.password:
            return None
        raw = f"{self.username}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def headers_for(self, url: str) -> dict[str, str]:
        """The CyberFoil identity headers, plus credentials for this origin only.

        CyberFoil sends credentials only to the origin they were written
        for, and an operator's Hauth value is recorded per host, so both
        stay off any other origin an index or a redirect points at.
        """
        same_origin = origin_of(url) == self.origin
        headers = {
            "User-Agent": "cyberfoil",
            "Theme": THEME,
            "Uid": self.uid,
            "Version": CYBERFOIL_VERSION,
            "Revision": CYBERFOIL_REVISION,
            "Language": "en",
            "Hauth": (self.hauth or "0") if same_origin else "0",
            "Uauth": "0",
        }
        auth = self.authorization() if same_origin else None
        if auth:
            headers["Authorization"] = auth
        return headers

    def redact(self, text: str) -> str:
        """Remove this source's credentials from text bound for a message."""
        for secret in (self.authorization(), self.password, self.username):
            if secret:
                text = text.replace(secret, "***")
        return text


def origin_of(url: str) -> tuple[str, str]:
    """Scheme and host:port, lowercased, with userinfo ignored."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ("", "")
    scheme = parts.scheme.lower()
    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, f"{host}:{port}")


def strip_userinfo(url: str) -> str:
    """The URL without `user:pass@` and without a fragment."""
    parts = urlsplit(url)
    netloc = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def parse_source(line: str, number: int) -> Source:
    """Parse one source line. `number` is used only to name it in errors."""
    fields = [part.strip() for part in line.split("|")]
    if len(fields) < 2 or len(fields) > 4:
        raise SourceLineError(f"source line {number} needs the form name|url[|protocol][|hauth]")
    name, raw_url = fields[0], fields[1]
    protocol = (fields[2] if len(fields) > 2 else "").lower() or "auto"
    hauth = fields[3] if len(fields) > 3 else ""
    if not name:
        raise SourceLineError(f"source line {number} has no name")
    label = f"source {name!r}"
    if protocol not in PROTOCOLS:
        raise SourceLineError(
            f"{label} has protocol {protocol!r}; use one of {', '.join(PROTOCOLS)}"
        )
    try:
        parts = urlsplit(raw_url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError:
        raise SourceLineError(f"{label} has a URL that cannot be parsed") from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise SourceLineError(f"{label} needs an http or https URL with a host")
    username = unquote(parts.username or "")
    password = unquote(parts.password or "")
    url = strip_userinfo(raw_url)
    url = url.rstrip("/") if not urlsplit(url).query else url
    if any(ch in hauth for ch in "\r\n\0"):
        raise SourceLineError(f"{label} has a control character in its hauth")
    return Source(
        name=name,
        url=url,
        protocol=protocol,
        hauth=hauth,
        username=username,
        password=password,
    )


def parse_sources(lines) -> tuple[list[Source], list[str]]:
    """Every usable source, and a message for each line that was not usable.

    Blank lines and lines starting with `#` are skipped. A second line with
    a name already used is refused, since `plan()` finds the source again
    by name.
    """
    sources: list[Source] = []
    errors: list[str] = []
    seen: set[str] = set()
    if isinstance(lines, str):
        lines = lines.splitlines()
    for number, line in enumerate(lines or [], start=1):
        if not isinstance(line, str) or not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            source = parse_source(line, number)
        except SourceLineError as exc:
            errors.append(str(exc))
            continue
        if source.name.casefold() in seen:
            errors.append(f"source {source.name!r} is named twice; line {number} is ignored")
            continue
        seen.add(source.name.casefold())
        sources.append(source)
    return sources, errors
