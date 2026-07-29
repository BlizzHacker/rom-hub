"""The plugin directory: a list of known sources, in the qBittorrent mould.

qBittorrent's unofficial search-plugin wiki is the model — a community-kept
table of who publishes what, with a direct link to install from. This is the
same idea, made machine-readable so the CLI can list and install by slug
instead of asking people to copy raw URLs by hand.

**The catalog holds no authority.** It says where a plugin lives; it does not
say what a plugin may do. An installed plugin's network allowlist comes from
its own manifest.toml, read at install time by the registry and enforced by the
broker. If the catalog could grant permissions, whoever hosts it could silently
widen every plugin's reach — so the `network` field here is advisory, shown to
a human deciding whether to install, and never consulted at runtime.
"""

import json
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_CATALOG_VERSION = "1"

# Mirrors qBittorrent's convention. ASCII fallbacks exist because a Windows
# console defaults to cp1252, which cannot encode these at all -- printing them
# raises UnicodeEncodeError and takes the whole command down, so the status
# column would break `browse` on the platform this was developed on.
STATUS_SYMBOLS = {"ok": "✔", "caveat": "❗", "broken": "✖"}
STATUS_ASCII = {"ok": "ok", "caveat": "!", "broken": "x"}


def symbol_for(status: str, encoding: str | None) -> str:
    """The status marker, degraded to ASCII when the terminal cannot show it."""
    symbol = STATUS_SYMBOLS[status]
    try:
        symbol.encode(encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return STATUS_ASCII[status]
    return symbol

_REQUIRED_FIELDS = (
    "slug", "name", "author", "repository", "install", "download",
    "version", "ref", "updated", "rpp_version", "capabilities",
    "network", "status", "comments",
)


class CatalogError(Exception):
    """The catalog is malformed, unsupported, or unsafe to act on."""


@dataclass(frozen=True)
class CatalogEntry:
    slug: str
    name: str
    author: str
    repository: str
    install: str
    download: str
    version: str
    ref: str
    updated: str
    rpp_version: str
    capabilities: list[str]
    network: list[str]
    status: str
    comments: str

    @property
    def symbol(self) -> str:
        return STATUS_SYMBOLS[self.status]


def _check_https(entry: dict, field: str) -> None:
    value = entry.get(field, "")
    if not isinstance(value, str) or not value.startswith("https://"):
        raise CatalogError(
            f"{entry.get('slug', '?')}: {field} must be an https URL, got {value!r}"
        )


def parse_catalog(raw: dict) -> list[CatalogEntry]:
    if not isinstance(raw, dict):
        raise CatalogError("catalog must be a JSON object")

    version = str(raw.get("catalog_version", ""))
    if version != SUPPORTED_CATALOG_VERSION:
        raise CatalogError(
            f"unsupported catalog_version {version!r}: this build reads v"
            f"{SUPPORTED_CATALOG_VERSION}"
        )

    plugins = raw.get("plugins")
    if not isinstance(plugins, list):
        raise CatalogError("catalog must contain a plugins array")

    entries: list[CatalogEntry] = []
    seen: set[str] = set()
    for item in plugins:
        if not isinstance(item, dict):
            raise CatalogError("each catalog entry must be an object")
        missing = [f for f in _REQUIRED_FIELDS if f not in item]
        if missing:
            raise CatalogError(
                f"{item.get('slug', '?')}: entry is missing {', '.join(missing)}"
            )

        slug = item["slug"]
        if slug in seen:
            raise CatalogError(f"duplicate slug {slug!r} in catalog")
        seen.add(slug)

        if item["status"] not in STATUS_SYMBOLS:
            raise CatalogError(
                f"{slug}: unknown status {item['status']!r}; expected one of "
                f"{sorted(STATUS_SYMBOLS)}"
            )
        if str(item["rpp_version"]) != "1":
            raise CatalogError(
                f"{slug}: rpp_version {item['rpp_version']!r} is not readable by "
                "this host"
            )

        for field in ("repository", "install", "download"):
            _check_https(item, field)

        # A download must name the exact tag it ships. Pointing at a branch is
        # how a directory silently hands somebody new code on a later install.
        if item["ref"] not in item["download"]:
            raise CatalogError(
                f"{slug}: download URL must be pinned to ref {item['ref']!r}"
            )

        entries.append(CatalogEntry(**{f: item[f] for f in _REQUIRED_FIELDS}))
    return entries


def load_catalog(path: Path) -> list[CatalogEntry]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read catalog at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog at {path} is not valid JSON: {exc}") from exc
    return parse_catalog(raw)


def render_markdown(entries: list[CatalogEntry]) -> str:
    """Render the directory table, using qBittorrent's column names."""
    lines = [
        "| Source | Author (Repository) | Version | Last update | Install | Capabilities | Network | Comments |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in sorted(entries, key=lambda x: x.name.lower()):
        network = ", ".join(f"`{h}`" for h in e.network) or "_none_"
        caps = ", ".join(f"`{c}`" for c in e.capabilities)
        lines.append(
            f"| {e.symbol} [{e.name}]({e.repository}) "
            f"| {e.author} ([repo]({e.repository})) "
            f"| {e.version} "
            f"| {e.updated} "
            f"| [`{e.ref}` tarball]({e.download}) "
            f"| {caps} "
            f"| {network} "
            f"| {e.comments} |"
        )
    return "\n".join(lines)
