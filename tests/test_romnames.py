"""What the name parser must and must not do.

The "must not" half carries most of the weight. Every test below that
asserts two names produce *different* keys is a duplicate this project has
chosen to show rather than a merge it has chosen to risk.
"""

import pytest

from rom_hub.romnames import (
    RomName,
    normalise_title,
    parse,
    strip_extension,
    variant_rank,
)


# --- the [!] regression -------------------------------------------------
#
# A previous filename validator in this project silently dropped every
# GoodTools `[!]` name it saw -- the verified-good-dump marker, i.e. the
# files people most want. Parsing is the obvious lever for grouping and it
# is also the obvious place to reintroduce that bug, so it is pinned here
# before anything else.


def test_a_verified_dump_marker_survives_parsing():
    name = parse("Super Mario Land 4 (J) [!].gb")
    assert name.title == "Super Mario Land 4"
    assert name.raw == "Super Mario Land 4 (J) [!].gb"
    assert "[!]" in name.tags
    assert "verified" in name.flags
    assert "[!]" in name.label


def test_a_verified_dump_is_a_variant_not_a_filter():
    """`[!]` distinguishes a dump; it never removes one."""
    plain = parse("Aladdin (USA).md")
    verified = parse("Aladdin (USA) [!].md")
    assert plain.title_key == verified.title_key
    assert plain.variant_key != verified.variant_key
    # And it sorts first, because it is the one you want -- ordering, not
    # filtering.
    assert variant_rank(verified) < variant_rank(plain)


@pytest.mark.parametrize(
    "raw",
    [
        "Game [!].nes",
        "Game [b1].nes",
        "Game [a2].nes",
        "Game [o1].nes",
        "Game [T+Eng1.0].nes",
        "Game [h1C].nes",
        "Game (Unl) [!].nes",
    ],
)
def test_every_goodtools_bracket_form_still_parses_to_a_title(raw):
    name = parse(raw)
    assert name.title == "Game"
    assert name.title_key == "game"
    assert name.tags  # the tag was kept, not dropped


# --- titles that must agree ---------------------------------------------


@pytest.mark.parametrize(
    "left,right",
    [
        ("Pokemon Cafe Mix", "Pokémon Café Mix"),
        ("The Legend of Zelda", "Legend of Zelda, The"),
        ("Sonic & Knuckles", "Sonic and Knuckles"),
        ("Mario Bros.", "Mario Bros"),
        ("Prince of Persia", "PRINCE OF PERSIA"),
        ("Micro Machines: Turbo", "Micro Machines - Turbo"),
    ],
)
def test_the_same_game_spelled_two_ways_keys_the_same(left, right):
    assert normalise_title(left) == normalise_title(right)


def test_regions_from_two_conventions_reach_one_variant_key():
    """`(USA, Europe)` is No-Intro; `(UE)` is GoodTools. Same ROM."""
    assert parse("Aladdin (USA, Europe)").variant_key == parse(
        "Aladdin (UE)"
    ).variant_key


def test_region_order_inside_a_tag_does_not_matter():
    assert parse("Aladdin (Europe, USA)").regions == parse(
        "Aladdin (USA, Europe)"
    ).regions


# --- titles that must NOT agree -----------------------------------------


@pytest.mark.parametrize(
    "left,right",
    [
        ("Sonic the Hedgehog", "Sonic the Hedgehog 2"),
        ("Prince of Persia", "Prince of Persia 2 - The Shadow and the Flame"),
        ("Aladdin", "Aladdin 2"),
        ("Batman Returns", "Batman Forever"),
        ("Final Fantasy II", "Final Fantasy III"),
        # Roman numerals are deliberately NOT folded to digits: doing it
        # correctly needs to know which trailing token is a numeral, and
        # doing it wrongly merges two different games.
        ("Final Fantasy II", "Final Fantasy 2"),
        # Nor are subtitles stripped, nor near-identical names guessed at.
        ("Agassi Tennis", "Andre Agassi Tennis"),
        ("Sonic 3", "Sonic 3.0"),
    ],
)
def test_two_different_games_never_share_a_title_key(left, right):
    assert normalise_title(left) != normalise_title(right)


def test_japanese_kana_keep_their_dakuten():
    """Accent folding is a Latin rule, and must not touch other scripts.

    NFKD decomposes ガ into カ + a combining mark; dropping that mark
    unconditionally would turn "ga" into "ka", which is a different
    syllable and therefore a genuinely wrong merge.
    """
    assert normalise_title("ガ") != normalise_title("カ")


# --- extensions ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Batman Returns (USA).md", "Batman Returns (USA)"),
        ("Sonic.zip", "Sonic"),
        ("Golden Axe.7z", "Golden Axe"),
        # Not an extension this module knows: left alone rather than
        # guessed at, because "drop whatever follows the last dot" turns
        # `Sonic 3.0` into `Sonic 3`.
        ("Sonic 3.0", "Sonic 3.0"),
        ("Mario Bros.", "Mario Bros."),
        ("Half-Life", "Half-Life"),
    ],
)
def test_strip_extension_only_removes_names_it_recognises(raw, expected):
    assert strip_extension(raw) == expected


# --- totality -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "(",
        "[]",
        "()",
        "(Homebrew)",
        "ソニックリンカー",
        "hit: oregon trail",
        "a" * 400,
    ],
)
def test_every_string_parses_to_something(raw):
    name = parse(raw)
    assert isinstance(name, RomName)
    assert name.raw == raw


def test_a_title_that_is_only_a_tag_keeps_its_tag_as_the_title():
    """Stripping it would leave nothing to group on."""
    name = parse("(Homebrew)")
    assert name.title == "(Homebrew)"
    assert name.tags == ()


def test_an_unknown_tag_becomes_a_variant_rather_than_being_ignored():
    """The discard rule, stated as a test.

    A tag nobody has taught this module about still distinguishes two
    dumps, so the two stay separate rows instead of being merged on the
    strength of a pattern that did not match.
    """
    known = parse("Columns (USA)")
    unknown = parse("Columns (USA) (Sega Channel)")
    assert known.title_key == unknown.title_key
    assert known.variant_key != unknown.variant_key
    assert any(t.startswith("tag:") for t in unknown.tokens)


def test_an_indexed_flag_keeps_its_index():
    """`(Beta 2)` and `(Beta 3)` are two builds, not one."""
    assert parse("Vectorman (Beta 2)").variant_key != parse(
        "Vectorman (Beta 3)"
    ).variant_key
    assert parse("Vectorman [a1]").variant_key != parse("Vectorman [a2]").variant_key


def test_revisions_are_variants_of_one_game():
    base = parse("Batman Returns (USA).md")
    rev = parse("Batman Returns (USA) (Rev 1).md")
    assert base.title_key == rev.title_key
    assert base.variant_key != rev.variant_key
    # The later revision leads the expanded listing.
    assert variant_rank(rev) < variant_rank(base)


def test_bracketed_a_is_an_alternate_dump_not_australia():
    """The bracket style is kept through parsing precisely for this."""
    assert parse("Ecco (A)").regions == ("Australia",)
    assert parse("Ecco [a]").regions == ()
    assert "alternate" in parse("Ecco [a]").flags


def test_a_bad_dump_sorts_last_and_is_still_there():
    good = parse("Ecco (USA) [!]")
    bad = parse("Ecco (USA) [b1]")
    assert variant_rank(good) < variant_rank(bad)
    assert "baddump" in bad.flags
