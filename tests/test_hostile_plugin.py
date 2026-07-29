"""End-to-end confinement test against a deliberately hostile plugin.

This is the test finding I9 said was missing: the suite asserted the broker
path was enforced, but nothing asserted the broker was the *only* path. Finding
C1 then demonstrated it was not — a plugin declaring only `allowed.example`
opened a raw socket to an undeclared host while the broker's fetcher recorded
zero calls.

This test runs that same escape through the real PluginProcess. If it starts
failing, the containment claim in DESIGN.md and README.md has regressed and
those documents are lying again.
"""

import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from romm_hub.broker.host import (
    FORCED_ENV,
    SAFE_ENV_VARS,
    PluginProcess,
    plugin_environment,
)
from romm_hub.manifest import parse_manifest

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="seccomp confinement is Linux-only"
)

MANIFEST = """
[plugin]
slug = "hostile"
name = "Hostile"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "hostile_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

HOSTILE = textwrap.dedent(
    """
    from romm_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            findings = []

            # Raw socket to a host the manifest never declared.
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect(("1.1.1.1", 53))
                findings.append("SOCKET:ESCAPED")
                s.close()
            except PermissionError:
                findings.append("SOCKET:BLOCKED")
            except Exception as e:
                findings.append("SOCKET:OTHER(%s)" % type(e).__name__)

            # Spawn a child process.
            try:
                import subprocess
                subprocess.run(["/bin/echo", "x"], capture_output=True, timeout=5)
                findings.append("EXEC:ESCAPED")
            except PermissionError:
                findings.append("EXEC:BLOCKED")
            except Exception as e:
                findings.append("EXEC:OTHER(%s)" % type(e).__name__)

            return [SearchResult(source_id="x", title=" ".join(findings))]
    """
)


SNOOPER_MANIFEST = MANIFEST.replace("hostile_plugin:Search", "snooper_plugin:Search")

# Reports the plugin's ENTIRE environment back to the host, so the test can
# assert on what is there rather than only on the names it thought to ask
# about. A name-based check alone would pass a regression that reinstated
# inheritance of everything except the handful of names it happened to list.
SNOOPER = textwrap.dedent(
    """
    import os

    from romm_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            return [SearchResult(
                source_id="x",
                title="env",
                extra={"names": ",".join(sorted(os.environ))},
            )]
    """
)


def _plugin_environment_names(monkeypatch) -> set[str]:
    """Every variable name a real plugin subprocess can actually see."""
    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp)
        (plugin_dir / "snooper_plugin.py").write_text(SNOOPER, encoding="utf-8")
        proc = PluginProcess(
            plugin_dir=plugin_dir,
            manifest=parse_manifest(SNOOPER_MANIFEST),
            config={},
            fetcher=RecordingFetcher(),
            timeout=60.0,
            # The scrub is not a sandbox feature and must hold with or
            # without confinement, so this runs everywhere.
            allow_unsandboxed=True,
        )
        with proc:
            raw = proc.search("q", None, 5)[0].extra["names"]
    return {name for name in raw.split(",") if name}


class RecordingFetcher:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str, params: dict) -> tuple[int, str]:
        self.calls.append(url)
        return 200, "should-never-be-called"


@linux_only
def test_hostile_plugin_cannot_escape_the_broker():
    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp)
        (plugin_dir / "hostile_plugin.py").write_text(HOSTILE, encoding="utf-8")

        fetcher = RecordingFetcher()
        proc = PluginProcess(
            plugin_dir=plugin_dir,
            manifest=parse_manifest(MANIFEST),
            config={},
            fetcher=fetcher,
            timeout=60.0,
            allow_unsandboxed=False,
        )
        with proc:
            assert proc.sandboxed is True, proc.sandbox_reason
            verdict = proc.search("q", None, 5)[0].title

        assert "SOCKET:BLOCKED" in verdict, f"network escape is open: {verdict}"
        assert "EXEC:BLOCKED" in verdict, f"process spawn is open: {verdict}"
        assert fetcher.calls == [], "hostile plugin should never reach the fetcher"


# Names seeded into the parent for the leak tests. ROMM_PASSWORD is the one
# Phase 2 introduced; the rest are here because the real failure was never
# about RomM specifically -- the workstation this was found on had a GitHub
# token and a DeepSeek API key sitting in the same environment. CANARY has no
# recognisable shape at all, which is the point: a name-based filter cannot
# know about it, and an allowlist does not need to.
SECRETS_IN_THE_PARENT = {
    "ROMM_PASSWORD": "correct-horse-battery-staple",
    "ROMM_USER": "admin",
    "ROMM_URL": "http://romm.invalid:8080",
    "GITHUB_TOKEN": "ghp_deadbeefdeadbeefdeadbeef",
    "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_alsoasecret",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI-not-a-real-key",
    "DEEPSEEK_API_KEY": "sk-not-a-real-key",
    "ROMM_HUB_SECRET_CANARY": "if-a-plugin-can-read-this-the-allowlist-is-gone",
}

# Names CPython sets on ITSELF at startup, which therefore appear in a
# child's os.environ no matter how empty the environment handed to it was.
#
# LC_CTYPE is PEP 538 locale coercion: where the inherited locale is C or
# POSIX -- as in the python:3.12-slim container this deploys to -- CPython
# coerces to C.UTF-8 and writes LC_CTYPE into its own environment so its
# own children inherit the coercion. Verified on the deployment target that
# `Popen(..., env={"PATH": ...})` still yields LC_CTYPE in the child while
# the parent has it unset, so it carries nothing from the parent and is not
# a channel. Listed rather than tolerated generically: anything appearing
# here that is NOT on this list is a real leak and must fail.
_INTERPRETER_INJECTED = {"LC_CTYPE"}

# What a plugin may legitimately end up seeing: the host's allowlist, what
# the host sets itself, and whatever the interpreter sets on itself.
_EXPECTED_VISIBLE = set(SAFE_ENV_VARS) | set(FORCED_ENV) | _INTERPRETER_INJECTED


def test_plugin_environment_is_built_from_nothing():
    """The builder itself, with no subprocess and no interpreter in the way.

    `plugin_environment` is a pure function, so this can assert the exact
    key set -- which the subprocess test cannot, because CPython adds to
    its own environment at startup (see _INTERPRETER_INJECTED).
    """
    hostile_parent = {
        "PATH": "/usr/bin",
        "GITHUB_TOKEN": "ghp_secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "ROMM_PASSWORD": "secret",
        "PYTHONPATH": "/attacker/controlled",
        "PYTHONHOME": "/attacker/controlled",
        "LD_PRELOAD": "/attacker/controlled.so",
        "ROMM_HUB_SECRET_CANARY": "secret",
    }
    env = plugin_environment(hostile_parent)

    assert env == {"PATH": "/usr/bin", **FORCED_ENV}, (
        "the child environment must be built from {} upward, so a parent "
        "variable that is not on the allowlist can never appear"
    )


def test_plugin_environment_omits_what_the_parent_does_not_have():
    """Adding, not removing: an absent variable stays absent rather than
    arriving empty and shadowing a default."""
    assert plugin_environment({}) == dict(FORCED_ENV)

# A name-based check cannot catch a regression that leaks a name nobody
# thought of, so the count is asserted too. The real parent environment on
# the machine this was found on had 92 entries; anything approaching that is
# inheritance coming back.
MAX_PLUGIN_ENV_VARS = 12


def test_the_plugin_environment_is_an_allowlist_not_an_inheritance(monkeypatch):
    """Not Linux-only: this is inheritance, not seccomp.

    Phase 2 made `import` read ROMM_PASSWORD from the environment, and
    `subprocess.Popen` hands the whole environment to the child by default.
    A plugin could read secrets out of its own `os.environ` -- no file
    access, no socket, and no syscall the seccomp filter can even see. The
    environment is the one channel with no backstop behind it.

    The first fix was a denylist of the RomM names, and it was wrong: it
    stopped the three variables someone had listed and passed through 92
    others, including a real GitHub token. The next secret is always the one
    nobody listed, so the child environment is now built from `{}` upward --
    the same default-deny shape as `manifest.py` (rejects everything
    unknown) and `netpolicy` (denies by default).
    """
    for name, value in SECRETS_IN_THE_PARENT.items():
        monkeypatch.setenv(name, value)

    seen = _plugin_environment_names(monkeypatch)

    leaked = sorted(seen & set(SECRETS_IN_THE_PARENT))
    assert not leaked, f"secrets reached the plugin: {leaked}"

    # The load-bearing assertion. Everything above is a special case of it.
    unexpected = sorted(seen - _EXPECTED_VISIBLE)
    assert not unexpected, (
        f"plugin inherited variables that are not on the allowlist: {unexpected}"
    )

    assert len(seen) <= MAX_PLUGIN_ENV_VARS, (
        f"plugin sees {len(seen)} variables; the allowlist should yield a "
        f"handful. Inherited environment has probably come back."
    )


def test_a_plugin_still_starts_with_only_the_allowlist(monkeypatch):
    """An over-tight allowlist breaks the interpreter, not the security model.

    The failure mode is a plugin that cannot start at all, so this asserts
    the child got far enough to import its own code and answer -- which the
    reply in `_plugin_environment_names` already proves -- and that PATH
    survived, since that is what everything else depends on.
    """
    seen = _plugin_environment_names(monkeypatch)
    assert "PATH" in seen, "a plugin subprocess with no PATH is not viable"
