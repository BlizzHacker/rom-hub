"""A secret reaches the plugin, and comes back out of everything printed.

These run a real subprocess, because the two claims worth checking are
about the boundary itself:

1. The plugin **does** get the value. That is not a weakening -- a plugin
   already runs arbitrary code, and it needs its own API key to make its
   own request. The threat this feature addresses is accidental disclosure,
   not a malicious plugin.
2. Nothing the host prints carries it, even when the plugin tries: a key in
   a traceback, a key printed to stderr while crashing, a key echoed back
   into a result the host then quotes in a validation error.

And one negative that matters as much as either: the value must not arrive
through the environment. `broker.host.SAFE_ENV_VARS` is built from `{}`
upward for a reason, and routing a credential through it would undo that.
"""

import textwrap
from pathlib import Path

import pytest

from rom_hub.broker.host import PluginCallError, PluginProcess, plugin_environment
from rom_hub.manifest import parse_manifest

SECRET = "RA-live-0123456789abcdefghijklmnop"

MANIFEST = """
[plugin]
slug = "keyed"
name = "Keyed"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "keyed_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []

[config]
api_key = { type = "secret" }
"""

PLUGIN_SRC = textwrap.dedent(
    '''
    import os
    import sys

    from rom_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            key = self.ctx.config.get("api_key", "")
            mode = self.ctx.config.get("mode", "report")
            if mode == "raise_with_key":
                raise ValueError("upstream rejected key=%s" % key)
            if mode == "print_key_and_die":
                print("DEBUG api_key=%s" % key, file=sys.stderr)
                sys.stderr.flush()
                os._exit(3)
            if mode == "echo_key_into_a_bad_result":
                # An invalid result, so the host raises a validation error
                # built from text the plugin chose.
                return [{"source_id": key, "title": None}]
            if mode == "environment":
                # Everything the subprocess can see, so the test can assert
                # the key is not among it.
                return [
                    SearchResult(source_id="env", title="|".join(sorted(os.environ)))
                ]
            return [SearchResult(source_id="key", title=key)]
    '''
)


class NullFetcher:
    def get(self, url, params):
        return 200, "payload"


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "keyed_plugin.py").write_text(PLUGIN_SRC, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, config=None, secrets=None):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=NullFetcher(),
        timeout=30.0,
        allow_unsandboxed=True,
        secrets=secrets if secrets is not None else {"api_key": SECRET},
    )


# -- it arrives ----------------------------------------------------------


def test_the_plugin_receives_the_secret_at_call_time(plugin_dir):
    """The whole point. A key it cannot read is a key it cannot use."""
    with _proc(plugin_dir) as proc:
        results = proc.search("x", None, 5)
    assert results[0].title == SECRET


def test_the_secret_is_not_mixed_into_the_processs_own_config(plugin_dir):
    """So anything that inspects a live PluginProcess sees plain settings."""
    proc = _proc(plugin_dir, config={"mode": "report"})
    assert proc.config == {"mode": "report"}
    assert "api_key" not in proc.config


def test_the_secret_does_not_arrive_through_the_environment(plugin_dir):
    """The allowlist in `plugin_environment` is built from {} upward.

    Handing a credential to a plugin through the environment would put it
    somewhere every library, every crash reporter and every `env` call can
    see -- and would reopen exactly the hole that allowlist closed.
    """
    with _proc(plugin_dir, config={"mode": "environment"}) as proc:
        names = proc.search("x", None, 5)[0].title.split("|")
    assert "api_key" not in names
    assert "ROM_HUB_SECRET_KEY" not in names
    assert SECRET not in plugin_environment({"api_key": SECRET}).values()


# -- and does not come back ----------------------------------------------


def test_a_plugin_raising_with_its_key_in_the_message_is_scrubbed(plugin_dir):
    with _proc(plugin_dir, config={"mode": "raise_with_key"}) as proc:
        with pytest.raises(PluginCallError) as exc:
            proc.search("x", None, 5)
    assert SECRET not in str(exc.value)
    assert "***" in str(exc.value)


def test_a_key_printed_to_stderr_before_a_crash_is_scrubbed(plugin_dir):
    """The most likely accidental disclosure there is: debug logging."""
    with _proc(plugin_dir, config={"mode": "print_key_and_die"}) as proc:
        with pytest.raises(PluginCallError) as exc:
            proc.search("x", None, 5)
    message = str(exc.value)
    assert SECRET not in message
    assert "DEBUG api_key=***" in message


def test_a_key_echoed_into_an_invalid_result_is_scrubbed(plugin_dir):
    """Validation errors quote the offending input, which the plugin chose."""
    with _proc(plugin_dir, config={"mode": "echo_key_into_a_bad_result"}) as proc:
        with pytest.raises(PluginCallError) as exc:
            proc.search("x", None, 5)
    assert SECRET not in str(exc.value)


def test_with_no_secrets_the_scrubber_changes_nothing(plugin_dir):
    """A plugin with no secrets must behave exactly as it did before."""
    with _proc(plugin_dir, config={"mode": "raise_with_key"}, secrets={}) as proc:
        with pytest.raises(PluginCallError) as exc:
            proc.search("x", None, 5)
    assert "upstream rejected key=" in str(exc.value)
    assert "***" not in str(exc.value)


def test_every_failure_this_class_reports_goes_through_the_scrubber():
    """Structural, not behavioural.

    The redaction is only as good as its coverage, and coverage decays the
    moment somebody adds a `raise PluginCallError(...)` by hand. So the
    source is checked: `_fail` is the only constructor.
    """
    import inspect

    from rom_hub.broker import host

    source = inspect.getsource(host.PluginProcess)
    assert "raise PluginCallError(" not in source
    assert source.count("raise self._fail(") > 10
