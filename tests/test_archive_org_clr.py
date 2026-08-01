"""Archive.org at Console Living Room scale: paging, mapping, controls.

Every fixture in `tests/fixtures/archive_org/` used here was captured from
the live service, including the failures -- `search_deep_paging_error.json`
is Archive.org's own refusal to page past 10,000 results, verbatim, and is
the reason `index.py` exists in the shape it does.

**No test here opens a socket.** The plugin's only network path is
`ctx.http`, and `FakeHttp` stands in for it. That is not a convenience: a
plugin subprocess is seccomp-confined and could not open one anyway, so a
test that needed the network would be testing something the plugin cannot
do.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "archive_org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org import controls  # noqa: E402
from archive_org.index import (  # noqa: E402
    DEEP_PAGING_LIMIT,
    MAX_ROWS,
    Index,
    IndexUnavailable,
    build_query,
)
from archive_org.metadata import Metadata  # noqa: E402
from archive_org.platforms import emulators_for, platform_for  # noqa: E402
from archive_org.search import Search, SearchRefused  # noqa: E402
from archive_org.stream import Stream  # noqa: E402

from rom_hub.types import RomRef, SearchResult  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

ALLOWLIST = ["archive.org", "*.archive.org"]


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeHttp:
    """One canned reply per call, or the same reply every time.

    `replies` may be a list, which is consumed in order -- that is how the
    retry and halving behaviour is exercised without a clock or a socket.
    """

    def __init__(self, replies, status_code=200):
        self.replies = replies
        self.status_code = status_code
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if isinstance(self.replies, list):
            reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        else:
            reply = self.replies
        if isinstance(reply, tuple):
            status, body = reply
        else:
            status, body = self.status_code, reply
        if not isinstance(body, str):
            body = json.dumps(body)
        return HttpResponse(status_code=status, text=body)


# -- build_query --------------------------------------------------------


def test_an_emulator_filter_is_an_or_of_archive_orgs_own_ids():
    q = build_query("sonic", ["consolelivingroom"], emulators=["genesis", "megadriv"])
    assert q == (
        'title:("sonic") AND collection:(consolelivingroom) '
        'AND emulator:("genesis" OR "megadriv")'
    )


def test_downloadable_only_excludes_archive_orgs_own_stream_only_marker():
    q = build_query(None, ["consolelivingroom"], downloadable_only=True)
    assert q == "collection:(consolelivingroom) AND NOT collection:(stream_only)"


def test_an_emulator_id_cannot_break_out_of_its_quoted_phrase():
    """These ids come from a table in this repo today, and from an
    operator's `--platform` tomorrow. The escaping is the same either
    way: the injected quote is neutralised, so the whole hostile string
    stays one Lucene phrase."""
    q = build_query(None, ["consolelivingroom"], emulators=['x" OR emulator:("y'])
    assert q == (
        'collection:(consolelivingroom) '
        'AND emulator:("x\\" OR emulator:(\\"y")'
    )


def test_browsing_a_collection_with_no_query_is_still_possible():
    assert build_query("", ["consolelivingroom"]) == "collection:(consolelivingroom)"


# -- index.py: what the live service actually does ----------------------


def test_a_bulk_read_omits_page_because_page_is_what_imposes_the_ceiling():
    """The whole reason this module exists.

    `advancedsearch.php` refuses page 101 of 100 and the error names the
    escape: ask for any number of results at once, without a page.
    """
    http = FakeHttp(fixture("search_clr_page.json"))
    Index(http).fetch("collection:(consolelivingroom)", 20000)
    _, params = http.calls[0]
    assert "page" not in params
    assert params["rows"] == 20000


def test_a_small_read_may_still_be_paged():
    http = FakeHttp(fixture("search_clr_page.json"))
    Index(http).fetch("collection:(consolelivingroom)", 25, page=3)
    assert http.calls[0][1]["page"] == 3


def test_a_page_beyond_archive_orgs_own_limit_is_refused_before_it_is_sent():
    http = FakeHttp(fixture("search_clr_page.json"))
    with pytest.raises(IndexUnavailable, match=str(DEEP_PAGING_LIMIT)):
        Index(http).fetch("collection:(consolelivingroom)", 100, page=101)
    assert http.calls == []


def test_archive_orgs_deep_paging_refusal_is_reported_and_not_retried():
    """The captured error body, verbatim. Halving `rows` cannot fix a query
    the service has rejected, so it must not be tried."""
    http = FakeHttp(raw("search_deep_paging_error.json"))
    with pytest.raises(IndexUnavailable, match="DEEP_PAGING"):
        Index(http).fetch("collection:(consolelivingroom)", 500)
    assert len(http.calls) == 1


def test_a_failing_request_is_retried_and_then_asked_for_fewer_rows():
    """A caller that asked for 4,000 and can be given 1,000 is better served
    than one given an exception."""
    page = fixture("search_clr_page.json")
    http = FakeHttp([(503, "{}"), (503, "{}"), (503, "{}"), (503, "{}"), page])
    docs = Index(http).fetch("collection:(consolelivingroom)", 4000)
    assert len(docs) == 25
    sizes = [c[1]["rows"] for c in http.calls]
    assert sizes == [4000, 4000, 2000, 2000, 1000]


def test_a_response_that_is_not_json_counts_as_a_failure_not_a_crash():
    """Rate limiting and maintenance both arrive as 200 + HTML."""
    http = FakeHttp("<html>Too many requests</html>")
    with pytest.raises(IndexUnavailable, match="rate-limit"):
        Index(http).fetch("collection:(consolelivingroom)", 4)


def test_one_query_is_only_asked_once():
    http = FakeHttp(fixture("search_clr_page.json"))
    index = Index(http)
    index.fetch("collection:(consolelivingroom)", 25)
    index.fetch("collection:(consolelivingroom)", 25)
    assert len(http.calls) == 1


def test_a_config_typo_cannot_ask_archive_org_for_a_million_rows():
    http = FakeHttp(fixture("search_clr_page.json"))
    Index(http, max_rows=10**9).fetch("collection:(consolelivingroom)", 10**9)
    assert http.calls[0][1]["rows"] == MAX_ROWS


def test_total_reads_numfound_from_a_rows_zero_response():
    http = FakeHttp(fixture("search_count_only.json"))
    assert Index(http).total('collection:(consolelivingroom) AND emulator:("nes")') == 355
    assert http.calls[0][1]["rows"] == 0


# -- platforms: the census, read both ways ------------------------------


@pytest.mark.parametrize(
    "emulator,slug",
    [
        ("genesis", "genesis"),
        ("megadriv", "genesis"),
        ("megadrij", "genesis"),
        ("a2600", "atari2600"),
        ("a7800", "atari7800"),
        ("a5200", "atari5200"),
        ("gameboy", "gb"),
        ("gbcolor", "gbc"),
        ("ngpc", "neo-geo-pocket-color"),
        ("sgx", "supergrafx"),
        ("smsj", "sms"),
        ("sms-phaser", "sms"),
        ("intv2", "intellivision"),
        ("odyssey2", "odyssey-2"),
        ("channelf", "fairchild-channel-f"),
    ],
)
def test_the_console_ids_map_to_the_machine_romm_names(emulator, slug):
    assert platform_for(emulator) == slug


@pytest.mark.parametrize(
    "emulator",
    [
        # MAME romset names: a game id, not a machine id.
        "galaxian",
        "mspacman",
        "tmnt2",
        # A misspelling of `genesis`, on one item.
        "genisis",
        # Machines RomM has no slug for.
        "bally",
        "socrates",
        # Composite values: an emulator plus a loader configuration.
        "gameboy,gb",
        # Ambiguous targets, left out on purpose since before this work.
        "ruffle-swf",
    ],
)
def test_the_long_tail_is_left_unmapped_rather_than_guessed(emulator):
    assert platform_for(emulator) is None


def test_one_romm_slug_gathers_every_spelling_archive_org_uses_for_it():
    assert emulators_for("genesis") == ["genesis", "megadrij", "megadriv"]
    assert emulators_for("sms") == ["sms", "sms-phaser", "smsj"]


def test_a_platform_this_source_holds_nothing_under_answers_empty():
    assert emulators_for("nintendo-switch-2") == []


def test_every_mapped_slug_is_one_the_rest_of_the_catalogue_already_uses():
    """A slug RomM does not know fails much later, at `platform_id()`, with
    a far less useful message. The other plugins' declared platforms are
    the vocabulary that has already been checked against RomM."""
    from rom_hub.catalog import load_catalog

    from archive_org.platforms import EMULATOR_PLATFORMS

    catalog = Path(__file__).resolve().parents[1] / "catalog" / "plugins.json"
    known = set()
    for entry in load_catalog(catalog):
        known.update(entry.platforms)
    unknown = sorted(set(EMULATOR_PLATFORMS.values()) - known)
    assert not unknown, f"slugs no other plugin files to: {unknown}"


# -- controls: what Archive.org really carries --------------------------


def test_the_genesis_boilerplate_is_recognised_as_control_text():
    item = fixture("metadata_genesis_notes.json")
    blob = controls.extract(item["metadata"], "whac-a-critter-usa-unl")
    assert blob["source_fields"] == ["notes"]
    assert "Arrow Keys" in blob["notes"]
    # The markup is dropped, not carried into a library field.
    assert "<b>" not in blob["notes"]
    assert blob["emulator"] == "genesis"


def test_the_one_structured_field_is_carried_verbatim():
    item = fixture("metadata_a2600_controller.json")
    blob = controls.extract(item["metadata"], "meltdown")
    assert blob["controller"] == "joystick"
    assert "controller" in blob["source_fields"]
    assert "SELECT switch" in blob["instructions"]


def test_archive_orgs_own_instructions_field_is_trusted_without_a_test():
    """Two of the eight distinct texts in that field would fail
    `is_control_text` -- the Socrates and Arcadia ones -- and both plainly
    are control instructions. A field whose whole population is control
    text does not need a gate."""
    item = fixture("metadata_stream_only.json")
    blob = controls.extract(item["metadata"], "trap-shooting")
    assert "emulator_instructions" in blob["source_fields"]
    assert blob["instructions"]


def test_an_item_with_no_control_field_yields_nothing_at_all():
    """None, not an empty blob: `MetadataPatch` reads absent as 'leave the
    library alone' and present-but-empty as 'replace it with nothing'."""
    item = fixture("metadata_vectrex_no_controls.json")
    assert controls.extract(item["metadata"], "vectrex-tankdemo") is None
    assert controls.patch_field(None) == {}


@pytest.mark.parametrize(
    "text",
    [
        "Unofficial boxart by me",
        "This is a game cartridge for the WASM-4 fantasy console. The "
        "bubblewrap.wasm file contains the cart data (ROM), in WebAssembly "
        "format.",
    ],
)
def test_uploader_chatter_in_notes_is_not_filed_as_a_control_mapping(text):
    """Both captured live from `notes`, which is the field that mixes them
    in with the boilerplate."""
    assert not controls.is_control_text(text)
    assert controls.extract({"notes": text, "emulator": "gba"}) is None


@pytest.mark.parametrize(
    "text",
    [
        "Press the 1 key to start games. Use Arrow Keys to move up, left, "
        "right and down. There are three buttons, A, B and C.",
        "CONTROLS FOR THE ATARI 7800: Use the ARROW KEYS to move, the "
        "CONTROL key for button 1.",
    ],
)
def test_the_control_boilerplate_passes(text):
    assert controls.is_control_text(text)


def test_a_field_given_as_a_list_is_still_read():
    blob = controls.extract({"notes": ["Use the arrow keys", "to move the joystick"]})
    assert blob["notes"] == "Use the arrow keys to move the joystick"


def test_an_escaped_tag_in_the_source_cannot_become_a_tag():
    assert "<b>" not in controls.plain_text("&lt;b&gt;press the fire button&lt;/b&gt;")


def test_the_blob_is_namespaced_inside_the_one_raw_field_it_may_use():
    blob = controls.extract(fixture("metadata_genesis_notes.json")["metadata"])
    patch = controls.patch_field(blob)
    assert list(patch) == ["raw_manual_metadata"]
    assert list(patch["raw_manual_metadata"]) == [controls.BLOB_KEY]


def test_the_blob_fits_the_hosts_own_limit_on_a_raw_field():
    """`MetadataPatch` refuses a raw blob over 256 KiB. A control field is
    prose from a stranger, so it is bounded before it gets there."""
    from rom_hub.types import MAX_RAW_METADATA_CHARS

    blob = controls.extract({"notes": "press the fire button " * 100000})
    encoded = json.dumps(controls.patch_field(blob))
    assert len(encoded) < MAX_RAW_METADATA_CHARS


# -- search -------------------------------------------------------------


def _search(config=None, replies=None):
    http = FakeHttp(replies if replies is not None else fixture("search_clr_page.json"))
    return Search(PluginContext(config=config or {}, http=http)), http


def test_the_default_scope_now_reaches_the_console_half_of_the_archive():
    """`softwarelibrary` holds 250,382 items and `consolelivingroom` 24,746,
    with 212 in both -- measured. The old default could not see a single
    Mega Drive cartridge."""
    search, http = _search()
    search.search("sonic", None, 25)
    assert "consolelivingroom" in http.calls[0][1]["q"]
    assert "softwarelibrary" in http.calls[0][1]["q"]


def test_a_platform_filter_asks_for_every_spelling_of_that_machine():
    search, http = _search(replies=fixture("search_clr_genesis.json"))
    search.search("sonic", "genesis", 12)
    q = http.calls[0][1]["q"]
    assert 'emulator:("genesis" OR "megadrij" OR "megadriv")' in q


def test_a_platform_this_source_holds_nothing_under_says_so():
    """Silently returning no results would look like 'Archive.org has no
    Switch games', which is a different statement from 'this plugin files
    nothing there'."""
    search, http = _search()
    with pytest.raises(SearchRefused, match="platforms.py"):
        search.search("mario", "nintendo-switch-2", 25)
    assert http.calls == []


def test_a_result_names_the_romm_slug_not_archive_orgs_emulator_id():
    search, _ = _search(replies=fixture("search_clr_genesis.json"))
    results = search.search("sonic", "genesis", 12)
    assert results
    assert {r.platform for r in results} == {"genesis"}
    assert all(r.extra["emulator"] in ("genesis", "megadriv", "megadrij")
               for r in results)


def test_an_unmapped_emulator_still_appears_in_search():
    """Hiding it would hide the fact that it needs mapping. The refusal
    belongs at import, where a platform is actually required."""
    docs = {"response": {"numFound": 1, "docs": [
        {"identifier": "x", "title": "X", "emulator": "galaxian",
         "collection": ["consolelivingroom"]}]}}
    search, _ = _search(replies=docs)
    (result,) = search.search("x", None, 5)
    assert result.platform is None
    assert result.extra["emulator"] == "galaxian"


def test_stream_only_items_are_carried_rather_than_filtered():
    """6,816 of the collection's 24,746 items are stream-only, and they are
    the `stream` capability's entire population."""
    search, _ = _search()
    results = search.search("", None, 25)
    assert any(r.extra["stream_only"] == "true" for r in results)


def test_downloadable_only_is_what_drops_them():
    search, http = _search(config={"downloadable_only": True})
    search.search("", None, 25)
    assert "NOT collection:(stream_only)" in http.calls[0][1]["q"]


def test_a_result_says_whether_the_item_has_control_information():
    """Answered from the search index, which already carries `notes` --
    so a caller need not spend a metadata call per item finding out there
    was nothing."""
    search, _ = _search(replies=fixture("search_clr_genesis.json"))
    results = search.search("sonic", "genesis", 12)
    flags = {r.extra["has_controls"] for r in results}
    assert flags == {"true", "false"} or flags == {"true"}
    assert any(r.extra["has_controls"] == "true" for r in results)


def test_every_search_url_is_inside_the_declared_allowlist():
    from rom_hub.netpolicy import check_url

    search, http = _search()
    results = search.search("", None, 25)
    check_url(http.calls[0][0], ALLOWLIST)
    for result in results:
        check_url(result.url, ALLOWLIST)


def test_a_url_outside_the_allowlist_would_be_refused():
    """The check is the host's, and this asserts it would actually fire --
    a test that only ever checks compliant URLs proves nothing."""
    from rom_hub.netpolicy import PolicyViolation, check_url

    with pytest.raises(PolicyViolation):
        check_url("https://archive.org.evil.example/details/x", ALLOWLIST)


# -- metadata: the controls landing on a rom ----------------------------


def _rom(source_id="whac-a-critter-usa-unl"):
    return RomRef(rom_id=7, name="Whac A Critter", filename="whac.md",
                  extra={"source_id": source_id})


def test_control_text_reaches_the_library_as_a_raw_manual_blob():
    item = fixture("metadata_genesis_notes.json")
    provider = Metadata(PluginContext(config={}, http=FakeHttp(item)))
    patch = provider.enrich(_rom())
    blob = patch.raw_metadata["raw_manual_metadata"][controls.BLOB_KEY]
    assert "Arrow Keys" in blob["notes"]
    assert blob["source"] == "archive.org"


def test_a_rom_whose_item_says_nothing_about_controls_gets_no_raw_field():
    """Absent means leave alone. Writing an empty blob would erase whatever
    the library already had."""
    item = fixture("metadata_vectrex_no_controls.json")
    provider = Metadata(PluginContext(config={}, http=FakeHttp(item)))
    patch = provider.enrich(_rom("vectrex-tankdemo"))
    assert patch.raw_metadata == {}


def test_the_patch_still_carries_only_what_it_knows():
    """The controls are an addition, not a replacement: a patch that set
    every field would blank the ids a user had curated."""
    item = copy.deepcopy(fixture("metadata_genesis_notes.json"))
    provider = Metadata(PluginContext(config={}, http=FakeHttp(item)))
    patch = provider.enrich(_rom())
    assert patch.provider_ids == {}
    assert patch.artwork_base64 is None


# -- stream: the browser-bound half -------------------------------------


def test_a_stream_only_item_resolves_to_the_page_that_plays_it():
    item = fixture("metadata_stream_only.json")
    provider = Stream(PluginContext(config={}, http=FakeHttp(item)))
    target = provider.resolve(
        SearchResult(source_id="segasms_Trap_Shooting_Marksman_Shooting_Safari_"
                               "Hunt_1986_Sega", title="Trap Shooting")
    )
    assert target.kind == "url"
    assert target.target.startswith("https://archive.org/details/")
    assert target.extra["stream_only"] == "true"


def test_a_stream_target_carries_the_keyboard_mapping_it_will_need():
    item = fixture("metadata_stream_only.json")
    provider = Stream(PluginContext(config={}, http=FakeHttp(item)))
    target = provider.resolve(SearchResult(source_id="x", title="X"))
    assert target.extra["controls"]
    # `sms-phaser` is the Master System with the Light Phaser: one machine.
    assert target.extra["platform"] == "sms"


def test_every_stream_target_is_inside_the_declared_allowlist():
    from rom_hub.netpolicy import check_url

    item = fixture("metadata_stream_only.json")
    provider = Stream(PluginContext(config={}, http=FakeHttp(item)))
    target = provider.resolve(SearchResult(source_id="x", title="X"))
    check_url(target.target, ALLOWLIST)
