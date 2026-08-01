"""`rom-hub search` end to end: grouping, paging and the flags for both.

The unit half is tests/test_grouping.py. These go through argparse and the
real registry with real plugin subprocesses, so they pin what an operator
actually sees -- including that `--no-group` still prints the listing this
command printed before grouping existed.

Offline throughout: the plugins here return literals and never call
`ctx.http`.
"""

import subprocess
from pathlib import Path

import pytest

from rom_hub.cli import main

MANIFEST = """
[plugin]
slug = "{slug}"
name = "{slug}"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "demo:Search"

[permissions]
network = ["demo.example"]
romm_api = []
"""

_BODY = """
from rom_hub_sdk import SearchProvider, SearchResult

TITLES = {titles!r}


class Search(SearchProvider):
    def search(self, query, platform, limit):
        hits = [t for t in TITLES if query.lower() in t.lower()]
        return [
            SearchResult(source_id=str(i), title=t, platform="gamegear")
            for i, t in enumerate(hits)
        ][:limit]
"""

# One game with eight dumps, two near-misses that must stay their own rows,
# and an unrelated game. Modelled on the reported Game Gear shelf, where
# *Batman Returns* appears eight times and the listing reads as broken.
SHELF_PLUGIN = _BODY.format(
    titles=[
        "Batman Returns (USA)",
        "Batman Returns (Europe)",
        "Batman Returns (Japan)",
        "Batman Returns (USA) (Rev 1)",
        "Batman Returns (Europe) (Rev 1)",
        "Batman Returns (USA) [!]",
        "Batman Returns (Europe) [b1]",
        "Batman Returns (World) (Beta)",
        "Batman Forever (USA)",
        "Batman - The Video Game (USA)",
        "Aladdin (USA) [!]",
    ]
)

# A second source offering two of the same ROMs, named the way a different
# catalogue names them: GoodTools initials and a file extension.
MIRROR_PLUGIN = _BODY.format(
    titles=["Batman Returns (USA).gg", "Aladdin (U) [!].gg"]
)

BROKEN_PLUGIN = """
from rom_hub_sdk import SearchProvider


class Search(SearchProvider):
    def search(self, query, platform, limit):
        raise RuntimeError("kaboom")
"""


def _repo(tmp_path: Path, slug: str, source: str) -> Path:
    repo = tmp_path / slug
    repo.mkdir()
    (repo / "manifest.toml").write_text(MANIFEST.format(slug=slug), encoding="utf-8")
    (repo / "demo.py").write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def two_sources(tmp_path, monkeypatch):
    """A Hub with two sources whose listings overlap."""
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    # The host fails closed when a plugin cannot be confined; on Linux this
    # is a no-op because the seccomp filter loads anyway.
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    for slug, source in (("shelf", SHELF_PLUGIN), ("mirror", MIRROR_PLUGIN)):
        main(["plugin", "install", str(_repo(tmp_path, slug, source))])
    return tmp_path


def _variant_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("        - ")]


# --- grouping -----------------------------------------------------------


def test_the_shelf_collapses_but_the_count_stays_visible(two_sources, capsys):
    assert main(["search", "batman"]) == 0
    out = capsys.readouterr().out
    # Nine near-identical lines became one row that says how many it is.
    assert "Batman Returns  [8 variants]" in out
    assert out.count("Batman Returns") == 1
    # The near-miss is a different game and keeps its own row.
    assert "Batman Forever (USA)" in out
    # Nothing was thrown away, and the summary states both numbers.
    assert "11 results in 3 games" in out


def test_no_group_prints_the_pre_grouping_listing(two_sources, capsys):
    """The flag exists so nobody has to trust the grouping to see the raw
    set, and the raw set is exactly what this command printed before."""
    assert main(["search", "batman", "--no-group"]) == 0
    out = capsys.readouterr().out
    assert out.count("Batman Returns") == 9  # 8 from shelf + 1 from mirror
    assert "variants" not in out
    assert "2 of 2 sources responded, 11 results" in out


def test_the_same_rom_from_two_sources_is_one_row_naming_both(two_sources, capsys):
    """`Aladdin (USA) [!]` and `Aladdin (U) [!].gg` are one ROM."""
    assert main(["search", "aladdin", "--all-variants"]) == 0
    out = capsys.readouterr().out
    assert "2 sources" in out
    assert "mirror, shelf" in out
    assert "2 results in 1 games" in out
    assert len(_variant_lines(out)) == 1


def test_a_bracketed_verified_name_survives_the_whole_command(two_sources, capsys):
    """The regression this project has already shipped once.

    A filename validator strict enough to drop GoodTools `[!]` names
    dropped exactly the ROMs people want most, and nothing said so.
    Grouping parses those names; it must never become the thing that
    loses them.
    """
    assert main(["search", "aladdin", "--all-variants"]) == 0
    assert "[!]" in capsys.readouterr().out


# --- expansion ----------------------------------------------------------


def test_expand_reaches_every_variant_of_one_row(two_sources, capsys):
    main(["search", "batman"])
    grouped = capsys.readouterr().out
    row = next(ln for ln in grouped.splitlines() if "variants]" in ln).split()[0]

    assert main(["search", "batman", "--expand", row]) == 0
    out = capsys.readouterr().out
    for label in ("(USA)", "(Europe)", "(Japan)", "(Rev 1)", "[!]", "[b1]", "(Beta)"):
        assert label in out
    lines = _variant_lines(out)
    assert len(lines) == 8
    # The verified dump leads; the bad dump is last but is still listed.
    assert "[!]" in lines[0]
    assert "[b1]" in lines[-1]


def test_all_variants_expands_every_row(two_sources, capsys):
    assert main(["search", "batman", "--all-variants"]) == 0
    # 8 Batman Returns dumps + Batman Forever + Batman - The Video Game.
    assert len(_variant_lines(capsys.readouterr().out)) == 10


def test_a_nonsense_expand_argument_is_a_note_not_a_failure(two_sources, capsys):
    assert main(["search", "batman", "--expand", "banana"]) == 0
    assert "not a row number" in capsys.readouterr().err


# --- paging -------------------------------------------------------------


def test_limit_and_offset_page_the_merged_set(two_sources, capsys):
    assert main(["search", "batman", "--limit", "1"]) == 0
    first = capsys.readouterr().out
    assert "showing 1-1 of 3" in first
    assert "next page: --offset 1" in first

    assert main(["search", "batman", "--limit", "1", "--offset", "1"]) == 0
    second = capsys.readouterr().out
    assert "showing 2-2 of 3" in second
    # A real page, not the same row again.
    assert first.splitlines()[0] != second.splitlines()[0]


def test_row_numbers_are_absolute_so_expand_works_on_page_two(two_sources, capsys):
    assert main(["search", "batman", "--limit", "1", "--offset", "2"]) == 0
    assert capsys.readouterr().out.splitlines()[0].split()[0] == "3"


def test_the_last_page_does_not_offer_a_next_one(two_sources, capsys):
    assert main(["search", "batman", "--limit", "50"]) == 0
    out = capsys.readouterr().out
    assert "showing 1-3 of 3" in out
    assert "next page" not in out


# --- honesty ------------------------------------------------------------


def test_per_source_bounds_the_fanout_and_the_cap_is_reported(two_sources, capsys):
    """Grouping must not hide that a source had more to give."""
    assert main(["search", "batman", "--per-source", "2"]) == 0
    captured = capsys.readouterr()
    assert "3 results" in captured.out
    assert "returned the full 2 results" in captured.err


def test_a_failing_source_is_still_reported_alongside_grouped_results(
    tmp_path, monkeypatch, capsys
):
    """Partial results stay partial.

    Grouping reorganises what came back; it cannot know what a source that
    failed would have said, and must not look like it does.
    """
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    for slug, source in (("shelf", SHELF_PLUGIN), ("broken", BROKEN_PLUGIN)):
        main(["plugin", "install", str(_repo(tmp_path, slug, source))])

    assert main(["search", "batman"]) == 0
    captured = capsys.readouterr()
    assert "1 of 2 sources responded" in captured.out
    assert "! broken:" in captured.err
    assert "kaboom" in captured.err
    # And the surviving source's results were still grouped.
    assert "variants]" in captured.out
