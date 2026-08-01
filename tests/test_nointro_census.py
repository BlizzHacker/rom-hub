"""The No-Intro census, replayed against captured Archive.org responses.

`tests/fixtures/nointro_archive/` gains five verbatim captures, all taken
on 2026-08-01:

* `census_scope.json` — the **whole** `advancedsearch.php` answer to
  `identifier:nointro*`: all 71 items with the `files_count` and
  `item_size` the classifier reads. Kept entire rather than sampled,
  because the interesting property of the classifier is how it behaves
  across the real distribution — a trimmed fixture would only prove it
  handles the examples somebody chose for it.
* `census_meta_nointro_sg.json` — the metadata for a nine-file item, the
  smallest real set in the corpus.
* `census_meta_nointro_casio_loopy_pv_1000.json` — twenty-eight files
  across two machine subdirectories, which is the shape that makes
  platform a per-*record* fact rather than a per-item one.
* `census_meta_NoIntro_VirtualBoy.json` and
  `census_meta_NoIntroVirtualBoy.json` — two separate uploads of the same
  Virtual Boy set. They are here to prove the deduplication story on real
  bytes: 31 of the 35 files in each carry identical sha1s.

Every one is unmodified except for dropping fields the plugin never reads,
so `files_count` still equals `len(files)` in each — which is the identity
the whole census rests on and would be worthless if a fixture had been
tidied into agreement.

No test opens a socket.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "nointro-archive"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nointro_archive"
sys.path.insert(0, str(PLUGIN_ROOT))

from nointro_archive.census import (  # noqa: E402
    MAX_FILES_PER_REQUEST,
    SKIP_BOOKKEEPING,
    Census,
    CensusUnavailable,
)
from nointro_archive.classify import classify  # noqa: E402

from rom_hub.census import Catalogue, build  # noqa: E402
from rom_hub.grouping import group_results  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

SCOPE = json.loads((FIXTURES / "census_scope.json").read_text(encoding="utf-8"))
META = {
    "nointro.sg": "census_meta_nointro_sg.json",
    "nointro-casio-loopy-pv-1000": "census_meta_nointro_casio_loopy_pv_1000.json",
    "NoIntro_VirtualBoy": "census_meta_NoIntro_VirtualBoy.json",
    "NoIntroVirtualBoy": "census_meta_NoIntroVirtualBoy.json",
}
METADATA = {
    identifier: json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    for identifier, name in META.items()
}

SEARCH = "https://archive.org/advancedsearch.php"


class FakeHttp:
    """Replays the captures, and refuses anything it was not given.

    An unknown URL answers 404 rather than an empty 200, because an empty
    200 is what a real dead mirror sends and a test that could not tell the
    difference would be no test at all.
    """

    def __init__(self, *, scope=SCOPE, metadata=None, status=200):
        self.scope = scope
        self.metadata = METADATA if metadata is None else metadata
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if url == SEARCH:
            page = int((params or {}).get("page", 1))
            docs = self.scope["response"]["docs"]
            rows = int((params or {}).get("rows", 500))
            window = docs[(page - 1) * rows : page * rows]
            body = {
                "response": {
                    "numFound": self.scope["response"]["numFound"],
                    "docs": window,
                }
            }
            return HttpResponse(status_code=self.status, text=json.dumps(body))
        prefix = "https://archive.org/metadata/"
        if url.startswith(prefix):
            identifier = url[len(prefix) :]
            if identifier in self.metadata:
                return HttpResponse(
                    status_code=self.status,
                    text=json.dumps(self.metadata[identifier]),
                )
        return HttpResponse(status_code=404, text="not found")


def make_census(http=None, config=None):
    http = http or FakeHttp()
    return Census(PluginContext(config=config or {}, http=http)), http


# -- the scope, over the real corpus -------------------------------------


def test_the_scope_is_every_item_the_search_index_returns():
    census, http = make_census()
    units = census.scope()

    assert len(units) == 71, "the whole `identifier:nointro*` result set"
    assert len({u.unit_id for u in units}) == 71
    # One request, because 71 fits in one page of 500.
    assert [url for url, _ in http.calls] == [SEARCH]


def test_the_declared_total_is_archive_orgs_own_files_count():
    census, _ = make_census()
    units = {u.unit_id: u for u in census.scope()}

    # Spot-checked against the live item on 2026-08-01. These are the
    # numbers the completeness claim is measured against, so they are
    # asserted literally rather than derived from the fixture.
    assert units["nointro.gg"].declared_total == 825
    assert units["nointro.md"].declared_total == 2779
    assert units["NoIntro-commodore-64_202302"].declared_total == 3682
    assert units["nointro_wiiu_cdn_nov_2020_2"].declared_total == 79


def test_every_item_in_the_corpus_is_classified_and_none_is_lost():
    census, _ = make_census()
    units = census.scope()

    kinds: dict[str, int] = {}
    declared: dict[str, int] = {}
    for unit in units:
        kinds[unit.kind] = kinds.get(unit.kind, 0) + 1
        declared[unit.kind] = declared.get(unit.kind, 0) + (unit.declared_total or 0)

    assert kinds == {"roms": 43, "pack": 11, "cdn-dump": 4, "media": 3, "other": 10}
    # 31,366 declared entries, every one of them inside some kind. The
    # denominator of the widest possible claim about this source.
    assert sum(declared.values()) == 31366
    assert declared["roms"] == 29955


def test_the_enormous_items_are_excluded_and_each_says_why():
    census, _ = make_census()
    excluded = {u.unit_id: u for u in census.scope() if not u.include}

    # The four the brief names, and nothing else, at cdn-dump scale.
    cdn = {u.unit_id for u in excluded.values() if u.kind == "cdn-dump"}
    assert cdn == {
        "nointro_wiiu_cdn_nov_2020",
        "nointro_wiiu_cdn_nov_2020_2",
        "nointro-sony-playstation-vita-PSVgameSD",
        "nointro-sony-playstation-vita-PSVgameSD-supplement",
    }
    # Excluding it is fine. Excluding it silently is not.
    for unit in excluded.values():
        assert unit.reason.strip(), unit.unit_id
    assert "distribution tree" in excluded["nointro_wiiu_cdn_nov_2020_2"].reason


def test_the_bulk_packs_are_told_apart_from_the_directories_of_games():
    census, _ = make_census()
    kinds = {u.unit_id: u.kind for u in census.scope()}

    # Archives-of-archives: each file is a whole machine's set.
    assert kinds["NoIntroROMsCollection"] == "pack"
    assert kinds["NoIntroPack2019Dec01MinusDS"] == "pack"
    assert kinds["nointro-merged"] == "pack"
    assert kinds["NoIntro_Atari"] == "pack"
    # And the one that stops this being a single threshold: 322 files
    # averaging 24 MB, which really are individual PC games.
    assert kinds["NoIntroIBMPc"] == "roms"


def test_soundtracks_are_not_counted_as_a_rom_set():
    census, _ = make_census()
    kinds = {u.unit_id: u.kind for u in census.scope()}
    assert kinds["NoIntroUnofficialVideoGame"] == "media"
    assert kinds["nointrosnaps"] == "media"
    # `image` is NOT a media mediatype: this one is a real ROM set.
    assert kinds["nointro.gbamultiboot"] == "roms"


def test_a_scope_query_that_matches_nothing_is_a_refusal_not_an_empty_census():
    """Cataloguing zero of zero would be a completeness claim about nothing."""
    census, _ = make_census(FakeHttp(scope={"response": {"numFound": 0, "docs": []}}))
    with pytest.raises(CensusUnavailable, match="matched nothing"):
        census.scope()


# -- enumerating one item ------------------------------------------------


def test_an_item_walks_to_exactly_its_declared_total():
    census, _ = make_census()
    unit = next(u for u in census.scope() if u.unit_id == "nointro.sg")
    page = census.enumerate(unit, None)

    # The identity, on a real capture: 9 declared = 5 kept + 4 skipped.
    assert page.declared_total == 9
    assert len(page.records) == 5
    assert page.skipped == {SKIP_BOOKKEEPING: 4}
    assert page.walked == page.declared_total
    assert page.cursor is None


def test_bookkeeping_is_skipped_with_a_reason_rather_than_dropped():
    census, _ = make_census()
    unit = next(u for u in census.scope() if u.unit_id == "nointro.sg")
    page = census.enumerate(unit, None)

    assert not any(r.title.endswith("_meta.xml") for r in page.records)
    assert sum(page.skipped.values()) == 4
    assert "bookkeeping" in next(iter(page.skipped))


def test_a_records_platform_comes_from_its_own_subdirectory():
    census, _ = make_census()
    unit = next(
        u for u in census.scope() if u.unit_id == "nointro-casio-loopy-pv-1000"
    )
    page = census.enumerate(unit, None)

    directories = {r.extra["directory"] for r in page.records}
    assert directories == {
        "nointro-casio-loopy-pv-1000/Casio - Loopy",
        "nointro-casio-loopy-pv-1000/Casio - PV-1000",
    }
    # Neither is in the platform table, so neither is guessed at.
    assert all(r.platform is None for r in page.records)
    assert page.walked == page.declared_total == 28


def test_an_unmapped_directory_is_catalogued_rather_than_skipped():
    """The file exists whether or not this plugin knows the machine.

    Dropping it would make the census understate the source in order to
    keep its own bookkeeping tidy, which is the opposite of the point.
    """
    census, _ = make_census()
    unit = next(
        u for u in census.scope() if u.unit_id == "nointro-casio-loopy-pv-1000"
    )
    page = census.enumerate(unit, None)
    assert len(page.records) == 24
    assert page.skipped == {SKIP_BOOKKEEPING: 4}


def test_every_record_carries_the_digests_grouping_reads():
    census, _ = make_census()
    unit = next(u for u in census.scope() if u.unit_id == "nointro.sg")
    page = census.enumerate(unit, None)

    for record in page.records:
        assert len(record.extra["sha1"]) == 40
        assert len(record.extra["md5"]) == 32
        assert len(record.extra["crc32"]) == 8
        assert record.extra["sha1"] == record.extra["sha1"].lower()


def test_a_record_url_points_at_the_file_inside_its_subdirectory():
    census, _ = make_census()
    unit = next(
        u for u in census.scope() if u.unit_id == "nointro-casio-loopy-pv-1000"
    )
    record = next(
        r for r in census.enumerate(unit, None).records
        if r.extra["directory"].endswith("Casio - Loopy")
    )
    assert record.url.startswith(
        "https://archive.org/download/nointro-casio-loopy-pv-1000/Casio - Loopy/"
    )
    assert record.record_id.startswith("nointro-casio-loopy-pv-1000/Casio - Loopy/")


def test_an_item_too_large_for_one_response_is_refused_by_name():
    """The metadata endpoint does not slice, so an item is one request or
    none. Truncating it would silently understate that item's coverage."""
    census, _ = make_census()
    unit = next(u for u in census.scope() if u.unit_id == "nointro.sg")
    huge = unit.model_copy(update={"declared_total": MAX_FILES_PER_REQUEST + 1})
    with pytest.raises(CensusUnavailable, match="cannot be paged"):
        census.enumerate(huge, None)


def test_an_item_the_service_will_not_serve_fails_loudly():
    census, _ = make_census(FakeHttp(metadata={}))
    unit = next(u for u in census.scope() if u.unit_id == "nointro.sg")
    with pytest.raises(CensusUnavailable, match="HTTP 404"):
        census.enumerate(unit, None)


# -- end to end, through the host's driver -------------------------------


class OneProcess:
    """The host's side of the wire, without a subprocess.

    `rom_hub.census.build` only ever calls these two methods, and both go
    through the same pydantic types the broker re-validates with -- so this
    exercises the real contract while staying offline.
    """

    def __init__(self, census):
        self._census = census

    def census_scope(self):
        return self._census.scope()

    def census_page(self, unit, cursor):
        return self._census.enumerate(unit, cursor)


def test_a_build_over_the_captured_items_is_complete_and_says_so(tmp_path):
    census, _ = make_census()
    only = set(META)

    class Scoped(OneProcess):
        def census_scope(self):
            # The fixtures carry metadata for four items; the rest would
            # 404. Narrowing the scope is what a `--only` would do, and it
            # keeps the identity under test rather than the fixture set.
            return [u for u in self._census.scope() if u.unit_id in only]

    with Catalogue(tmp_path / "c.sqlite3") as catalogue:
        report = build(Scoped(census), catalogue, slug="nointro-archive")

    assert report.complete, report.headline()
    assert report.shortfall == 0
    # 9 + 28 + 35 + 35 = 107 declared entries, all accounted for.
    assert report.declared_in_scope == 107
    assert report.accounted == 107
    assert report.kept == 5 + 24 + 31 + 31
    assert report.skipped == {SKIP_BOOKKEEPING: 16}


def test_two_uploads_of_one_set_collapse_on_hash_evidence(tmp_path):
    """The dedup claim, on real bytes rather than a constructed example.

    `NoIntro_VirtualBoy` and `NoIntroVirtualBoy` are separate Archive.org
    items. 62 rows go in; `rom_hub.grouping` — the one deduplicator in this
    codebase — is what decides how many distinct dumps that is.
    """
    census, _ = make_census()
    both = {"NoIntro_VirtualBoy", "NoIntroVirtualBoy"}

    class Scoped(OneProcess):
        def census_scope(self):
            return [u for u in self._census.scope() if u.unit_id in both]

    with Catalogue(tmp_path / "c.sqlite3") as catalogue:
        report = build(Scoped(census), catalogue, slug="nointro-archive")
        rows = catalogue.results()

    assert report.complete
    assert len(rows) == 62, "every row is kept; nothing is discarded"

    # The two items publish 31 identical sha1s between them, so at most 31
    # of those 62 rows can be distinct dumps.
    shared = len({r.extra["sha1"] for r in rows})
    assert shared == 31
    assert report.variants <= 31 < len(rows)


def test_the_catalogue_serves_a_search_for_a_platform_it_maps(tmp_path):
    census, _ = make_census()

    class Scoped(OneProcess):
        def census_scope(self):
            return [u for u in self._census.scope() if u.unit_id in META]

    with Catalogue(tmp_path / "c.sqlite3") as catalogue:
        build(Scoped(census), catalogue, slug="nointro-archive")
        # `NoIntroVirtualBoy` maps to `virtualboy`; the Casio item does not
        # map at all, and its rows are catalogued with no platform.
        vb = catalogue.results(platform="virtualboy")
        groups = group_results(catalogue.results("tetris"))

    assert vb, "the Virtual Boy set is reachable by platform"
    assert all(r.platform == "virtualboy" for r in vb)
    assert groups and "tetris" in groups[0].title_key


# -- the classifier on its own -------------------------------------------


def test_classify_puts_a_huge_item_beyond_argument():
    assert classify("software", 79, 928 * 1024**3) == "cdn-dump"


def test_classify_needs_both_signals_between_the_thresholds():
    # 24 MB average: a pack if there are a dozen files, a set if 300.
    twelve = 12 * 24 * 1024**2
    assert classify("software", 12, twelve) == "pack"
    assert classify("software", 320, 320 * 24 * 1024**2) == "roms"


def test_classify_calls_a_two_file_item_neither_a_set_nor_a_pack():
    assert classify("texts", 6, 2 * 1024**2) == "other"


def test_classify_survives_an_item_that_declares_nothing():
    assert classify(None, 0, 0) == "other"
    assert classify("", 0, 0) == "other"
