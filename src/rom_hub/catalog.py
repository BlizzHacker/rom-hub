"""The plugin directory: a community-kept list of known sources.

A table of who publishes what, with a direct link to install from, made
machine-readable so the CLI can list and install by slug instead of asking
people to copy raw URLs by hand.

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

# ASCII fallbacks exist because a Windows
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
    "network", "status", "description", "terms", "search_only",
    "key_required", "in_tree", "comments",
)

# Fields a reader needs in plain language before deciding to install.
# `terms` is the licensing/terms position of the *source* -- not of the
# plugin's own code, which is its LICENSE file. A directory that lists where
# to get ROMs and says nothing about whether they may lawfully be got is
# doing half the job.
_REQUIRED_TEXT = ("description", "terms")
_REQUIRED_FLAGS = ("search_only", "key_required", "in_tree")


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
    description: str
    terms: str
    search_only: bool
    key_required: bool
    in_tree: bool
    comments: str

    @property
    def symbol(self) -> str:
        return STATUS_SYMBOLS[self.status]

    @property
    def flags(self) -> list[str]:
        """The things a reader must know before installing, not after.

        Both of these have burned somebody: a plugin whose importer always
        refuses looks broken when it does, and a plugin needing an API key
        looks broken when it returns nothing. Saying so in the directory is
        cheaper than the bug report.

        The `search_only` field is rendered as "cannot import" rather than
        "search-only" because those stopped meaning the same thing: itch-io
        implements `metadata` and still cannot import anything, so the
        category name would be wrong where the behaviour it stands for is
        still exactly right.
        """
        out = []
        if self.search_only:
            out.append("**cannot import** (every import is refused)")
        if self.key_required:
            out.append("**API key required** (stored in clear text)")
        return out


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

        for field in _REQUIRED_FLAGS:
            if not isinstance(item[field], bool):
                raise CatalogError(
                    f"{slug}: {field} must be true or false, got {item[field]!r}"
                )

        # An empty string here is worse than a missing key: it renders as a
        # blank cell that reads like "nothing to declare" rather than like
        # "nobody filled this in".
        for field in _REQUIRED_TEXT:
            if not isinstance(item[field], str) or not item[field].strip():
                raise CatalogError(f"{slug}: {field} must be a non-empty string")

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


def _install_cell(e: CatalogEntry) -> str:
    """What to actually type to install this, which is not always a link.

    A plugin that ships in-tree has no published tarball. Rendering the
    pinned-`download` link anyway would put a URL on the page that does not
    resolve -- the one thing a directory must never do, because a reader
    cannot tell a typo from a supply-chain swap. So an in-tree plugin shows
    the path that works and says what it is.
    """
    if e.in_tree:
        return f"`./plugins-dev/{e.slug}` (in-tree)"
    return f"[`{e.ref}` tarball]({e.download})"


def _repo_cell(e: CatalogEntry) -> str:
    if e.in_tree:
        return "in-tree, no public repo yet"
    return f"[repo]({e.repository})"


def render_markdown(entries: list[CatalogEntry]) -> str:
    """Render the directory: a scannable table, then the per-plugin detail.

    The table carries what you compare across plugins; the sections carry
    what you have to read about one. Licensing does not fit in a table cell
    and gets skipped when it is squeezed into one, so it gets prose.
    """
    ordered = sorted(entries, key=lambda x: x.name.lower())
    lines = [
        "| Source | Author (Repository) | Version | Last update | Install "
        "| Capabilities | Flags | Network |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in ordered:
        network = ", ".join(f"`{h}`" for h in e.network) or "_none_"
        caps = ", ".join(f"`{c}`" for c in e.capabilities)
        name = e.name if e.in_tree else f"[{e.name}]({e.repository})"
        lines.append(
            f"| {e.symbol} {name} "
            f"| {e.author} ({_repo_cell(e)}) "
            f"| {e.version} "
            f"| {e.updated} "
            f"| {_install_cell(e)} "
            f"| {caps} "
            f"| {'<br>'.join(e.flags) or '—'} "
            f"| {network} |"
        )

    for e in ordered:
        lines += ["", f"### {e.symbol} {e.name} — `{e.slug}`", ""]
        if e.flags:
            lines += [f"> {' · '.join(e.flags)}", ""]
        lines += [
            f"{e.description}",
            "",
            f"**Source terms.** {e.terms}",
            "",
            f"**Comments.** {e.comments}",
            "",
            f"**Network requested.** "
            f"{', '.join(f'`{h}`' for h in e.network) or '_none_'} — declared in "
            f"this plugin's own `manifest.toml`, which is what the broker "
            f"enforces. The line above is a copy for reading, not the thing "
            f"that grants it.",
        ]
    return "\n".join(lines)
