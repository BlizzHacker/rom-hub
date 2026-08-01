"""The `TorrentSource` gate, at the boundary where the answer leaves the subprocess.

`resolve_torrent()` is the sixth channel by which a plugin can make the
host reach a host, and it is the one with the most ways to be a hole: the
`torrent_url` is fetched by the host, and everything *inside* the fetched
torrent -- trackers, web seeds -- is a location somebody will contact
afterwards.

A hostile plugin is not obliged to use the SDK's types, so several tests
here return a duck-typed object whose `model_dump()` emits whatever it
likes. Everything the host trusts is re-established on the host side.

The tests for what happens to the torrent's *own* URLs once it has been
fetched live in `test_torrents.py`; this file is about the plugin's
answer.
"""

import textwrap
from pathlib import Path

import pytest

from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.manifest import parse_manifest
from rom_hub.types import SearchResult

MANIFEST = """
[plugin]
slug = "tor"
name = "Tor"
version = "0.1.0"
rpp_version = "1"

[capabilities]
torrent = "tor_plugin:Tor"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from rom_hub_sdk import SearchResult, TorrentProvider, TorrentSource


    class Raw:
        """A plugin that skips the SDK's types entirely.

        Nothing stops a real hostile plugin doing exactly this: the runner
        only calls model_dump() on whatever resolve() hands back.
        """

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Tor(TorrentProvider):
        def resolve(self, result):
            mode = self.ctx.config.get("mode", "good")

            if mode == "undeclared":
                return TorrentSource(
                    kind="torrent_url",
                    source="https://evil.example/x.torrent",
                )

            if mode == "raw_undeclared":
                # No SDK validation anywhere in this path.
                return Raw({
                    "kind": "torrent_url",
                    "source": "https://evil.example/x.torrent",
                    "files": [],
                })

            if mode == "raw_plain_http":
                # An allowlisted host, but cleartext. netpolicy permits
                # https only and this path must honour that too.
                return Raw({
                    "kind": "torrent_url",
                    "source": "http://allowed.example/x.torrent",
                    "files": [],
                })

            if mode == "raw_userinfo":
                # The host is evil.example; the allowlisted name is userinfo.
                return Raw({
                    "kind": "torrent_url",
                    "source": "https://allowed.example@evil.example/x.torrent",
                    "files": [],
                })

            if mode == "raw_magnet_as_url":
                # Declaring a magnet as a torrent_url would route it to
                # check_url, which is not the check a magnet needs.
                return Raw({
                    "kind": "torrent_url",
                    "source": "magnet:?xt=urn:btih:" + "a" * 40,
                    "files": [],
                })

            if mode == "raw_url_as_magnet":
                # And the reverse: calling an http URL a magnet would skip
                # check_url entirely.
                return Raw({
                    "kind": "magnet",
                    "source": "https://evil.example/x.torrent",
                    "files": [],
                })

            if mode == "raw_file_url":
                return Raw({
                    "kind": "torrent_url",
                    "source": "file:///etc/passwd",
                    "files": [],
                })

            if mode == "raw_traversal":
                return Raw({
                    "kind": "torrent_url",
                    "source": "https://allowed.example/x.torrent",
                    "files": ["../../escape.zip"],
                })

            if mode == "raw_not_a_mapping":
                return Raw(["not", "a", "source"])

            if mode == "raw_bad_info_hash":
                return Raw({
                    "kind": "torrent_url",
                    "source": "https://allowed.example/x.torrent",
                    "files": [],
                    "info_hash": "nope",
                })

            if mode == "magnet":
                return TorrentSource(
                    kind="magnet",
                    source=(
                        "magnet:?xt=urn:btih:"
                        "6e56c747303e7bf35bf86b1956fb7ea06c99b805"
                        "&tr=udp%3A%2F%2Ftracker.allowed.example%3A6969"
                    ),
                )

            return TorrentSource(
                kind="torrent_url",
                source="https://allowed.example/x.torrent",
                files=["rom.zip"],
                info_hash="6e56c747303e7bf35bf86b1956fb7ea06c99b805",
            )
    '''
)


class NullFetcher:
    """This path must never touch the network. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "tor_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, config=None, fetcher=None):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=fetcher or NullFetcher(),
        timeout=30.0,
        # Windows cannot seccomp; the host is fail-closed by default.
        allow_unsandboxed=True,
    )


def _resolve(plugin_dir, mode=None, fetcher=None):
    with _proc(plugin_dir, {"mode": mode} if mode else None, fetcher) as proc:
        return proc.resolve_torrent(SearchResult(source_id="1", title="t"))


def test_a_good_source_comes_back_validated(plugin_dir):
    source = _resolve(plugin_dir)
    assert source.kind == "torrent_url"
    assert source.source == "https://allowed.example/x.torrent"
    assert source.files == ["rom.zip"]
    assert source.info_hash == "6e56c747303e7bf35bf86b1956fb7ea06c99b805"


def test_a_torrent_url_on_an_undeclared_host_is_rejected(plugin_dir):
    """The gate this capability exists behind.

    A `.torrent` URL is something the host will go and download, so it is
    exactly as privileged as a FetchPlan URL and gets exactly the same
    check. Without it, `network = [...]` in a manifest would mean nothing
    for torrents.
    """
    with pytest.raises(PluginCallError, match="evil.example"):
        _resolve(plugin_dir, "undeclared")


def test_the_gate_holds_for_a_plugin_that_skips_the_sdk_entirely(plugin_dir):
    """The SDK's validation is a courtesy; the host's is the enforcement."""
    with pytest.raises(PluginCallError, match="evil.example"):
        _resolve(plugin_dir, "raw_undeclared")


def test_no_request_is_made_for_a_rejected_source(plugin_dir):
    """Nothing below the check runs. The refusal costs no traffic at all."""
    fetcher = NullFetcher()
    with pytest.raises(PluginCallError):
        _resolve(plugin_dir, "raw_undeclared", fetcher)
    assert fetcher.calls == []


@pytest.mark.parametrize(
    "mode, fragment",
    [
        # An allowlisted host over cleartext. netpolicy is https-only and
        # this path must not be the exception.
        ("raw_plain_http", "TorrentSource rejected"),
        # https://allowed.example@evil.example/ -- .hostname is what
        # defeats it, and it has to defeat it here too.
        ("raw_userinfo", "evil.example"),
        ("raw_file_url", "invalid TorrentSource"),
    ],
)
def test_url_shapes_that_look_allowed_and_are_not(plugin_dir, mode, fragment):
    with pytest.raises(PluginCallError, match=fragment):
        _resolve(plugin_dir, mode)


def test_the_kind_cannot_be_used_to_pick_the_check(plugin_dir):
    """`StreamTarget`'s rule, and the reason it is not a formality.

    If a plugin could call a magnet a `torrent_url`, it would be routed to
    a check that says nothing useful about a magnet. If it could call an
    https URL a `magnet`, it would skip `check_url` altogether -- which is
    the actual escape, and it is refused by the wire type before the gate
    is even reached.
    """
    with pytest.raises(PluginCallError, match="must be an http"):
        _resolve(plugin_dir, "raw_magnet_as_url")
    with pytest.raises(PluginCallError, match="must be a magnet"):
        _resolve(plugin_dir, "raw_url_as_magnet")


def test_a_wanted_file_selector_cannot_be_a_traversal(plugin_dir):
    """Re-established host-side, because the plugin never ran the validator."""
    with pytest.raises(PluginCallError, match="invalid TorrentSource"):
        _resolve(plugin_dir, "raw_traversal")


def test_a_reply_that_is_not_an_object_is_rejected(plugin_dir):
    with pytest.raises(PluginCallError, match="expected an object, got list"):
        _resolve(plugin_dir, "raw_not_a_mapping")


def test_a_malformed_info_hash_claim_is_rejected(plugin_dir):
    with pytest.raises(PluginCallError, match="invalid TorrentSource"):
        _resolve(plugin_dir, "raw_bad_info_hash")


def test_a_magnet_passes_this_gate_and_is_checked_elsewhere(plugin_dir):
    """A deliberate split, asserted so it cannot become an accident.

    `check_url` permits https only, so it can say nothing useful about a
    `magnet:` URI -- running it here would either refuse every magnet or
    invite somebody to widen `ALLOWED_SCHEMES`, weakening the gate for the
    five capabilities that need it https-only. A magnet is taken apart in
    `torrents.check_magnet` instead, and that runs before anything is done
    with it.
    """
    source = _resolve(plugin_dir, "magnet")
    assert source.kind == "magnet"

    from rom_hub.torrents import TorrentError, check_magnet

    # Against this plugin's real allowlist the tracker is undeclared, and
    # that is where the refusal happens.
    with pytest.raises(TorrentError, match="outside the plugin's network"):
        check_magnet(source.source, ["allowed.example"])
    # Declare the tracker's host and the same magnet is accepted.
    link = check_magnet(source.source, ["*.allowed.example"])
    assert link.info_hash == "6e56c747303e7bf35bf86b1956fb7ea06c99b805"
