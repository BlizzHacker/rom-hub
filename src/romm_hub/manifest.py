"""Parsing and validation of a plugin's manifest.toml.

A manifest is the plugin's declaration of what it needs. Because the broker
enforces those declarations, a permissive parser here would quietly weaken
the whole security model — so everything unknown is rejected.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_CAPABILITIES = frozenset({"search", "importer", "metadata", "stream", "cores"})
RESERVED_CAPABILITIES = frozenset({"peer", "netplay"})
SUPPORTED_CONFIG_TYPES = frozenset({"str", "int", "bool", "list[str]"})
RESERVED_CONFIG_TYPES = frozenset({"secret"})

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class ManifestError(Exception):
    """Raised when a manifest is malformed, unsupported, or unsafe."""


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

    rpp_version = str(plugin.get("rpp_version", ""))
    if rpp_version != "1":
        raise ManifestError(
            f"unsupported rpp_version {rpp_version!r}: this host implements RPP v1"
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
                "in RPP v1 but not implemented in Phase 1"
            )
        if declared not in SUPPORTED_CONFIG_TYPES:
            raise ManifestError(f"config field {key!r} has unknown type {declared!r}")

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
    )


def load_manifest(path: Path) -> Manifest:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest at {path}: {exc}") from exc
    return parse_manifest(text)
