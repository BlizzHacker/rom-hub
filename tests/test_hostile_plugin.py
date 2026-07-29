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

from romm_hub.broker.host import PluginProcess
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

SNOOPER = textwrap.dedent(
    """
    import os

    from romm_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            seen = [n for n in ("ROMM_URL", "ROMM_USER", "ROMM_PASSWORD")
                    if os.environ.get(n)]
            return [SearchResult(source_id="x", title="SAW:" + ",".join(seen) or "SAW:")]
    """
)


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


def test_the_romm_credentials_are_not_in_the_plugins_environment(monkeypatch):
    """Not Linux-only: this is inheritance, not seccomp.

    Phase 2 made `import` read ROMM_PASSWORD from the environment, and
    `subprocess.Popen` hands the whole environment to the child by default.
    Without the scrub in `PluginProcess.start()` a plugin reads the RomM
    password out of its own `os.environ` -- no file access, no syscall the
    filter could see, no allowlist involved. The design's central claim is
    that a plugin never holds the RomM token; this test is what makes that
    a fact about the process rather than a fact about the API surface.
    """
    monkeypatch.setenv("ROMM_URL", "http://romm.invalid:8080")
    monkeypatch.setenv("ROMM_USER", "admin")
    monkeypatch.setenv("ROMM_PASSWORD", "correct-horse-battery-staple")

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
            verdict = proc.search("q", None, 5)[0].title

    assert verdict == "SAW:", f"plugin can read the RomM credentials: {verdict}"
