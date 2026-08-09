"""The `softwarelibrary` census, replayed against captured Archive.org answers.

`tests/fixtures/archive_org_census/` holds nine verbatim captures, all
taken on 2026-08-08 and trimmed only of response envelope fields the
plugin never reads (`numFound` and `docs` are kept exactly as they
arrived):

* `scope_total.json` — `collection:(softwarelibrary)` at `rows=0`. The
  number every window's declared total has to sum to.
* `scope_unsized.json` — `NOT item_size:[* TO *]` within the collection.
  It answers **zero**, which is the single fact this whole partition rests
  on: an item without `item_size` would sit in no window at all.
* `window_totals.json` — the `rows=0` count of each of the twenty-seven
  ladder windows, fetched one request per window. The sum is asserted
  against `scope_total.json` below, which is the completeness claim in its
  smallest form and the reason this file is worth reading.
* `small_total.json` / `small_read.json` — one real window,
  `item_size:[0 TO 45000]`, declared 776 and read whole. It holds 48
  `mediatype:collection` listings and 74 `stream_only` items, so the skip
  reason and the stream marker are exercised on the real distribution
  rather than on examples somebody constructed.
* `tail_total.json` / `tail_read.json` — the last window,
  `item_size:[5698242445 TO *]`, declared 946 and read whole. It is the
  one ladder window small enough to keep entire, and it is where an
  unbounded upper bound has to work.
* `sub_collections.json`, `untitled.json` — forty sub-collection listings
  and all eight items in the collection with no title at all.

Nothing here was tidied into agreement: `numFound` still equals
`len(docs)` in each read, which is the identity the arithmetic rests on
and would be worthless if a fixture had been trimmed to make it true.

No test opens a socket.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "archive_org_census"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.census import (  # noqa: E402
    CENSUS_FIELDS,
    PAGE_ROWS,
    SIZE_LADDER,
    SKIP_SUB_COLLECTION,
    SKIP_VANISHED,
    Census,
    CensusUnavailable,
)
from archive_org.index import Index, OPEN, parse_size_clause  # noqa: E402

from rom_hub.census import Catalogue, build  # noqa: E402
from rom_hub.grouping import group_results  # noqa: E402
from rom_hub_sdk import CensusUnit  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

SEARCH = "https://archive.org/advancedsearch.php"
COLLECTION = "collection:(softwarelibrary)"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


SCOPE_TOTAL = _fixture("scope_total")
SCOPE_UNSIZED = _fixture("scope_unsized")
WINDOW_TOTALS = _fixture("window_totals")
SMALL_TOTAL = _fixture("small_total")
SMALL_READ = _fixture("small_read")
TAIL_TOTAL = _fixture("tail_total")
TAIL_READ = _fixture("tail_read")
SUB_COLLECTIONS = _fixture("sub_collections")
UNTITLED = _fixture("untitled")

SMALL_WINDOW = "item_size:[0 TO 45000]"
TAIL_WINDOW = f"item_size:[5698242445 TO {OPEN}]"


# -- the two http stand-ins ----------------------------------------------


class Replay:
    """Answers exactly the captured requests and 404s everything else.

    404 rather than an empty 200, because an empty 200 is what a real
    outage sends and a stand-in that could not tell the difference would
    let a test pass on a plugin that had stopped asking anything.
    """

    def __init__(self, extra: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict[tuple[str, int], dict] = {
            (COLLECTION, 0): SCOPE_TOTAL,
            (f"{COLLECTION} AND NOT item_size:[* TO *]", 0): SCOPE_UNSIZED,
            (f"{COLLECTION} AND item_size:[0 TO 45000]", 0): SMALL_TOTAL,
            (f"{COLLECTION} AND item_size:[0 TO 45000]", PAGE_ROWS): SMALL_READ,
            (f"{COLLECTION} AND item_size:[5698242445 TO *]", 0): TAIL_TOTAL,
            (f"{COLLECTION} AND item_size:[5698242445 TO *]", PAGE_ROWS): TAIL_READ,
        }
        for unit_id, count in WINDOW_TOTALS["windows"].items():
            # The key *is* the clause, so this is the query verbatim.
            self.answers[(f"{COLLECTION} AND {unit_id}", 0)] = {
                "response": {"numFound": count, "docs": []}
            }
        self.answers.update(extra or {})

    def get(self, url, params=None):
        params = dict(params or {})
        self.calls.append((url, params))
        key = (str(params.get("q")), int(params.get("rows", 0)))
        body = self.answers.get(key)
        if url != SEARCH or body is None:
            return HttpResponse(status_code=404, text="not found")
        return HttpResponse(status_code=200, text=json.dumps(body))


_RANGE = re.compile(r"item_size:\[(\d+) TO (\d+|\*)\]")


class FakeArchive:
    """A filtering stand-in, for the shapes no single capture can hold.

    Splitting, a shrunken response, a byte count shared by more items than
    a response holds, and a collection that changes between the count and
    the read are all properties of a *sequence* of requests. They cannot be
    replayed from a capture, so they are driven here against real documents
    with the query honoured rather than matched.
    """

    def __init__(self, docs, *, rows_cap=None, drop_after_count=0, add_after_count=0):
        self.docs = list(docs)
        self.rows_cap = rows_cap
        self.drop_after_count = drop_after_count
        self.add_after_count = add_after_count
        self.counted = False
        self.calls: list[tuple[str, dict]] = []

    def _matching(self, q: str) -> list[dict]:
        match = _RANGE.search(q)
        low = int(match.group(1)) if match else 0
        high = (
            float("inf")
            if not match or match.group(2) == OPEN
            else int(match.group(2))
        )
        return sorted(
            (d for d in self.docs if low <= d["item_size"] <= high),
            key=lambda d: d["item_size"],
        )

    def get(self, url, params=None):
        params = dict(params or {})
        self.calls.append((url, params))
        rows = int(params.get("rows", 0))
        if self.rows_cap is not None and rows > self.rows_cap:
            # What a response over the host's 4 MiB budget looks like from
            # inside the plugin, and what makes `Index` halve `rows`.
            return HttpResponse(status_code=500, text="too large")

        docs = self._matching(str(params.get("q")))
        if rows == 0:
            self.counted = True
            return HttpResponse(
                status_code=200,
                text=json.dumps({"response": {"numFound": len(docs), "docs": []}}),
            )
        if self.counted and self.drop_after_count:
            docs = docs[: -self.drop_after_count]
        if self.counted and self.add_after_count:
            docs = docs + [
                {
                    "identifier": f"added_between_requests_{n}",
                    "title": f"Added Between Requests {n}",
                    "item_size": docs[-1]["item_size"],
                    "mediatype": "software",
                    "collection": ["softwarelibrary"],
                }
                for n in range(self.add_after_count)
            ]
            docs.sort(key=lambda d: d["item_size"])
        return HttpResponse(
            status_code=200,
            text=json.dumps(
                {"response": {"numFound": len(docs), "docs": docs[:rows]}}
            ),
        )


def make_census(http=None, config=None):
    http = http or Replay()
    return Census(PluginContext(config=config or {}, http=http)), http


def unit(unit_id: str) -> CensusUnit:
    return CensusUnit(unit_id=unit_id, label=unit_id, kind="roms")


# -- the partition, before a single request -------------------------------


def test_the_windows_tile_the_size_axis_with_no_gap_and_no_overlap():
    """The property the whole design rests on, checked without a network.

    A window that overlapped its neighbour by one byte would double-count
    every item of exactly that size; one that gapped by a byte would lose
    them. Both mistakes are invisible in the totals unless the tiling is
    asserted directly.
    """
    census, _ = make_census()
    spans = [parse_size_clause(u.unit_id) for u in census.scope()]

    assert spans[0][0] == 0, "the first window starts at zero bytes"
    assert spans[-1][1] == OPEN, (
        "the last window has no upper bound: an item bigger than a constant "
        "somebody guessed would otherwise be in no window at all"
    )
    for (low, high), (next_low, _) in zip(spans, spans[1:]):
        assert high + 1 == next_low, (
            f"window [{low}, {high}] and the one at {next_low} neither "
            f"overlap nor leave a gap"
        )


def test_the_captured_window_totals_sum_to_the_collections_own_count():
    """The completeness claim in its smallest form, on real numbers.

    Each window's total came from its own `rows=0` request; the
    collection's came from a separate one. Twenty-seven independent
    denominators against a twenty-eighth, and the sum has to be exact --
    an approximation here would be the flattering kind.
    """
    declared = SCOPE_TOTAL["response"]["numFound"]
    windows = WINDOW_TOTALS["windows"]

    assert len(windows) == len(SIZE_LADDER) == 27
    assert sum(windows.values()) == declared == 250_509

    census, _ = make_census()
    assert {u.unit_id for u in census.scope()} == set(windows), (
        "every window the plugin proposes is one that was counted, and "
        "every window that was counted is one the plugin proposes"
    )


def test_a_units_id_is_the_query_that_declares_it():
    census, http = make_census()
    tail = [u for u in census.scope() if u.unit_id == TAIL_WINDOW]
    assert tail, "the ladder ends on an unbounded window"

    census.enumerate(tail[0], None)
    counted = [
        p["q"] for _, p in http.calls if int(p.get("rows", 0)) == 0
    ]
    assert f"{COLLECTION} AND {TAIL_WINDOW}" in counted, (
        "pasting the unit id after the collection is literally the query "
        "whose numFound is that unit's declared total"
    )


# -- scope ----------------------------------------------------------------


def test_scope_costs_two_requests_and_neither_reads_a_document():
    census, http = make_census()
    units = census.scope()

    assert len(units) == 27
    assert [int(p.get("rows", 0)) for _, p in http.calls] == [0, 0], (
        "a scope failure fails the whole build, so it asks two counting "
        "questions and never enumerates"
    )
    assert all(u.declared_total is None for u in units)
    assert all(u.include and u.kind == "roms" for u in units)


def test_scope_names_the_number_the_windows_must_add_up_to():
    census, _ = make_census()
    labels = [u.label for u in census.scope()]
    assert all("250,509 items" in label for label in labels)
    assert "window 1 of 27" in labels[0]
    assert "unbounded" in labels[-1]


def test_scope_refuses_a_collection_holding_an_item_with_no_size():
    """The one way this design could be silently wrong, checked every build.

    An item with no `item_size` belongs to no window, so it would be
    missing from the catalogue *without ever appearing as a shortfall*.
    """
    http = Replay(
        {
            (f"{COLLECTION} AND NOT item_size:[* TO *]", 0): {
                "response": {"numFound": 3, "docs": []}
            }
        }
    )
    census, _ = make_census(http)

    with pytest.raises(CensusUnavailable) as exc:
        census.scope()
    assert "3 items" in str(exc.value)
    assert "no window" in str(exc.value)


def test_scope_refuses_a_query_that_matches_nothing():
    http = Replay({(COLLECTION, 0): {"response": {"numFound": 0, "docs": []}}})
    census, _ = make_census(http)

    with pytest.raises(CensusUnavailable) as exc:
        census.scope()
    assert "completeness claim about nothing" in str(exc.value)


def test_scope_refuses_a_service_that_will_not_answer_the_size_question():
    http = Replay()
    del http.answers[(f"{COLLECTION} AND NOT item_size:[* TO *]", 0)]
    census, _ = make_census(http)

    with pytest.raises(CensusUnavailable) as exc:
        census.scope()
    assert "no item_size" in str(exc.value)


# -- one window, over the real corpus -------------------------------------


def test_a_window_walks_to_exactly_its_declared_total():
    census, _ = make_census()
    page = census.enumerate(unit(SMALL_WINDOW), None)

    assert page.cursor is None, "776 items fit in one response"
    assert page.declared_total == 776 == SMALL_TOTAL["response"]["numFound"]
    assert len(page.records) + sum(page.skipped.values()) == 776


def test_a_sub_collection_listing_is_skipped_with_a_reason_not_dropped():
    """48 of the window's 776 rows are listings *of* items, not items.

    Cataloguing a listing as if it were a game is how a catalogue ends up
    reporting more entries than the collection holds. The whole collection
    holds 200 of them, and the live build skipped exactly 200.
    """
    census, _ = make_census()
    page = census.enumerate(unit(SMALL_WINDOW), None)

    assert page.skipped == {SKIP_SUB_COLLECTION: 48}
    assert len(page.records) == 728
    assert not any(
        r.extra.get("mediatype") == "collection" for r in page.records
    )
    # The same rows read straight from `sub_collections.json`, so the
    # thing being skipped is identifiable rather than merely counted.
    listings = {d["identifier"] for d in SUB_COLLECTIONS["response"]["docs"]}
    assert "fav-naira92" in listings
    assert not (listings & {r.record_id for r in page.records})


def test_the_unbounded_tail_window_is_read_like_any_other():
    census, _ = make_census()
    page = census.enumerate(unit(TAIL_WINDOW), None)

    assert page.declared_total == 946 == TAIL_TOTAL["response"]["numFound"]
    assert len(page.records) + sum(page.skipped.values()) == 946
    assert page.cursor is None


def test_items_of_every_mediatype_are_catalogued_not_only_software():
    """`movies` holds `msdos_Epidemic_1983`; `texts` holds a ZZT world.

    Archive.org's mediatype does not decide whether an item is a program,
    so filtering on it would drop real software to keep the census tidy.
    """
    census, _ = make_census()
    page = census.enumerate(unit(TAIL_WINDOW), None)
    kinds = {r.extra.get("mediatype") for r in page.records}

    assert {"web", "software", "image", "data", "texts", "movies"} <= kinds


def test_an_unmapped_emulator_is_catalogued_with_no_platform():
    """The established precedent: counted and searchable, never guessed at."""
    census, _ = make_census()
    page = census.enumerate(unit(TAIL_WINDOW), None)

    unmapped = [r for r in page.records if r.platform is None]
    assert len(unmapped) == 942, "938 carry no emulator at all, 4 an unmapped one"
    assert {r.platform for r in page.records if r.platform} == {"dos"}


def test_a_stream_only_item_says_so_rather_than_being_dropped():
    census, _ = make_census()
    page = census.enumerate(unit(SMALL_WINDOW), None)

    streamed = [r for r in page.records if r.extra.get("stream_only") == "1"]
    assert len(streamed) == 74
    assert all(r.url.startswith("https://archive.org/details/") for r in streamed)


def test_an_untitled_item_is_catalogued_under_its_identifier():
    """Eight items answer `NOT title:[* TO *]`. None of them is dropped.

    Four carry no `title` field at all and are catalogued under the
    identifier Archive.org files them under. The other four have a title
    that is only punctuation -- `)`, `-`, `???`, `_` -- which Solr
    tokenises to nothing but which is still what the uploader typed, so it
    is kept as it stands. An item with no name is still an item.
    """
    docs = UNTITLED["response"]["docs"]
    census, _ = make_census(FakeArchive(docs))
    page = census.enumerate(unit(f"item_size:[0 TO {OPEN}]"), None)
    titles = {r.record_id: r.title for r in page.records}

    assert len(page.records) == 8
    assert titles["c64__-"] == "c64__-"
    assert titles["simplicity_202105"] == "simplicity_202105"
    assert titles["demoscene_-Trybit"] == "???"
    assert all(t.strip() for t in titles.values())


def test_no_record_claims_a_digest_this_source_does_not_publish():
    """`advancedsearch.php` publishes no hash, so `grouping` gets none.

    Said as a test because a record carrying an empty or invented digest
    would make `rom_hub.grouping` merge on "evidence" that is not there.
    """
    census, _ = make_census()
    page = census.enumerate(unit(SMALL_WINDOW), None)

    for record in page.records:
        assert not {"sha256", "sha1", "md5", "crc32"} & set(record.extra)


# -- windows too big for one response -------------------------------------


def _tail_docs():
    return TAIL_READ["response"]["docs"]


def _made_up(sizes):
    return [
        {"identifier": f"i{n}", "title": f"T{n}", "item_size": size,
         "mediatype": "software", "collection": ["softwarelibrary"]}
        for n, size in enumerate(sizes)
    ]


#: `Index` halves `rows` five times from `PAGE_ROWS`, so the smallest read
#: it will ever attempt is 312 rows. A cap below that is not a shrunken
#: response, it is an unreachable service -- so the tests that want a
#: shrunken response cap at 400 and get a 312-row read.
SHRUNK = 400
SHRUNK_ROWS = 312


def _walk(census, window, cursor=None):
    """Every page of one window, and what they accounted for between them."""
    pages, kept, skipped, page = 0, 0, 0, None
    while True:
        page = census.enumerate(window, cursor)
        kept += len(page.records)
        skipped += sum(page.skipped.values())
        pages += 1
        cursor = page.cursor
        assert pages < 20, "the split has to terminate"
        if cursor is None:
            return pages, kept, skipped, page


def test_a_window_too_big_for_one_response_is_split_and_still_balances():
    census, _ = make_census(FakeArchive(_tail_docs(), rows_cap=SHRUNK))
    pages, kept, skipped, last = _walk(census, unit(f"item_size:[0 TO {OPEN}]"))

    assert pages > 1, f"946 items do not fit in a {SHRUNK_ROWS}-row response"
    assert kept + skipped == 946 == last.declared_total


def test_a_shrunken_read_is_not_mistaken_for_a_finished_window():
    """`Index` halves `rows` when a response will not fit.

    A plugin comparing what came back against `PAGE_ROWS` rather than
    against what the successful request actually asked for would call a
    halved read a finished window and lose everything above it -- silently,
    and with the arithmetic still balancing on the smaller number.
    """
    census, _ = make_census(FakeArchive(_tail_docs(), rows_cap=SHRUNK))

    page = census.enumerate(unit(f"item_size:[0 TO {OPEN}]"), None)
    assert page.cursor is not None, (
        f"{SHRUNK_ROWS} documents came back out of 946 because the response "
        f"was halved, and that is not the end of the window"
    )
    assert len(page.records) < 946


def test_a_split_never_cuts_through_a_shared_byte_count():
    """Documents at the boundary size go to the next page, all together.

    Taking the ones that fit and leaving the rest is the only way this
    partition could lose a row, so the boundary size is excluded from the
    page that reached it. Here the 312-row response ends inside a run of
    four items that all measure 4,096 bytes.
    """
    sizes = list(range(1, SHRUNK_ROWS)) + [4096] * 4 + [4097] * 3
    census, _ = make_census(FakeArchive(_made_up(sizes), rows_cap=SHRUNK))
    window = unit(f"item_size:[0 TO {OPEN}]")

    first = census.enumerate(window, None)
    assert len(first.records) == SHRUNK_ROWS - 1
    assert all(r.size_bytes < 4096 for r in first.records), (
        "the last row of the response is one of four at 4,096 bytes; all "
        "four wait for the next page"
    )
    assert first.cursor == f"4096|{SHRUNK_ROWS - 1}|{len(sizes)}"

    second = census.enumerate(window, first.cursor)
    assert [r.size_bytes for r in second.records] == [4096] * 4 + [4097] * 3
    assert second.cursor is None
    assert second.declared_total == len(sizes)


def test_a_byte_count_shared_by_more_items_than_fit_is_refused_by_name():
    docs = _made_up([4096] * (SHRUNK_ROWS + 20))
    census, _ = make_census(FakeArchive(docs, rows_cap=SHRUNK))

    with pytest.raises(CensusUnavailable) as exc:
        census.enumerate(unit(f"item_size:[4096 TO {OPEN}]"), None)
    assert "share item_size 4,096 exactly" in str(exc.value)
    assert "rather than truncating it" in str(exc.value)


# -- a collection that moves while it is read -----------------------------


def test_items_gone_between_the_count_and_the_read_are_named_not_lost():
    census, _ = make_census(FakeArchive(_tail_docs(), drop_after_count=5))
    page = census.enumerate(unit(f"item_size:[0 TO {OPEN}]"), None)

    assert page.declared_total == 946
    assert page.skipped[SKIP_VANISHED] == 5
    assert len(page.records) + sum(page.skipped.values()) == 946


def test_items_added_between_the_count_and_the_read_raise_the_total():
    census, _ = make_census(FakeArchive(_tail_docs(), add_after_count=3))
    page = census.enumerate(unit(f"item_size:[0 TO {OPEN}]"), None)

    assert page.declared_total == 949, (
        "the read is the service's more recent statement about the window, "
        "and the report compares against it"
    )
    assert SKIP_VANISHED not in page.skipped
    assert len(page.records) + sum(page.skipped.values()) == 949


# -- refusals -------------------------------------------------------------


def test_a_cursor_this_plugin_did_not_write_is_refused():
    census, _ = make_census()
    with pytest.raises(CensusUnavailable) as exc:
        census.enumerate(unit(SMALL_WINDOW), "page=2")
    assert "low|seen|declared" in str(exc.value)


def test_a_unit_id_that_is_not_a_size_window_is_refused():
    census, _ = make_census()
    with pytest.raises(CensusUnavailable) as exc:
        census.enumerate(unit("softwarelibrary_c64"), None)
    assert "not an item_size window" in str(exc.value)


def test_a_window_the_service_will_not_count_fails_loudly():
    http = Replay()
    del http.answers[(f"{COLLECTION} AND item_size:[0 TO 45000]", 0)]
    census, _ = make_census(http)

    with pytest.raises(CensusUnavailable) as exc:
        census.enumerate(unit(SMALL_WINDOW), None)
    assert "a walk that cannot be checked is a list, not a census" in str(exc.value)


def test_a_window_the_service_will_not_serve_fails_loudly():
    http = Replay()
    del http.answers[(f"{COLLECTION} AND item_size:[0 TO 45000]", PAGE_ROWS)]
    census, _ = make_census(http)

    with pytest.raises(CensusUnavailable):
        census.enumerate(unit(SMALL_WINDOW), None)


def test_a_slow_service_costs_one_unit_rather_than_the_subprocess():
    """The plugin stops before the host's 30 seconds do.

    `broker.host` enforces its budget by killing the subprocess, so a
    plugin that overruns costs a process restart on top of the unit. The
    first real build lost two windows of twenty-seven to exactly that, on
    a call that normally takes 2.3 seconds.
    """
    census, _ = make_census()
    # Already spent, which is what a service that answered slowly leaves
    # behind by the time the next request is due.
    census._index = lambda: Index(census.ctx.http, deadline=time.monotonic() - 1)

    with pytest.raises(CensusUnavailable) as exc:
        census.enumerate(unit(SMALL_WINDOW), None)
    assert "rather than overrun the host's call budget" in str(exc.value)


def test_the_lean_field_set_is_what_is_actually_asked_for():
    census, http = make_census()
    census.enumerate(unit(SMALL_WINDOW), None)
    reads = [p for _, p in http.calls if int(p.get("rows", 0)) > 0]

    assert reads and reads[0]["fl[]"] == list(CENSUS_FIELDS)
    assert reads[0]["sort[]"] == "item_size asc"
    assert "page" not in reads[0], (
        "advancedsearch.php refuses to page past 10,000 results, so a bulk "
        "read omits page entirely"
    )


# -- a build, end to end --------------------------------------------------


class TwoWindows:
    """A plugin process serving the two windows that were captured whole."""

    def __init__(self, census):
        self._census = census

    def census_scope(self):
        return [unit(SMALL_WINDOW), unit(TAIL_WINDOW)]

    def census_page(self, unit_, cursor):
        return self._census.enumerate(unit_, cursor)

    def close(self):
        pass


def test_a_build_over_two_real_windows_is_complete_and_says_so(tmp_path):
    census, _ = make_census()

    with Catalogue(tmp_path / "c.sqlite3") as catalogue:
        report = build(lambda: TwoWindows(census), catalogue, slug="archive-org")

    assert report.complete, report.headline()
    assert report.shortfall == 0
    assert report.declared_in_scope == 776 + 946
    assert report.accounted == 1722
    assert report.kept == 728 + 946
    assert report.skipped == {SKIP_SUB_COLLECTION: 48}
    assert "complete" in report.headline()
    assert "1,722 of 1,722 declared entries across 2 units" in report.headline()


def test_the_catalogue_answers_a_search_the_way_the_live_plugin_would(tmp_path):
    census, _ = make_census()

    with Catalogue(tmp_path / "c.sqlite3") as catalogue:
        build(lambda: TwoWindows(census), catalogue, slug="archive-org")
        dos = catalogue.results(platform="dos")
        rows = catalogue.results()

    assert dos and all(r.platform == "dos" for r in dos)
    groups = group_results(rows)
    assert 0 < len(groups) <= len(rows), (
        "grouping merges on the parsed name alone here -- this source "
        "publishes no digest at item level"
    )


def test_a_killed_build_resumes_the_window_rather_than_re_reading_it(tmp_path):
    """Every page is committed as it arrives, so a kill costs one page.

    This census states no declared total in `scope` -- 27 range counts is
    20 to 54 seconds and a scope that overruns kills the build rather than
    one unit -- and `rom_hub.census.Catalogue.begin` has to read that as
    "the plugin did not say" rather than as "the total moved". If it read
    it the other way the rows committed here would be thrown away on the
    next run and the resume machinery would do nothing at all.
    """
    census, http = make_census(FakeArchive(_tail_docs(), rows_cap=SHRUNK))
    window = unit(f"item_size:[0 TO {OPEN}]")

    class Serves:
        pages = 0
        first_cursor = "never asked"

        def census_scope(self):
            return [window]

        def census_page(self, unit_, cursor):
            if not Serves.pages:
                Serves.first_cursor = cursor
            Serves.pages += 1
            return census.enumerate(unit_, cursor)

        def close(self):
            pass

    # Two pages committed and then the process gone -- a kill between
    # pages, which is what leaves a unit `partial` rather than `failed`.
    path = tmp_path / "c.sqlite3"
    with Catalogue(path) as catalogue:
        catalogue.begin("archive-org", [window])
        cursor = None
        for _ in range(2):
            page = census.enumerate(window, cursor)
            catalogue.add_page(window.unit_id, page)
            cursor = page.cursor
        assert cursor is not None
        killed = catalogue.report().units[0]
    assert killed.state == "partial"
    assert 0 < killed.kept < 946

    with Catalogue(path) as catalogue:
        second = build(lambda: Serves(), catalogue, slug="archive-org")

    unit_after = second.units[0]
    assert unit_after.kept + sum(unit_after.skipped.values()) == 946
    assert unit_after.state == "done"
    assert unit_after.kept > killed.kept
    assert Serves.first_cursor == cursor, (
        "the second run picked up the byte count the first one stopped at, "
        "rather than starting again from zero"
    )
    assert Serves.pages == 2, (
        "946 items take four pages at this response size; two of them were "
        "already committed and are not read again"
    )


def test_the_ladder_is_ordered_and_has_no_repeats():
    assert list(SIZE_LADDER) == sorted(SIZE_LADDER)
    assert len(set(SIZE_LADDER)) == len(SIZE_LADDER)
    assert SIZE_LADDER[0] == 0
