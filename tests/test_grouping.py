"""Merging, and refusing to merge.

The cases split three ways, which is how the module is designed:

* **cross-variant** -- one source, many regions/revisions of one game;
* **cross-source** -- many sources, one ROM;
* **the near-misses** -- pairs that look mergeable and must not be.

Everything is offline. Nothing here starts a subprocess or opens a socket:
grouping is a pure function of a list of `SearchResult`s.
"""

import time

import pytest

from rom_hub.dispatcher import (
    MAX_FANOUT,
    MIN_FANOUT,
    PluginStatus,
    SearchOutcome,
    fanout_limit,
    search_all,
)
from rom_hub.grouping import group_results, paginate, platform_key
from rom_hub.types import SearchResult


def r(title, plugin="src-a", platform="genesis", size=None, **extra):
    return SearchResult(
        source_id=f"{plugin}:{title}",
        title=title,
        platform=platform,
        size_bytes=size,
        plugin=plugin,
        extra={k: str(v) for k, v in extra.items()},
    )


def one(groups):
    assert len(groups) == 1, [g.title for g in groups]
    return groups[0]


# --- cross-variant ------------------------------------------------------


def test_the_batman_returns_shelf_collapses_to_one_row():
    """The reported symptom, verbatim.

    A real Game Gear shelf shows *Batman Returns* eight times. Every row
    is a genuinely distinct ROM, and the listing still reads as broken.
    """
    titles = [
        "Batman Returns (USA)",
        "Batman Returns (Europe)",
        "Batman Returns (Japan)",
        "Batman Returns (USA) (Rev 1)",
        "Batman Returns (Europe) (Rev 1)",
        "Batman Returns (USA) [!]",
        "Batman Returns (Europe) [b1]",
        "Batman Returns (World) (Beta)",
    ]
    groups = group_results([r(t) for t in titles], "batman returns")
    group = one(groups)
    assert group.title == "Batman Returns"
    assert group.variant_count == 8
    # Collapsed for reading, not for reaching: every row is still in there.
    assert group.result_count == 8
    assert sorted(x.title for x in group.results) == sorted(titles)


def test_one_game_per_platform_not_one_per_region():
    results = [
        r("Prince of Persia (USA)", platform="genesis"),
        r("Prince of Persia (Europe)", platform="genesis"),
        r("Prince of Persia (USA) (Rev 1)", platform="genesis"),
        r("Prince of Persia (USA)", platform="gb"),
        r("Prince of Persia (Japan)", platform="gb"),
    ]
    groups = group_results(results, "prince of persia")
    assert [(g.platform, g.variant_count) for g in groups] == [
        ("gb", 2),
        ("genesis", 3),
    ]


def test_a_platform_nobody_stated_is_never_merged_into_one_that_was():
    """"Unknown" is not evidence. Guessing the console is how a library
    ends up wrong in a way nobody can see."""
    groups = group_results(
        [r("Columns (USA)", platform="genesis"), r("Columns (USA)", platform=None)]
    )
    assert len(groups) == 2
    assert {g.platform for g in groups} == {"genesis", None}
    # And the one nobody described sorts last.
    assert groups[-1].platform is None


def test_the_single_variant_row_keeps_its_whole_title():
    """Collapsing removed nothing, so nothing should look removed."""
    group = one(group_results([r("Vectorman (USA) (Rev 1) [!]")]))
    assert group.title == "Vectorman (USA) (Rev 1) [!]"
    assert group.variant_count == 1


# --- cross-source -------------------------------------------------------


def test_the_same_rom_from_two_sources_is_one_variant():
    groups = group_results(
        [
            r("Aladdin (USA).md", plugin="archive-org"),
            r("Aladdin (USA).md", plugin="nointro-archive"),
        ]
    )
    group = one(groups)
    assert group.variant_count == 1
    assert group.sources == ("archive-org", "nointro-archive")
    # Both rows survive under the one variant -- the operator picks a source.
    assert group.result_count == 2


def test_two_catalogues_naming_one_region_differently_still_merge():
    """`(USA, Europe)` is No-Intro's spelling; `(UE)` is GoodTools'."""
    group = one(
        group_results(
            [
                r("Aladdin (USA, Europe)", plugin="nointro-archive"),
                r("Aladdin (UE)", plugin="archive-org"),
            ]
        )
    )
    assert group.variant_count == 1
    assert len(group.sources) == 2


def test_cross_source_and_cross_variant_are_different_problems():
    """Both fire on one listing without interfering."""
    results = [
        r("Sonic the Hedgehog (USA)", plugin="archive-org"),
        r("Sonic the Hedgehog (USA)", plugin="nointro-archive"),
        r("Sonic the Hedgehog (Europe)", plugin="nointro-archive"),
        r("Sonic the Hedgehog (Japan)", plugin="archive-org"),
    ]
    group = one(group_results(results, "sonic the hedgehog"))
    assert group.variant_count == 3  # USA, Europe, Japan
    assert group.result_count == 4  # the USA one came from two sources
    assert group.sources == ("archive-org", "nointro-archive")


def test_a_matching_hash_merges_names_that_would_not_have():
    """A hash is stronger evidence than a title, so it wins."""
    sha = "a" * 40
    group = one(
        group_results(
            [
                r("Streets of Rage (USA)", plugin="archive-org", sha1=sha),
                r("Streets of Rage (U) [!]", plugin="nointro-archive", sha1=sha),
            ]
        )
    )
    assert group.variant_count == 1
    assert group.result_count == 2


# --- the near-misses ----------------------------------------------------


def test_a_conflicting_hash_splits_two_identically_named_rows():
    """Disproof outranks the name.

    Two catalogues can name two different dumps the same thing. When they
    disagree about the bytes, that disagreement is the more reliable
    signal, and the two stay two rows.
    """
    group = one(
        group_results(
            [
                r("Golden Axe (USA)", plugin="archive-org", sha1="a" * 40),
                r("Golden Axe (USA)", plugin="nointro-archive", sha1="b" * 40),
            ]
        )
    )
    assert group.variant_count == 2


def test_a_conflicting_crc32_also_splits_them():
    """CRC-32 refuses a merge without ever being allowed to assert one.

    The same asymmetry `plugins-dev/hasheous` applies to identity: 32 bits
    is far too weak to say "these are the same file" and quite strong
    enough to say "these are not".
    """
    group = one(
        group_results(
            [
                r("Golden Axe (USA)", plugin="a", crc="deadbeef"),
                r("Golden Axe (USA)", plugin="b", crc="feedface"),
            ]
        )
    )
    assert group.variant_count == 2


def test_a_matching_crc32_alone_does_not_merge_two_names():
    crc = "deadbeef"
    groups = group_results(
        [
            r("Golden Axe (USA)", plugin="a", crc=crc),
            r("Golden Axe (Europe)", plugin="b", crc=crc),
        ]
    )
    assert one(groups).variant_count == 2


@pytest.mark.parametrize(
    "left,right",
    [
        ("Sonic the Hedgehog (USA)", "Sonic the Hedgehog 2 (USA)"),
        ("Aladdin (USA)", "Aladdin 2 (USA)"),
        ("Prince of Persia (USA)", "Prince of Persia 2 (USA)"),
        ("Desert Assault (USA)", "Desert Strike (USA)"),
        ("Agassi Tennis (USA)", "Andre Agassi Tennis (USA)"),
        ("Final Fantasy II (USA)", "Final Fantasy 2 (USA)"),
    ],
)
def test_two_games_that_look_alike_stay_two_rows(left, right):
    """Showing a duplicate is recoverable; a wrong merge is not."""
    assert len(group_results([r(left), r(right)])) == 2


def test_a_malformed_hash_is_treated_as_no_hash_at_all():
    """A plugin's typo must degrade to "unknown", never to a comparison."""
    group = one(
        group_results(
            [
                r("Ecco (USA)", plugin="a", sha1="not-a-hash"),
                r("Ecco (USA)", plugin="b", sha1="also bad"),
            ]
        )
    )
    # Fell back to the name, which says these are the same variant.
    assert group.variant_count == 1


# --- ordering -----------------------------------------------------------


def test_relevance_leads_then_platform_then_title():
    results = [
        r("Sonic Spinball (USA)", platform="genesis"),
        r("Sonic (USA)", platform="genesis"),
        r("Sonic (USA)", platform="gamegear"),
        r("Dr. Robotnik's Mean Bean Machine (USA)", platform="genesis"),
    ]
    groups = group_results(results, "sonic")
    assert groups[0].title_key == "sonic" and groups[0].platform == "gamegear"
    assert groups[1].title_key == "sonic" and groups[1].platform == "genesis"
    assert groups[2].title_key == "sonic spinball"
    assert groups[-1].title_key.startswith("dr robotnik")


def test_the_verified_dump_leads_its_group():
    group = one(
        group_results(
            [
                r("Ecco (USA) [b1]"),
                r("Ecco (USA)"),
                r("Ecco (USA) [!]"),
            ]
        )
    )
    assert [v.label for v in group.variants] == [
        "(USA) [!]",
        "(USA)",
        "(USA) [b1]",
    ]


def test_grouping_is_reproducible():
    results = [r(f"Game {i % 7} (USA) (Rev {i % 3})") for i in range(40)]
    first = [(g.title_key, [v.label for v in g.variants]) for g in group_results(results, "game")]
    second = [
        (g.title_key, [v.label for v in g.variants])
        for g in group_results(list(reversed(results)), "game")
    ]
    assert first == second


# --- paging -------------------------------------------------------------


def test_paging_walks_the_merged_set_not_the_raw_one():
    """The whole point: 60 raw rows are 20 games, and page 2 is games
    11-20, not "whatever the second source happened to return"."""
    results = []
    for i in range(20):
        for region in ("USA", "Europe", "Japan"):
            results.append(r(f"Game {i:02d} ({region})"))
    groups = group_results(results, "game")
    assert len(groups) == 20

    first = paginate(groups, limit=10, offset=0)
    second = paginate(groups, limit=10, offset=10)

    assert first.total_groups == 20
    assert first.total_results == 60
    assert (first.first, first.last) == (1, 10)
    assert first.has_more is True
    assert (second.first, second.last) == (11, 20)
    assert second.has_more is False
    # No overlap and no gap.
    assert [g.title_key for g in first.groups] + [
        g.title_key for g in second.groups
    ] == [g.title_key for g in groups]


def test_a_page_past_the_end_is_empty_and_says_so():
    groups = group_results([r("Only Game (USA)")])
    page = paginate(groups, limit=10, offset=50)
    assert page.groups == []
    assert page.first == 0
    assert page.total_groups == 1
    assert page.has_more is False


def test_nonsense_paging_arguments_are_corrected_not_refused():
    groups = group_results([r("A (USA)"), r("B (USA)")])
    assert paginate(groups, limit=10, offset=-5).offset == 0
    assert paginate(groups, limit=0, offset=0).groups == []


# --- honesty ------------------------------------------------------------


def test_grouping_does_not_touch_the_per_source_statuses():
    """Grouping reorganises what came back. It cannot know what a source
    that failed would have said, and must not look like it does."""
    outcome = SearchOutcome(
        results=[r("Ecco (USA)", plugin="ok"), r("Ecco (Europe)", plugin="ok")],
        statuses=[
            PluginStatus("ok", True, 2),
            PluginStatus("down", False, 0, "TimeoutError: boom"),
        ],
    )
    group = one(group_results(outcome.results))
    assert group.variant_count == 2
    assert outcome.responded == 1
    assert outcome.total == 2
    assert outcome.complete is False


class FakePlugin:
    def __init__(self, slug, enabled=True):
        self.slug = slug
        self.enabled = enabled
        self.manifest = type(
            "M", (), {"slug": slug, "capabilities": {"search": "x:Y"}}
        )()
        self.path = "/nowhere"
        self.config = {}


class FakeProcess:
    def __init__(self, results):
        self._results = results

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def search(self, query, platform, limit):
        return self._results[:limit]


def test_a_source_that_filled_its_quota_is_reported_as_capped():
    """A merged listing that dropped a source's tail must not be
    indistinguishable from a complete one."""
    plugins = [FakePlugin("full"), FakePlugin("short")]
    behaviour = {
        "full": FakeProcess([r(f"G{i} (USA)", plugin="full") for i in range(10)]),
        "short": FakeProcess([r("One (USA)", plugin="short")]),
    }
    outcome = search_all(
        plugins,
        fetcher=None,
        query="g",
        limit=5,
        process_factory=lambda p, f, t: behaviour[p.slug],
    )
    assert outcome.capped == ["full"]
    assert outcome.responded == 2


def test_fanout_asks_for_more_than_the_page_because_grouping_collapses():
    assert fanout_limit(25, 0) > 25
    assert fanout_limit(1, 0) == MIN_FANOUT
    assert fanout_limit(10_000, 0) == MAX_FANOUT
    # Paging deep still fetches enough to reach the page asked for.
    assert fanout_limit(10, 200) > fanout_limit(10, 0)
    # And an explicit override is obeyed exactly.
    assert fanout_limit(25, 0, per_source=7) == 7


# --- scale --------------------------------------------------------------


def test_platform_key_is_case_and_whitespace_only():
    assert platform_key("  Genesis ") == "genesis"
    assert platform_key("") is None
    assert platform_key(None) is None


def test_grouping_thousands_of_rows_is_not_the_bottleneck():
    """Console Living Room alone holds ~10,000 downloadable Genesis ROMs.

    A generous ceiling rather than a benchmark -- this asserts that
    grouping stayed roughly linear, not that any particular machine is
    fast. The fan-out it follows is network-bound and orders of magnitude
    slower.
    """
    regions = ("USA", "Europe", "Japan", "World")
    results = [
        r(
            f"Game {i // 4:05d} ({regions[i % 4]}) (Rev {i % 3})",
            plugin=f"src-{i % 8}",
        )
        for i in range(20_000)
    ]
    start = time.perf_counter()
    groups = group_results(results, "game")
    elapsed = time.perf_counter() - start
    assert len(groups) == 5_000
    assert sum(g.result_count for g in groups) == 20_000
    assert elapsed < 10.0, f"grouping 20k rows took {elapsed:.2f}s"
