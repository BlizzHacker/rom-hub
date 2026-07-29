"""Installed-plugin bookkeeping.

A plugin is a git repo, cloned to <root>/plugins/<slug> and pinned. Updates
are never automatic: re-running install with a new ref is an explicit act,
which is what stops a plugin from silently widening its own permissions.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest, ManifestError, load_manifest


class RegistryError(Exception):
    """Install, lookup, or state persistence failed."""


@dataclass
class InstalledPlugin:
    slug: str
    path: Path
    manifest: Manifest
    enabled: bool
    config: dict


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.plugins_dir = self.root / "plugins"
        self.state_path = self.root / "state.json"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    def _read_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot read {self.state_path}: {exc}") from exc

    def _write_state(self, state: dict) -> None:
        self.state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )

    def install(self, source: str, ref: str | None = None) -> InstalledPlugin:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "clone"
            cmd = ["git", "clone", "--quiet", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [source, str(staging)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RegistryError(
                    f"git clone of {source!r} failed: {result.stderr.strip()}"
                )

            try:
                manifest = load_manifest(staging / "manifest.toml")
            except ManifestError as exc:
                raise RegistryError(f"{source}: {exc}") from exc

            target = self.plugins_dir / manifest.slug
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staging, target)

        state = self._read_state()
        entry = state.get(manifest.slug, {})
        defaults = {
            key: spec["default"]
            for key, spec in manifest.config_schema.items()
            if "default" in spec
        }
        state[manifest.slug] = {
            "enabled": entry.get("enabled", True),
            "config": entry.get("config", defaults),
            "source": source,
            "ref": ref,
        }
        self._write_state(state)
        return self.get(manifest.slug)

    def get(self, slug: str) -> InstalledPlugin:
        state = self._read_state()
        if slug not in state:
            raise RegistryError(f"plugin {slug!r} is not installed")
        path = self.plugins_dir / slug
        try:
            manifest = load_manifest(path / "manifest.toml")
        except ManifestError as exc:
            raise RegistryError(f"plugin {slug!r}: {exc}") from exc
        entry = state[slug]
        return InstalledPlugin(
            slug=slug,
            path=path,
            manifest=manifest,
            enabled=entry.get("enabled", True),
            config=entry.get("config", {}),
        )

    def installed(self) -> list[InstalledPlugin]:
        return [self.get(slug) for slug in sorted(self._read_state())]

    def set_enabled(self, slug: str, enabled: bool) -> None:
        state = self._read_state()
        if slug not in state:
            raise RegistryError(f"plugin {slug!r} is not installed")
        state[slug]["enabled"] = enabled
        self._write_state(state)

    def set_config(self, slug: str, config: dict) -> None:
        state = self._read_state()
        if slug not in state:
            raise RegistryError(f"plugin {slug!r} is not installed")
        state[slug]["config"] = config
        self._write_state(state)
