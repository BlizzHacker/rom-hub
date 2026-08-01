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


# --- the host side: what the Hub does with a validated target -------------
#
# The gate above proves a hostile plugin cannot get an undeclared host past
# the boundary. These prove the *second* gate: the one standing immediately
# in front of the act, which is what has to hold if the first has a gap.
# Same reasoning as `paths.dest_in_job_dir` behind `bare_filename`.

import httpx  # noqa: E402

from rom_hub import stream as host_stream  # noqa: E402
from rom_hub.stream import (  # noqa: E402
    BROWSER,
    HANDOFF,
    Handover,
    PlayRoute,
    StreamError,
    StreamOutcome,
    StreamServerClient,
    library_handover,
    library_player_path,
    library_player_url,
    open_handover,
    open_library_url,
    plan_handover,
)

ALLOWED = ["allowed.example", "*.allowed.example"]


class Opener:
    """A stand-in browser. Records what it was asked to open."""

    def __init__(self, result=True):
        self.opened: list[str] = []
        self.result = result

    def __call__(self, url):
        self.opened.append(url)
        return self.result


def test_a_url_target_becomes_a_browser_handover():
    target = StreamTarget(
        kind="url", target="https://allowed.example/play/x", title="X"
    )
    handover = plan_handover(target, ALLOWED, source="streamer")
    assert handover.route == BROWSER
    assert handover.url == "https://allowed.example/play/x"
    assert handover.playable
    assert "browser" in handover.how


def test_a_handle_target_becomes_a_handoff_the_hub_will_not_guess_about():
    handover = plan_handover(StreamTarget(kind="handle", target="ia:x"), ALLOWED)
    assert handover.route == HANDOFF
    assert handover.url is None
    assert not handover.playable
    assert "will not guess" in handover.how


def test_planning_refuses_a_url_whose_host_the_plugin_never_declared():
    """The third fetch-adjacent channel, gated identically to the other two.

    A plugin's `network` allowlist is what the operator reviewed; a stream
    target is a URL something will fetch, so an undeclared host is refused
    here exactly as a FetchPlan URL and a MetadataPatch artwork_url are.
    """
    target = StreamTarget(kind="url", target="https://evil.example/play")
    with pytest.raises(StreamError, match="evil.example"):
        plan_handover(target, ALLOWED)


def test_planning_refuses_a_cleartext_url_even_on_a_declared_host():
    target = StreamTarget(kind="url", target="http://allowed.example/play")
    with pytest.raises(StreamError, match="allowed.example"):
        plan_handover(target, ALLOWED)


def test_planning_refuses_a_lookalike_host():
    """`host_matches` is the thing being relied on; prove it is reached."""
    target = StreamTarget(kind="url", target="https://notallowed.example/x")
    with pytest.raises(StreamError):
        plan_handover(target, ALLOWED)


def test_a_wildcard_subdomain_the_plugin_declared_is_allowed():
    target = StreamTarget(kind="url", target="https://cdn.allowed.example/x")
    assert plan_handover(target, ALLOWED).route == BROWSER


def test_opening_checks_the_allowlist_again_at_the_moment_of_the_act():
    """A Handover can be built anywhere -- from --json, from a queue -- so
    the check that protects the operator is the one in front of the act."""
    smuggled = Handover(
        route=BROWSER,
        target=StreamTarget(kind="url", target="https://evil.example/x"),
        url="https://evil.example/x",
        how="open this URL in a browser to play it",
    )
    opener = Opener()
    with pytest.raises(StreamError, match="evil.example"):
        open_handover(smuggled, ALLOWED, opener=opener)
    assert opener.opened == []


def test_opening_a_good_target_launches_exactly_it():
    handover = plan_handover(
        StreamTarget(kind="url", target="https://allowed.example/play/x"), ALLOWED
    )
    opener = Opener()
    assert open_handover(handover, ALLOWED, opener=opener) == (
        "https://allowed.example/play/x"
    )
    assert opener.opened == ["https://allowed.example/play/x"]


def test_a_handle_cannot_be_opened():
    handover = plan_handover(StreamTarget(kind="handle", target="ia:x"), ALLOWED)
    opener = Opener()
    with pytest.raises(StreamError, match="cannot be opened"):
        open_handover(handover, ALLOWED, opener=opener)
    assert opener.opened == []


def test_a_browser_that_will_not_start_is_reported_not_swallowed():
    handover = plan_handover(
        StreamTarget(kind="url", target="https://allowed.example/x"), ALLOWED
    )
    with pytest.raises(StreamError, match="no browser"):
        open_handover(handover, ALLOWED, opener=Opener(result=False))


# --- the library's own player ---------------------------------------------


def test_the_library_player_url_is_the_backend_path_for_that_rom():
    assert (
        library_player_url("romm", "http://library.example:8080", 7)
        == "http://library.example:8080/rom/7/ejs"
    )


def test_a_trailing_slash_on_the_configured_base_does_not_double_up():
    assert (
        library_player_url("romm", "https://library.example/", 7)
        == "https://library.example/rom/7/ejs"
    )


def test_a_backend_with_no_verified_player_is_refused_not_guessed_at():
    """A guessed URL opens, 404s, and blames the wrong thing."""
    with pytest.raises(StreamError, match="no in-browser player"):
        library_player_path("nosuchbackend")


def test_a_base_that_is_not_an_http_origin_is_refused():
    for bad in ["", "library.example", "ftp://library.example", "/rom/1"]:
        with pytest.raises(StreamError, match="http"):
            library_player_url("romm", bad, 1)


def test_a_rom_id_must_be_a_real_id():
    with pytest.raises(StreamError, match="positive integer"):
        library_player_url("romm", "http://library.example", 0)


def test_the_library_handover_has_the_same_shape_as_a_plugin_one():
    handover = library_handover("romm", "http://library.example", 7)
    assert handover.route == BROWSER
    assert handover.playable
    assert handover.as_dict()["url"] == "http://library.example/rom/7/ejs"
    assert handover.as_dict()["extra"]["rom_id"] == "7"


def test_the_library_door_takes_no_allowlist_but_still_wants_an_origin():
    """No plugin was involved, so `netpolicy`'s https-only rule would be a
    plugin rule enforced against the operator's own LAN server. What is
    still enforced is that this is a URL at all."""
    opener = Opener()
    assert open_library_url("http://library.example/rom/7/ejs", opener) == (
        "http://library.example/rom/7/ejs"
    )
    with pytest.raises(StreamError, match="http"):
        open_library_url("rom/7/ejs", opener)
    assert opener.opened == ["http://library.example/rom/7/ejs"]


# --- what a romm-stream server says it can play ---------------------------


def _stream_server(handler):
    return StreamServerClient(
        "http://stream.example:8090", transport=httpx.MockTransport(handler)
    )


def test_the_stream_server_is_asked_only_about_routing():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"tier": "stream"})

    with _stream_server(handler) as client:
        route = client.route("dc")
    assert seen == ["/api/play/route"]
    assert route.tier == "stream"
    assert "server-side" in route.describe()


def test_a_local_tier_is_named_as_the_client_playing_it_itself():
    with _stream_server(
        lambda r: httpx.Response(200, json={"tier": "local"})
    ) as client:
        assert "EmulatorJS in the client" in client.route("nes").describe()


def test_an_unplayable_platform_carries_the_servers_own_reason():
    def handler(request):
        return httpx.Response(
            404, json={"error": "unplayable", "why": "no core for this platform"}
        )

    with _stream_server(handler) as client:
        route = client.route("windows")
    assert route.tier is None
    assert "no core for this platform" in route.describe()


def test_a_stream_server_that_is_down_does_not_fail_the_resolve():
    """The operator's answer is already in hand. `backends.degrade`'s
    reasoning, applied to a second service."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    with _stream_server(handler) as client:
        route = client.route("nes")
    assert not route.known
    assert "unreachable" in route.describe()


def test_a_reply_that_is_not_an_object_is_not_trusted():
    with _stream_server(lambda r: httpx.Response(200, json=["nope"])) as client:
        assert not client.route("nes").known


def test_asking_about_no_platform_at_all_asks_nothing():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"tier": "local"})

    with _stream_server(handler) as client:
        assert not client.route("  ").known
    assert seen == []


def test_the_streamable_list_comes_back_as_slugs():
    def handler(request):
        return httpx.Response(
            200, json={"streamable": ["dc", "ngc", 7], "unavailable": {}}
        )

    with _stream_server(handler) as client:
        assert client.streamable() == ["dc", "ngc"]


def test_the_client_holds_no_endpoint_that_starts_anything():
    """The session routes take a rom on the stream server's own disk or a
    library rom id plus credentials -- neither of which a plugin-resolved
    target is. Naming them here would be inventing an integration."""
    assert StreamServerClient.ALLOWED_PATHS == {
        "/api/play/route",
        "/api/play/streamable",
    }
    for path in StreamServerClient.ALLOWED_PATHS:
        assert "start" not in path and "offer" not in path


def test_a_path_outside_the_read_only_set_is_refused_by_the_client():
    with _stream_server(lambda r: httpx.Response(200, json={})) as client:
        with pytest.raises(StreamError, match="read-only"):
            client._get("/api/stream/start")


def test_a_stream_server_base_that_is_not_a_url_is_refused():
    for bad in ["", "stream.example", "ws://stream.example"]:
        with pytest.raises(StreamError, match="stream server"):
            StreamServerClient(bad)


# --- the outcome the CLI prints and --json emits --------------------------


def test_the_outcome_carries_the_decision_not_just_the_target():
    handover = plan_handover(
        StreamTarget(
            kind="url",
            target="https://allowed.example/x",
            title="X",
            extra={"stream_only": "true"},
        ),
        ALLOWED,
        source="streamer",
    )
    outcome = StreamOutcome(
        handover=handover,
        route=PlayRoute(platform="dos", tier="local"),
        opened="https://allowed.example/x",
        notes=["a note"],
    )
    data = outcome.as_dict()
    assert data["route"] == BROWSER
    assert data["kind"] == "url"
    assert data["source"] == "streamer"
    assert data["extra"]["stream_only"] == "true"
    assert data["stream_server"]["tier"] == "local"
    assert data["opened"] == "https://allowed.example/x"
    assert data["notes"] == ["a note"]


def test_the_host_side_never_builds_a_transport():
    """`stream` resolves and hands over; `romm-stream` streams.

    Read off the import graph rather than the prose, because the prose
    talks about subprocesses and sockets at length and should keep being
    allowed to. A second streaming server growing in here would need one
    of these modules first.
    """
    import ast

    tree = ast.parse(Path(host_stream.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"subprocess", "socket", "asyncio", "selectors"}
