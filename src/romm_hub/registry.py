"""Installed-plugin bookkeeping.

A plugin is a git repo, cloned to <root>/plugins/<slug> and pinned. Updates
are never automatic: re-running install with a new ref is an explicit act,
which is what stops a plugin from silently widening its own permissions.

Pinning records the *resolved commit*, not just the ref that was asked for.
Branches and tags are mutable — a tag can be force-moved after you install —
so `ref` alone cannot tell you whether the code on disk is the code you
approved. `commit` can, and a bare SHA is accepted as a ref for that reason.

`source` and `ref` are attacker-controlled in the general case (an install
string copy-pasted from a forum, or a value taken from a plugin catalog), and
git parses options anywhere before a `--` separator — `--upload-pack=<cmd>` is
run through a shell. Both are therefore validated against an allowlist before
any git process is spawned, and every positional argument is passed after `--`.
"""

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest, ManifestError, load_manifest

# A source is one of: https URL, ssh URL, scp-style git@host:path, or a local
# path that already exists. Everything else — most importantly anything
# starting with '-', and the `ext::` transport, which is remote code execution
# by design — is refused.
_HTTPS_SOURCE_RE = re.compile(r"\Ahttps://[^\s]+\Z")
_SSH_SOURCE_RE = re.compile(r"\Assh://[^\s]+\Z")
_SCP_SOURCE_RE = re.compile(r"\A[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s]+\Z")

# Refs are branch names, tag names, or commit SHAs. Deliberately narrower than
# git's own rules: no leading '-', no whitespace, no '..', no shell characters.
_REF_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/+-]*\Z")

_SOURCE_HELP = (
    "must be an https:// URL, an ssh:// or git@host:path URL, or an existing "
    "local path"
)


class RegistryError(Exception):
    """Install, lookup, or state persistence failed."""


def _checked_source(source: str) -> str:
    """Return `source` unchanged, or raise if git could misread it as an option."""
    if not isinstance(source, str) or not source.strip():
        raise RegistryError("install source is empty")
    if source.startswith("-"):
        raise RegistryError(
            f"refusing install source {source!r}: a source starting with '-' would "
            f"be parsed by git as an option, not a repository ({_SOURCE_HELP})"
        )
    if "::" in source.split("/")[0]:
        raise RegistryError(
            f"refusing install source {source!r}: git remote-helper transports "
            f"such as 'ext::' execute arbitrary commands ({_SOURCE_HELP})"
        )
    if _HTTPS_SOURCE_RE.match(source) or _SSH_SOURCE_RE.match(source):
        return source
    if _SCP_SOURCE_RE.match(source):
        return source
    if Path(source).exists():
        return source
    raise RegistryError(f"refusing install source {source!r}: {_SOURCE_HELP}")


def _checked_ref(ref: str) -> str:
    """Return `ref` unchanged, or raise if git could misread it as an option."""
    if not isinstance(ref, str) or not ref.strip():
        raise RegistryError("install ref is empty")
    if not _REF_RE.match(ref) or ".." in ref:
        raise RegistryError(
            f"refusing install ref {ref!r}: a ref must be a branch name, a tag "
            f"name, or a commit SHA (letters, digits, '.', '_', '/', '+', '-', "
            f"not starting with '-')"
        )
    return ref


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    # protocol.ext.allow=never is belt-and-braces: _checked_source already
    # refuses 'ext::', but a redirect or an insteadOf rule could reintroduce it.
    return subprocess.run(
        ["git", "-c", "protocol.ext.allow=never", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@dataclass
class InstalledPlugin:
    slug: str
    path: Path
    manifest: Manifest
    enabled: bool
    config: dict
    commit: str | None = None


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

    def _fetch(self, source: str, ref: str | None, staging: Path) -> None:
        """Populate `staging` with the tree at `ref` (or the default branch)."""
        if ref is None:
            result = _git(["clone", "--quiet", "--depth", "1", "--", source, str(staging)])
            if result.returncode != 0:
                raise RegistryError(
                    f"git clone of {source!r} failed: {result.stderr.strip()}"
                )
            return

        # `clone --branch` cannot express a commit SHA, and `--depth 1` cannot
        # fetch an arbitrary one, so a ref is fetched explicitly instead. The
        # shallow attempt covers branches, tags, and (on servers that advertise
        # unadvertised objects) SHAs; the full fetch is the fallback for the rest.
        staging.mkdir(parents=True, exist_ok=True)
        init = _git(["init", "--quiet"], cwd=staging)
        if init.returncode != 0:
            raise RegistryError(f"git init failed: {init.stderr.strip()}")

        target = "FETCH_HEAD"
        for args in (
            ["fetch", "--quiet", "--depth", "1", "--", source, ref],
            ["fetch", "--quiet", "--", source, ref],
        ):
            fetched = _git(args, cwd=staging)
            if fetched.returncode == 0:
                break
        else:
            # Some servers refuse a request for an object they did not
            # advertise. They will still serve the full history, from which a
            # reachable commit can be checked out by name.
            fetched = _git(
                ["fetch", "--quiet", "--tags", "--", source,
                 "+refs/heads/*:refs/remotes/origin/*"],
                cwd=staging,
            )
            if fetched.returncode != 0:
                raise RegistryError(
                    f"git fetch of {ref!r} from {source!r} failed: "
                    f"{fetched.stderr.strip()}"
                )
            target = ref

        checkout = _git(["checkout", "--quiet", "--detach", target], cwd=staging)
        if checkout.returncode != 0:
            raise RegistryError(
                f"git checkout of {ref!r} from {source!r} failed: "
                f"{checkout.stderr.strip()}"
            )

    def install(self, source: str, ref: str | None = None) -> InstalledPlugin:
        source = _checked_source(source)
        ref = _checked_ref(ref) if ref is not None else None

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "clone"
            self._fetch(source, ref, staging)

            resolved = _git(["rev-parse", "HEAD"], cwd=staging)
            if resolved.returncode != 0:
                raise RegistryError(
                    f"cannot resolve the installed commit of {source!r}: "
                    f"{resolved.stderr.strip()}"
                )
            commit = resolved.stdout.strip()

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
            "commit": commit,
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
            commit=entry.get("commit"),
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
