"""The `stream` capability: the type, the host gate, and the plugin.

The host side is thin on purpose -- `romm-stream` is a separate service --
but "thin" is not "unchecked". A `url` target is something a player will
fetch, so it passes the same allowlist as a FetchPlan URL, and the `kind`
discriminator must not become the way around that check.

The broker tests below run a real plugin subprocess, as in
test_broker_plan.py, and several of them return a duck-typed object whose
`model_dump()` emits whatever it likes.
"""

import copy
import json
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.manifest import parse_manifest
from rom_hub.types import SearchResult, StreamTarget

# --- the type -----------------------------------------------------------


def test_a_url_target_carries_its_kind():
    target = StreamTarget(kind="url", target="https://allowed.example/play")
    assert target.kind == "url"
    assert target.target == "https://allowed.example/play"


def test_a_handle_is_opaque_and_needs_no_scheme():
    target = StreamTarget(kind="handle", target="ia:rubik_202308")
    assert target.kind == "handle"


def test_a_handle_may_not_be_a_url():
    """Otherwise the discriminator IS the hole: declare `handle`, put a URL
    in it, and the allowlist check is skipped on a string every consumer
    would treat as a URL anyway."""
    with pytest.raises(ValidationError, match="declare kind='url'"):
        StreamTarget(kind="handle", target="https://evil.example/x")


@pytest.mark.parametrize(
    "scheme", ["http", "https", "file", "data", "javascript", "ftp"]
)
def test_no_fetchable_scheme_can_hide_behind_a_handle(scheme):
    with pytest.raises(ValidationError):
        StreamTarget(kind="handle", target=f"{scheme}://evil.example/x")


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValidationError):
        StreamTarget(kind="magnet", target="x")


@pytest.mark.parametrize("evil", ["a\rb", "a\nb", "a\x00b", "a\x1bb"])
def test_control_characters_are_refused_in_a_target(evil):
    """This string is printed, logged and handed to another service; a CR
    in it is a header-splitting or log-forging primitive depending on who
    consumes it."""
    with pytest.raises(ValidationError, match="control characters"):
        StreamTarget(kind="handle", target=evil)


def test_an_empty_target_is_refused():
    with pytest.raises(ValidationError):
        StreamTarget(kind="url", target="")


# --- the host gate, through a real plugin subprocess ---------------------

MANIFEST = """
[plugin]
slug = "streamer"
name = "Streamer"
version = "0.1.0"
rpp_version = "1"

[capabilities]
stream = "stream_plugin:Stream"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from rom_hub_sdk import StreamProvider, StreamTarget


    class Raw:
        """A plugin that skips the SDK's types entirely."""

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Stream(StreamProvider):
        def resolve(self, result):
            mode = self.ctx.config.get("mode", "good")

            if mode == "exfiltrate":
                return Raw({"kind": "url",
                            "target": "https://evil.example/play"})

            if mode == "handle_is_a_url":
                # The escape this exists to close.
                return Raw({"kind": "handle",
                            "target": "https://evil.example/play"})

            if mode == "raw_plain_http":
                return Raw({"kind": "url",
                            "target": "http://allowed.example/play"})

            if mode == "raw_not_a_mapping":
                return Raw(["not", "a", "target"])

            if mode == "raw_bad_kind":
                return Raw({"kind": "torrent", "target": "x"})

            if mode == "handle":
                return StreamTarget(kind="handle",
                                    target="ia:" + result.source_id)

            if mode == "refuse":
                raise RuntimeError("this item cannot be streamed")

            return StreamTarget(
                kind="url",
                target="https://allowed.example/play/" + result.source_id,
                mime_type="text/html",
                title="Playable",
                extra={"stream_only": "true"},
            )
    '''
)


class NullFetcher:
    """The stream path must never touch this. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "stream_plugin.py").write_text(PLUGIN, encoding="utf-8")
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


RESULT = SearchResult(source_id="rubik_202308", title="Rubik")


def test_resolve_returns_a_validated_target(plugin_dir):
    with _proc(plugin_dir) as proc:
        target = proc.resolve_stream(RESULT)
    assert target.kind == "url"
    assert target.target == "https://allowed.example/play/rubik_202308"
    assert target.title == "Playable"
    assert target.extra == {"stream_only": "true"}


def test_a_handle_target_comes_back_intact(plugin_dir):
    with _proc(plugin_dir, {"mode": "handle"}) as proc:
        target = proc.resolve_stream(RESULT)
    assert (target.kind, target.target) == ("handle", "ia:rubik_202308")


def test_a_url_target_on_an_undeclared_host_is_rejected(plugin_dir):
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.resolve_stream(RESULT)


def test_a_url_cannot_be_smuggled_through_as_a_handle(plugin_dir):
    """`kind` decides whether the allowlist is consulted, so `kind` is
    exactly what a hostile plugin would lie about."""
    with _proc(plugin_dir, {"mode": "handle_is_a_url"}) as proc:
        with pytest.raises(PluginCallError, match="invalid StreamTarget"):
            proc.resolve_stream(RESULT)


def test_a_cleartext_url_is_rejected_even_for_an_allowed_host(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_plain_http"}) as proc:
        with pytest.raises(PluginCallError, match="allowed.example"):
            proc.resolve_stream(RESULT)


def test_a_non_mapping_target_is_a_plugin_error_not_a_crash(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_not_a_mapping"}) as proc:
        with pytest.raises(
            PluginCallError,
            match="invalid StreamTarget: expected an object, got list",
        ):
            proc.resolve_stream(RESULT)


def test_an_unknown_kind_from_a_raw_plugin_is_rejected(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_bad_kind"}) as proc:
        with pytest.raises(PluginCallError, match="invalid StreamTarget"):
            proc.resolve_stream(RESULT)


def test_a_plugin_refusal_reaches_the_host_as_an_error(plugin_dir):
    with _proc(plugin_dir, {"mode": "refuse"}) as proc:
        with pytest.raises(PluginCallError, match="cannot be streamed"):
            proc.resolve_stream(RESULT)


def test_resolving_never_fetches_anything_itself(plugin_dir):
    """resolve() describes where to play; the host opens nothing."""
    fetcher = NullFetcher()
    with _proc(plugin_dir, fetcher=fetcher) as proc:
        proc.resolve_stream(RESULT)
    assert fetcher.calls == []


# --- the Archive.org implementation --------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.stream import Stream as ArchiveStream  # noqa: E402
from archive_org.stream import StreamRefused  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

# https://archive.org/metadata/msdos_Oregon_Trail_The_1990 -- the item the
# importer refuses. `stream_only` is what makes it un-importable and what
# makes it streamable.
OREGON_TRAIL = {
    "metadata": {
        "identifier": "msdos_Oregon_Trail_The_1990",
        "collection": ["softwarelibrary_msdos_games", "stream_only", "emulation"],
        "title": "The Oregon Trail",
        "emulator": "dosbox",
        "emulator_ext": "zip",
    },
    "files": [{"name": "oregon.zip", "format": "ZIP", "size": "100"}],
}


class FakeHttp:
    def __init__(self, payload=None, status_code=200, text=None):
        self.payload = OREGON_TRAIL if payload is None else payload
        self.status_code = status_code
        self.text = text
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        body = self.text if self.text is not None else json.dumps(self.payload)
        return HttpResponse(status_code=self.status_code, text=body)


def _archive(http=None):
    http = http or FakeHttp()
    return ArchiveStream(PluginContext(config={}, http=http)), http


def test_a_stream_only_item_resolves_to_its_details_page():
    """The item the importer refuses is exactly the one this serves."""
    provider, http = _archive()
    target = provider.resolve(
        SearchResult(source_id="msdos_Oregon_Trail_The_1990", title="x")
    )
    assert target.kind == "url"
    assert target.target == (
        "https://archive.org/details/msdos_Oregon_Trail_The_1990"
    )
    assert target.title == "The Oregon Trail"
    assert target.extra["stream_only"] == "true"
    assert target.extra["emulator"] == "dosbox"
    assert http.calls == [
        "https://archive.org/metadata/msdos_Oregon_Trail_The_1990"
    ]


def test_a_downloadable_item_is_still_playable_and_says_so():
    item = copy.deepcopy(OREGON_TRAIL)
    item["metadata"]["collection"] = ["softwarelibrary_msdos_games"]
    provider, _ = _archive(FakeHttp(item))
    target = provider.resolve(SearchResult(source_id="rubik_202308", title="x"))
    assert target.extra["stream_only"] == "false"


def test_a_non_emulated_item_is_refused_rather_than_pointed_at():
    """A details page that will not play anything is worse than a refusal."""
    item = copy.deepcopy(OREGON_TRAIL)
    del item["metadata"]["emulator"]
    provider, _ = _archive(FakeHttp(item))
    with pytest.raises(StreamRefused, match="not an emulated item"):
        provider.resolve(SearchResult(source_id="x", title="x"))


def test_an_unknown_identifier_is_refused():
    provider, _ = _archive(FakeHttp({}))
    with pytest.raises(StreamRefused, match="no item"):
        provider.resolve(SearchResult(source_id="nope", title="x"))


def test_the_archive_org_target_is_inside_its_declared_allowlist():
    from rom_hub.netpolicy import check_url

    provider, _ = _archive()
    target = provider.resolve(SearchResult(source_id="msdos_x", title="x"))
    check_url(target.target, ["archive.org", "*.archive.org"])
