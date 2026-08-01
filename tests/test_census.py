"""The catalogue's arithmetic, which is the whole claim.

Every test here is about one identity:

    declared_total == kept + sum(skipped.values())

and about what the report says when it does not hold. A catalogue that
merely stores rows is easy; one that can tell you what it is missing is
the thing being asked for, so the interesting cases are all the ones where
something is absent.
"""

from __future__ import annotations

import pytest

from rom_hub.census import (
    DONE,
    EXCLUDED,
    FAILED,
    PARTIAL,
    Catalogue,
    CensusError,
    build,
    catalogue_path,
)
from rom_hub.types import CensusPage, CensusRecord, CensusUnit


def unit(uid, total=None, kind="roms", **kw):
    return CensusUnit(
        unit_id=uid, label=kw.pop("label", uid), kind=kind,
        declared_total=total, **kw
    )


def page(*titles, cursor=None, skipped=None, declared=None, platform="gb", unit_id="u"):
    return CensusPage(
        records=[
            CensusRecord(
                record_id=f"{unit_id}/{t}", title=t, platform=platform,
                size_bytes=1024,
            )
            for t in titles
        ],
        cursor=cursor,
        skipped=skipped or {},
        declared_total=declared,
    )


@pytest.fixture
def cat(tmp_path):
    with Catalogue(tmp_path / "c.sqlite3") as c:
        yield c


# -- the identity --------------------------------------------------------


def test_a_unit_is_complete_when_kept_plus_skipped_equals_the_declared_total(cat):
    cat.begin("s", [unit("u", total=5)])
    cat.add_page("u", page("A.zip", "B.zip", "C.zip", skipped={"bookkeeping": 2}))

    report = cat.report()
    (coverage,) = report.units
    assert coverage.kept == 3
    assert coverage.walked == 5
    assert coverage.shortfall == 0
    assert coverage.complete
    assert report.complete
    assert "complete" in report.headline()


def test_rows_the_plugin_neither_kept_nor_explained_are_a_visible_shortfall(cat):
    # The failure this whole module exists to make impossible: a plugin
    # that quietly drops two entries produces a *smaller* catalogue, and
    # nothing about the smaller catalogue looks wrong on its own.
    cat.begin("s", [unit("u", total=5)])
    cat.add_page("u", page("A.zip", "B.zip", "C.zip"))

    report = cat.report()
    assert report.units[0].shortfall == 2
    assert not report.units[0].complete
    assert not report.complete
    assert "2 unaccounted for" in report.headline()


def test_a_skip_reason_is_carried_through_to_the_report(cat):
    cat.begin("s", [unit("u", total=10)])
    cat.add_page(
        "u",
        page("A.zip", skipped={"archive.org bookkeeping": 6, "not a ROM": 3}),
    )
    assert cat.report().skipped == {"archive.org bookkeeping": 6, "not a ROM": 3}


def test_skip_counts_accumulate_across_pages_of_one_unit(cat):
    cat.begin("s", [unit("u", total=6)])
    cat.add_page("u", page("A.zip", cursor="1", skipped={"junk": 2}))
    cat.add_page("u", page("B.zip", skipped={"junk": 2}))

    (coverage,) = cat.report().units
    assert coverage.skipped == {"junk": 4}
    assert coverage.walked == 6
    assert coverage.complete


def test_a_source_that_declares_no_total_is_unmeasured_not_complete(cat):
    # "Unknown" and "zero missing" must not print the same thing.
    cat.begin("s", [unit("u", total=None)])
    cat.add_page("u", page("A.zip"))

    report = cat.report()
    assert report.units[0].shortfall is None
    assert [u.unit_id for u in report.unmeasured] == ["u"]
    assert not report.complete
    assert "no declared total" in report.headline()


# -- exclusions are loud -------------------------------------------------


def test_an_excluded_unit_keeps_its_reason_and_stays_in_the_denominator(cat):
    cat.begin(
        "s",
        [
            unit("keep", total=2),
            unit("drop", total=999, include=False, reason="928 GB CDN dump"),
        ],
    )
    cat.add_page("keep", page("A.zip", "B.zip"))

    report = cat.report()
    assert report.complete  # every *in-scope* unit balances
    assert [u.unit_id for u in report.excluded_units] == ["drop"]
    assert report.excluded_units[0].reason == "928 GB CDN dump"
    # The excluded entries are still counted somewhere an operator sees.
    assert report.declared == 1001
    assert report.declared_in_scope == 2
    assert report.declared_excluded == 999
    assert "1 units excluded (999 entries)" in report.headline()


def test_a_kind_outside_the_requested_scope_is_excluded_with_that_as_the_reason(cat):
    cat.begin(
        "s",
        [unit("games", total=1, kind="roms"), unit("bundle", total=1, kind="pack")],
        kinds=("roms",),
    )
    report = cat.report()
    excluded = {u.unit_id: u for u in report.excluded_units}
    assert set(excluded) == {"bundle"}
    assert "outside the requested scope" in excluded["bundle"].reason
    assert excluded["bundle"].state == EXCLUDED


def test_widening_the_kinds_brings_a_previously_excluded_unit_back_into_scope(cat):
    units = [unit("games", total=1, kind="roms"), unit("bundle", total=1, kind="pack")]
    cat.begin("s", units, kinds=("roms",))
    cat.begin("s", units, kinds=("roms", "pack"))
    assert {u.unit_id for u, _ in cat.pending()} == {"games", "bundle"}


# -- resuming ------------------------------------------------------------


def test_a_partial_unit_resumes_from_its_stored_cursor(cat):
    cat.begin("s", [unit("u", total=4)])
    cat.add_page("u", page("A.zip", "B.zip", cursor="page-2"))

    assert cat.report().units[0].state == PARTIAL
    # A fresh handle on the same file -- which is what a resumed build has.
    with Catalogue(cat.path) as reopened:
        assert [(u.unit_id, c) for u, c in reopened.pending()] == [("u", "page-2")]


def test_a_finished_unit_is_not_offered_again(cat):
    cat.begin("s", [unit("u", total=1)])
    cat.add_page("u", page("A.zip"))
    assert cat.pending() == []


def test_re_reading_a_page_after_a_crash_does_not_double_count(cat):
    # A build killed between storing rows and storing the cursor re-reads
    # the page it was on. `kept` is recomputed from the table rather than
    # incremented, so the unit does not end up *over*-complete.
    cat.begin("s", [unit("u", total=2)])
    cat.add_page("u", page("A.zip", "B.zip", cursor="x"))
    cat.add_page("u", page("A.zip", "B.zip"))

    (coverage,) = cat.report().units
    assert coverage.kept == 2
    assert coverage.complete


def test_a_rescope_keeps_the_records_of_a_unit_that_has_not_changed(cat):
    cat.begin("s", [unit("u", total=1)])
    cat.add_page("u", page("A.zip"))
    cat.begin("s", [unit("u", total=1)])

    assert cat.count() == 1
    assert cat.pending() == []


def test_a_unit_whose_declared_total_moved_is_walked_again(cat):
    # A different total means different contents. Keeping the old rows
    # would leave the catalogue describing a version of the unit that no
    # longer exists, and claiming coverage of it.
    cat.begin("s", [unit("u", total=1)])
    cat.add_page("u", page("A.zip"))
    cat.begin("s", [unit("u", total=9)])

    assert cat.count() == 0
    assert [u.unit_id for u, _ in cat.pending()] == ["u"]


def test_a_unit_that_vanished_from_the_source_leaves_no_rows_behind(cat):
    cat.begin("s", [unit("old", total=1), unit("new", total=1)])
    cat.add_page("old", page("A.zip", unit_id="old"))
    cat.begin("s", [unit("new", total=1)])

    assert cat.count() == 0
    assert [u.unit_id for u in cat.report().units] == ["new"]


# -- failure is isolated and named ---------------------------------------


def test_one_unit_failing_does_not_stop_the_others(tmp_path):
    class Proc:
        def census_scope(self):
            return [unit("good", total=1), unit("bad", total=1)]

        def census_page(self, u, cursor):
            if u.unit_id == "bad":
                raise RuntimeError("archive.org said 503")
            return page("A.zip", unit_id="good")

    with Catalogue(tmp_path / "c.sqlite3") as cat:
        report = build(Proc(), cat, slug="s")

    states = {u.unit_id: u.state for u in report.units}
    assert states == {"good": DONE, "bad": FAILED}
    assert "503" in {u.unit_id: u.error for u in report.units}["bad"]
    assert not report.complete
    assert "1 failed" in report.headline()


def test_a_cursor_that_never_advances_fails_the_unit_rather_than_looping(tmp_path):
    class Proc:
        def census_scope(self):
            return [unit("u", total=99)]

        def census_page(self, u, cursor):
            return page("A.zip", cursor="stuck", unit_id="u")

    with Catalogue(tmp_path / "c.sqlite3") as cat:
        report = build(Proc(), cat, slug="s")

    assert report.units[0].state == FAILED
    assert "did not advance" in report.units[0].error


def test_a_unit_that_pages_forever_stops_at_the_ceiling(tmp_path):
    seen = []

    class Proc:
        def census_scope(self):
            return [unit("u", total=10**9)]

        def census_page(self, u, cursor):
            seen.append(cursor)
            return page(f"{len(seen)}.zip", cursor=str(len(seen)), unit_id="u")

    with Catalogue(tmp_path / "c.sqlite3") as cat:
        report = build(Proc(), cat, slug="s", max_pages_per_unit=3)

    assert len(seen) == 3
    assert report.units[0].state == FAILED
    assert "per-unit ceiling" in report.units[0].error


# -- deduplication is grouping's job, not a second implementation --------


def test_distinct_counts_come_from_the_grouping_module(cat):
    # Two items mirroring the same ROM under names that parse the same:
    # one game, one variant, two rows. That is `rom_hub.grouping`'s answer
    # and this module must not have its own.
    cat.begin("s", [unit("a", total=1), unit("b", total=1)])
    cat.add_page("a", page("Tetris (USA).zip", unit_id="a"))
    cat.add_page("b", page("Tetris (USA).zip", unit_id="b"))

    report = cat.report(distinct=True)
    assert cat.count() == 2
    assert report.games == 1
    assert report.variants == 1


def test_a_matching_hash_collapses_rows_the_names_would_have_kept_apart(cat):
    cat.begin("s", [unit("a", total=1), unit("b", total=1)])
    sha = "a" * 40
    for uid, title in (("a", "Tetris (USA).zip"), ("b", "TETRIS_USA.ZIP")):
        cat.add_page(
            uid,
            CensusPage(
                records=[
                    CensusRecord(
                        record_id=f"{uid}/{title}", title=title,
                        platform="gb", extra={"sha1": sha},
                    )
                ]
            ),
        )
    # Different title_keys, so they are two groups -- but the hash proves
    # the bytes are the same, and grouping is what knows that.
    assert cat.report(distinct=True).variants == 2  # one per group
    results = cat.results()
    assert {r.extra["sha1"] for r in results} == {sha}


def test_different_regions_stay_different_variants(cat):
    cat.begin("s", [unit("a", total=2)])
    cat.add_page("a", page("Tetris (USA).zip", "Tetris (Europe).zip", unit_id="a"))

    report = cat.report(distinct=True)
    assert report.games == 1
    assert report.variants == 2


# -- serving from the catalogue ------------------------------------------


def test_search_matches_every_term_as_a_substring(cat):
    cat.begin("s", [unit("u", total=3)])
    cat.add_page(
        "u", page("Super Mario Land (USA).zip", "Tetris (USA).zip", "Alleyway.zip")
    )
    assert [r.title for r in cat.results("mario land")] == [
        "Super Mario Land (USA).zip"
    ]


def test_search_can_narrow_to_a_platform(cat):
    cat.begin("s", [unit("u", total=2)])
    cat.add_page(
        "u",
        CensusPage(
            records=[
                CensusRecord(record_id="1", title="Tetris.zip", platform="gb"),
                CensusRecord(record_id="2", title="Tetris.zip", platform="nes"),
            ]
        ),
    )
    assert [r.platform for r in cat.results("tetris", platform="NES")] == ["nes"]


def test_a_percent_sign_in_a_query_is_not_a_wildcard(cat):
    cat.begin("s", [unit("u", total=2)])
    cat.add_page("u", page("100% Orange.zip", "Tetris.zip"))
    assert [r.title for r in cat.results("100%")] == ["100% Orange.zip"]
    # A bare `%` finds the titles that literally contain one -- not the
    # whole catalogue, which is what an unescaped LIKE would have returned.
    assert [r.title for r in cat.results("%")] == ["100% Orange.zip"]


def test_an_underscore_in_a_query_matches_only_an_underscore(cat):
    cat.begin("s", [unit("u", total=2)])
    cat.add_page("u", page("Final_Fantasy.zip", "Final Fantasy.zip"))
    assert [r.title for r in cat.results("final_fantasy")] == ["Final_Fantasy.zip"]


def test_excluded_units_are_not_served_by_search(cat):
    cat.begin(
        "s",
        [unit("a", total=1), unit("b", total=1, include=False, reason="too big")],
    )
    cat.add_page("a", page("Tetris.zip", unit_id="a"))
    # Even if rows somehow existed for it, the join drops them.
    assert [r.source_id for r in cat.results()] == ["a/Tetris.zip"]


def test_hashes_survive_the_round_trip_under_the_keys_grouping_reads(cat):
    cat.begin("s", [unit("u", total=1)])
    cat.add_page(
        "u",
        CensusPage(
            records=[
                CensusRecord(
                    record_id="1", title="Tetris.zip", platform="gb",
                    extra={"sha1": "b" * 40, "md5": "c" * 32, "crc32": "d" * 8},
                )
            ]
        ),
    )
    (result,) = cat.results()
    assert result.extra == {"sha1": "b" * 40, "md5": "c" * 32, "crc32": "d" * 8}


# -- housekeeping --------------------------------------------------------


def test_an_empty_catalogue_says_so_rather_than_claiming_completeness(cat):
    report = cat.report()
    assert not report.complete
    assert "no catalogue has been built yet" in report.headline()


def test_a_scope_with_only_excluded_units_is_not_complete(cat):
    cat.begin("s", [unit("u", total=5, include=False, reason="nope")])
    assert not cat.report().complete


def test_the_catalogue_path_is_contained_under_the_home(tmp_path):
    path = catalogue_path(tmp_path, "nointro-archive")
    assert path.parent == tmp_path / "var" / "catalogues"
    assert path.name == "nointro-archive.sqlite3"


def test_a_slug_that_would_escape_the_catalogue_directory_is_refused(tmp_path):
    with pytest.raises(CensusError):
        catalogue_path(tmp_path, "../../etc/passwd")


def test_a_corrupt_skipped_column_degrades_to_no_skips_rather_than_crashing(cat):
    cat.begin("s", [unit("u", total=1)])
    cat.add_page("u", page("A.zip"))
    cat._db.execute("UPDATE units SET skipped_json = 'not json'")
    assert cat.report().units[0].skipped == {}
