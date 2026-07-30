"""Parsing and validation of a plugin's manifest.toml.

A manifest is the plugin's declaration of what it needs. Because the broker
enforces those declarations, a permissive parser here would quietly weaken
the whole security model — so everything unknown is rejected.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .netpolicy import url_allowed
from .types import bare_filename

KNOWN_CAPABILITIES = frozenset({"search", "importer", "metadata", "stream", "cores"})
RESERVED_CAPABILITIES = frozenset({"peer", "netplay"})
# `secret` moved here from RESERVED_CONFIG_TYPES when the store behind it
# landed (see `rom_hub.secrets`). It is a `str` to the plugin, which still
# receives the value at call time because it needs it to make its request;
# what changes is where the Hub keeps it and what the Hub prints.
SUPPORTED_CONFIG_TYPES = frozenset({"str", "int", "bool", "list[str]", "secret"})

# Nothing is reserved-but-unimplemented any more. Kept as a name because the
# check below is the shape a future reservation goes back into, and because
# an empty allowlist that still gets consulted is cheaper to re-fill than a
# deleted one is to reconstruct.
RESERVED_CONFIG_TYPES: frozenset[str] = frozenset()

# A `secret` field's value never comes from the manifest. `default` on any
# other type is a convenience; on a secret it would be a credential written
# into a file that ships in a public plugin repo.
_SECRET_TYPE = "secret"

# -- data assets ---------------------------------------------------------
#
# A `metadata` source is sometimes a *file* rather than a service: OpenVGDB
# publishes one SQLite database and no API at all. RPP v1 gave a plugin no
# way to obtain one — `ctx.http` caps a response at 4 MiB, carries text
# rather than bytes, and follows no redirect, and a per-command subprocess
# has nowhere to cache anything between invocations. So the plugin
# *declares* what it needs and the host fetches it.
#
# Declaration rather than a runtime request, deliberately. A URL in a
# manifest is reviewable before install, diffable on update, and printable
# by `rom-hub plugin install`; `ctx.download(url)` would be none of those,
# and would hand a plugin a way to pull arbitrary megabytes at a moment
# nobody is watching.

# Only "zip" in v1. Every additional archive format is another parser
# reading hostile bytes, and one covers the case that exists.
KNOWN_ARCHIVE_FORMATS = frozenset({"zip"})

# The host walks every asset a manifest declares, so the list is bounded
# like `MAX_FILES_PER_PLAN` and for the same reason.
MAX_DATA_ASSETS = 8

# A SEPARATE budget from `broker.fetcher.MAX_RESPONSE_BYTES` (4 MiB), and
# deliberately much larger. The two bound different things for different
# reasons: `ctx.http`'s cap exists because that body is buffered in host
# memory and then JSON-escaped into a reply frame, so it must stay under
# `protocol.MAX_MESSAGE_CHARS`. A data asset never enters either — it is
# streamed to disk and the plugin is handed a *path*. What is left to
# bound is the operator's disk and patience, which is a much higher number.
# Raising the `ctx.http` cap to cover this case would have made every
# plugin response 128 MiB-shaped; they are not the same limit.
MAX_DATA_ASSET_BYTES = 128 * 1024 * 1024

_MAX_ASSET_DESCRIPTION_CHARS = 300
_SHA256_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")
_ASSET_KEYS = frozenset(
    {"name", "url", "sha256", "size_bytes", "archive", "member", "description"}
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class ManifestError(Exception):
    """Raised when a manifest is malformed, unsupported, or unsafe."""


@dataclass(frozen=True)
class DataAsset:
    """One dataset a plugin declares and the host fetches on its behalf.

    `sha256` is the digest of **the file the plugin opens** — the extracted
    member when `archive` is set, the downloaded bytes when it is not. It
    is not optional, and there is no "trust on first use" mode: a 9 MB blob
    pulled over the network and handed to code that trusts it is a supply
    chain the operator never agreed to. The host verifies before the plugin
    is told the path, and re-verifies a cached copy rather than assuming it.

    `size_bytes` describes the *download*, not the unpacked file, because
    its job is to let the host say "this will pull 8.7 MiB from
    github.com" before the request goes out.
    """

    name: str
    url: str
    sha256: str
    size_bytes: int | None = None
    archive: str | None = None
    member: str | None = None
    description: str = ""

    @property
    def host(self) -> str:
        """The host the download starts at. Not where it may end up — the
        downloader re-checks every redirect hop against the allowlist."""
        return urlsplit(self.url).hostname or ""


@dataclass(frozen=True)
class Manifest:
    slug: str
    name: str
    version: str
    rpp_version: str
    license: str | None
    capabilities: dict[str, str]
    network: list[str]
    romm_api: list[str]
    config_schema: dict = field(default_factory=dict)
    data_assets: tuple[DataAsset, ...] = ()


def parse_manifest(text: str) -> Manifest:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"manifest is not valid TOML: {exc}") from exc

    plugin = raw.get("plugin")
    if not isinstance(plugin, dict):
        raise ManifestError("manifest is missing a [plugin] table")

    slug = plugin.get("slug", "")
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ManifestError(
            f"invalid slug {slug!r}: must be lowercase alphanumeric with hyphens"
        )

    # Not str(): the spec says exactly the string "1", and coercing would
    # accept a TOML integer 1. This file drives an allowlist, so "everything
    # unknown is rejected" includes the wrong type for a known key.
    rpp_version = plugin.get("rpp_version", "")
    if not isinstance(rpp_version, str) or rpp_version != "1":
        raise ManifestError(
            f"unsupported rpp_version {rpp_version!r}: this host implements "
            f'RPP v1, declared as the string "1"'
        )

    for required in ("name", "version"):
        if not plugin.get(required):
            raise ManifestError(f"[plugin] is missing {required}")

    capabilities = raw.get("capabilities") or {}
    if not isinstance(capabilities, dict) or not capabilities:
        raise ManifestError("manifest must declare at least one capability")

    for cap, entrypoint in capabilities.items():
        if cap in RESERVED_CAPABILITIES:
            raise ManifestError(
                f"capability {cap!r} is reserved for a future RPP version"
            )
        if cap not in KNOWN_CAPABILITIES:
            raise ManifestError(f"unknown capability {cap!r}")
        if not isinstance(entrypoint, str) or not _ENTRYPOINT_RE.match(entrypoint):
            raise ManifestError(
                f"capability {cap!r} entrypoint {entrypoint!r} must be 'module:Class'"
            )

    permissions = raw.get("permissions") or {}
    network = permissions.get("network") or []
    romm_api = permissions.get("romm_api") or []
    if not isinstance(network, list) or not all(isinstance(h, str) for h in network):
        raise ManifestError("permissions.network must be a list of host patterns")
    if not isinstance(romm_api, list) or not all(isinstance(s, str) for s in romm_api):
        raise ManifestError("permissions.romm_api must be a list of scope strings")

    config_schema = raw.get("config") or {}
    if not isinstance(config_schema, dict):
        raise ManifestError("[config] must be a table")
    for key, spec in config_schema.items():
        if not isinstance(spec, dict) or "type" not in spec:
            raise ManifestError(f"config field {key!r} must declare a type")
        declared = spec["type"]
        if declared in RESERVED_CONFIG_TYPES:
            raise ManifestError(
                f"config field {key!r} uses type {declared!r}, which is reserved "
                "for a future RPP version but not implemented here"
            )
        if declared not in SUPPORTED_CONFIG_TYPES:
            raise ManifestError(f"config field {key!r} has unknown type {declared!r}")
        if declared == _SECRET_TYPE and "default" in spec:
            raise ManifestError(
                f"config field {key!r} is a secret and must not declare a "
                f"default: a manifest is a public file in a git repo, so a "
                f"default value there is a credential published on purpose. "
                f"An unset secret is simply absent, and your plugin should "
                f"refuse with its own message when it sees one."
            )

    return Manifest(
        slug=slug,
        name=plugin["name"],
        version=str(plugin["version"]),
        rpp_version=rpp_version,
        license=plugin.get("license"),
        capabilities=dict(capabilities),
        network=list(network),
        romm_api=list(romm_api),
        config_schema=config_schema,
        data_assets=_parse_data_assets(raw.get("data_assets"), list(network)),
    )


def _parse_data_assets(raw, network: list[str]) -> tuple[DataAsset, ...]:
    """Validate `[[data_assets]]`, or return () when there are none.

    The URL is checked against this manifest's own `permissions.network`
    here, at parse time, as well as by the downloader at fetch time. Both
    matter and neither replaces the other: this one makes an asset on an
    undeclared host a manifest the Hub refuses to *install*, so the
    `network` list a reviewer reads is a complete account of where this
    plugin causes traffic. The fetch-time check is what actually holds,
    including across every redirect hop.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ManifestError(
            "[[data_assets]] must be an array of tables (write [[data_assets]], "
            "not [data_assets])"
        )
    if len(raw) > MAX_DATA_ASSETS:
        raise ManifestError(
            f"a plugin may declare at most {MAX_DATA_ASSETS} data assets, "
            f"got {len(raw)}"
        )

    assets: list[DataAsset] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ManifestError(f"data asset {index} must be a table")
        unknown = sorted(set(entry) - _ASSET_KEYS)
        if unknown:
            raise ManifestError(
                f"data asset {index} has unknown key(s) {unknown}; permitted: "
                f"{sorted(_ASSET_KEYS)}"
            )
        asset = _parse_one_asset(index, entry, network)
        # Case-insensitively, because the cache directory may be on a
        # filesystem that cannot tell "DB.sqlite" from "db.sqlite" and two
        # assets that collide there would overwrite each other's verified
        # bytes.
        if asset.name.casefold() in seen:
            raise ManifestError(
                f"data asset {asset.name!r} is declared more than once; each "
                f"asset needs a distinct name (they share one directory)"
            )
        seen.add(asset.name.casefold())
        assets.append(asset)
    return tuple(assets)


def _parse_one_asset(index: int, entry: dict, network: list[str]) -> DataAsset:
    label = f"data asset {index}"

    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{label} is missing a name")
    try:
        # The same validator a FetchPlan filename goes through. This name
        # becomes a file in the plugin's data directory, so it is exactly
        # as privileged as a name in a plan and gets exactly the same rule
        # rather than a second, slightly different one.
        name = bare_filename(name)
    except ValueError as exc:
        raise ManifestError(f"{label} name {entry['name']!r}: {exc}") from exc
    label = f"data asset {name!r}"

    url = entry.get("url")
    if not isinstance(url, str) or not url:
        raise ManifestError(f"{label} is missing a url")
    if not url_allowed(url, network):
        raise ManifestError(
            f"{label} url {url!r} is not permitted by this manifest's own "
            f"permissions.network {network!r}. An asset URL is gated exactly "
            f"like a FetchPlan URL, so the host it starts at must be declared "
            f"— and so must every host it redirects to."
        )

    sha256 = entry.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise ManifestError(
            f"{label} needs a sha256 of exactly 64 hex characters (got "
            f"{sha256!r}). Integrity is not optional: the host verifies the "
            f"bytes before a plugin is told where they are."
        )

    size_bytes = entry.get("size_bytes")
    if size_bytes is not None:
        # bool is an int in Python; a `size_bytes = true` must not become 1.
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
            raise ManifestError(f"{label} size_bytes must be an integer")
        if size_bytes < 1 or size_bytes > MAX_DATA_ASSET_BYTES:
            raise ManifestError(
                f"{label} size_bytes {size_bytes} is outside 1.."
                f"{MAX_DATA_ASSET_BYTES}"
            )

    archive = entry.get("archive")
    member = entry.get("member")
    if archive is not None:
        if archive not in KNOWN_ARCHIVE_FORMATS:
            raise ManifestError(
                f"{label} archive {archive!r} is not supported; RPP v1 knows "
                f"{sorted(KNOWN_ARCHIVE_FORMATS)}"
            )
        if not isinstance(member, str) or not member:
            raise ManifestError(
                f"{label} declares archive = {archive!r} but no member; name "
                f"the single file inside it that the plugin opens"
            )
        try:
            member = bare_filename(member)
        except ValueError as exc:
            raise ManifestError(f"{label} member {entry['member']!r}: {exc}") from exc
    elif member is not None:
        raise ManifestError(
            f"{label} declares a member but no archive; member only means "
            f"something inside an archive"
        )

    description = entry.get("description", "")
    if not isinstance(description, str):
        raise ManifestError(f"{label} description must be a string")
    if len(description) > _MAX_ASSET_DESCRIPTION_CHARS:
        raise ManifestError(
            f"{label} description must be at most "
            f"{_MAX_ASSET_DESCRIPTION_CHARS} characters"
        )

    return DataAsset(
        name=name,
        url=url,
        sha256=sha256.lower(),
        size_bytes=size_bytes,
        archive=archive,
        member=member,
        description=description,
    )


def load_manifest(path: Path) -> Manifest:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest at {path}: {exc}") from exc
    return parse_manifest(text)
