"""Name normalisation for libretro's thumbnail repositories.

Every expected filename in this module is a **real file** on
`https://thumbnails.libretro.com/`, copied out of the Named_Boxarts
listings of the SNES, NES, PlayStation and DOS sets as they stood on
2026-07-29. That matters more than usual here: the plugin's entire job is
to reproduce a filename it cannot see, so a test asserting what we *think*
libretro does would pass while the plugin found nothing.

The scrub rule itself is RetroArch's, from
`gfx_thumbnail_fill_content_img()` in `gfx/gfx_thumbnail.c`.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-thumbnails"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_thumbnails.names import (  # noqa: E402
    MAX_CANDIDATES,
    SCRUBBED,
    candidates,
    match_key,
    move_article,
    scrub,
    shorten,
    strip_extension,
)


# -- the scrub set ------------------------------------------------------


def test_the_scrub_set_is_retroarchs_exactly():
    """RetroArch: strpbrk(s, "&*/:`\\"<>?\\\\|"). Eleven characters, no more."""
    assert set(SCRUBBED) == set('&*/:`"<>?\\|')


@pytest.mark.parametrize(
    "label,expected",
    [
        # All four are real SNES Named_Boxarts filenames.
        ("Pocky & Rocky (USA)", "Pocky _ Rocky (USA)"),
        ("Joe & Mac (USA)", "Joe _ Mac (USA)"),
        ("Mega Man & Bass (USA)", "Mega Man _ Bass (USA)"),
        (
            "Ren & Stimpy Show, The - Veediots! (USA)",
            "Ren _ Stimpy Show, The - Veediots! (USA)",
        ),
    ],
)
def test_ampersand_becomes_underscore(label, expected):
    assert scrub(label) == expected


def test_every_scrubbed_character_becomes_one_underscore():
    assert scrub('a&b*c/d:e`f"g<h>i?j\\k|l') == "a_b_c_d_e_f_g_h_i_j_k_l"


@pytest.mark.parametrize("keep", "'!,.[]+$%^~;#=-")
def test_characters_libretro_really_uses_are_left_alone(keep):
    """Each of these appears in the live listings, so scrubbing one would
    turn a name that exists into a name that does not."""
    assert scrub(f"x{keep}y") == f"x{keep}y"


def test_apostrophes_survive():
    # Real: Nintendo - SNES/Named_Boxarts/Super Mario World 2 - Yoshi's Island (USA).png
    assert scrub("Super Mario World 2 - Yoshi's Island (USA)").endswith(
        "Yoshi's Island (USA)"
    )


# -- extensions ---------------------------------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Super Mario World (USA).sfc", "Super Mario World (USA)"),
        ("rubik.zip", "rubik"),
        ("Final Fantasy VII (USA) (Disc 1).chd", "Final Fantasy VII (USA) (Disc 1)"),
        # No extension to take.
        ("Super Mario World (USA)", "Super Mario World (USA)"),
        # A dot that is not an extension: what follows it has a space in it.
        ("Dr. Mario (USA)", "Dr. Mario (USA)"),
        ("Mario Bros. 3", "Mario Bros. 3"),
        # A label that is nothing but a dotted word keeps itself.
        (".hack", ".hack"),
    ],
)
def test_strip_extension(label, expected):
    assert strip_extension(label) == expected


# -- articles -----------------------------------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        # Real: SNES/Named_Boxarts/Legend of Zelda, The - A Link to the Past (USA).png
        (
            "The Legend of Zelda - A Link to the Past (USA)",
            "Legend of Zelda, The - A Link to the Past (USA)",
        ),
        # ... and back the other way.
        (
            "Legend of Zelda, The - A Link to the Past (USA)",
            "The Legend of Zelda - A Link to the Past (USA)",
        ),
        # Real: DOS/Named_Boxarts/Oregon Trail Deluxe, The (1992).png
        ("The Oregon Trail Deluxe (1992)", "Oregon Trail Deluxe, The (1992)"),
        # Real: DOS/Named_Boxarts/7th Guest, The (1993).png
        ("The 7th Guest (1993)", "7th Guest, The (1993)"),
        # The article lands before the tags, never after them.
        ("The Firemen (Europe)", "Firemen, The (Europe)"),
        # Non-English articles are moved too: real DOS and SNES entries use
        # ", La" and ", El".
        ("La Abadia del Crimen", "Abadia del Crimen, La"),
    ],
)
def test_move_article(label, expected):
    assert move_article(label) == expected


def test_the_article_goes_before_the_subtitle_not_after_it():
    """No-Intro puts it at the end of the title, not the end of the string.

    Real file: `Ren _ Stimpy Show, The - Veediots! (USA).png`. Appending
    ", The" to the whole label would give `... Veediots!, The`, which does
    not exist and never will.
    """
    assert (
        move_article("The Ren & Stimpy Show - Veediots! (USA)")
        == "Ren & Stimpy Show, The - Veediots! (USA)"
    )


@pytest.mark.parametrize(
    "label", ["Super Mario World (USA)", "Chrono Trigger (USA)", "Doom (1993)"]
)
def test_a_label_with_no_article_yields_no_second_spelling(label):
    assert move_article(label) is None


def test_a_bare_article_is_not_an_article():
    """"The" alone is a title, not "" with an article on it."""
    assert move_article("The") is None
    assert move_article("A (USA)") is None


def test_bare_i_is_not_treated_as_an_article():
    """It is the Italian plural article and also an English pronoun. No DAT
    spells "I Have No Mouth..." with it moved, so neither do we."""
    assert move_article("I Have No Mouth, and I Must Scream") is None


# -- RetroArch's own shortening ----------------------------------------


def test_shorten_drops_everything_from_the_first_bracket():
    # Real: DOS has BOTH `Prince of Persia (1990).png` and
    # `Prince of Persia.png`, which is exactly what this fallback is for.
    assert shorten("Prince of Persia (1990)") == "Prince of Persia"
    assert shorten("Super Metroid (Japan, USA) (En,Ja)") == "Super Metroid"


def test_shorten_returns_none_when_there_is_nothing_to_drop():
    assert shorten("Prince of Persia") is None
    # A label that is *only* a bracket has no title left to keep.
    assert shorten("(USA)") is None


# -- the candidate ladder ----------------------------------------------


def test_the_exact_spelling_is_tried_first():
    assert candidates(["Super Mario World (USA)"])[0] == "Super Mario World (USA)"


def test_the_ladder_covers_scrub_article_and_shorten():
    assert candidates(["The Ren & Stimpy Show - Veediots! (USA)"]) == [
        "The Ren _ Stimpy Show - Veediots! (USA)",
        "Ren _ Stimpy Show, The - Veediots! (USA)",  # the one that exists
        "The Ren _ Stimpy Show - Veediots!",
        "Ren _ Stimpy Show, The - Veediots!",
    ]


def test_the_name_is_tried_before_the_filename():
    """RomM's `name` is what a human curated; `fs_name` is what a scanner
    found. Both are offered, in that order."""
    got = candidates(["Prince of Persia (1990)", "PRINCEOFPERSIA.ZIP"])
    assert got.index("Prince of Persia (1990)") < got.index("PRINCEOFPERSIA")


def test_a_stripped_extension_is_still_offered_unstripped():
    """Insurance for a title that ends in something extension-shaped."""
    got = candidates(["Sam.and.Max"])
    assert "Sam.and.Max" in got


def test_nothing_is_offered_twice():
    got = candidates(["Doom (1993)", "Doom (1993)", "Doom (1993).exe"])
    assert len(got) == len(set(got))


def test_empty_and_blank_labels_contribute_nothing():
    assert candidates([None, "", "   "]) == []


def test_the_ladder_is_bounded():
    """Each probe is a real image download, so the list has a ceiling."""
    got = candidates([f"The Game {i} & Friends (USA)" for i in range(20)])
    assert len(got) == MAX_CANDIDATES


def test_no_candidate_ever_adds_a_word_the_library_did_not_have():
    """A candidate is a re-spelling, never a guess. Adding "(USA)" to a
    library that did not say it would match a different release -- or a
    different game."""
    for candidate in candidates(["Sonic the Hedgehog"]):
        assert "(" not in candidate and "[" not in candidate


# -- the index-fallback key --------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        # The point of the whole fallback: a library without the year tag.
        ("Prince of Persia", "Prince of Persia (1990)"),
        ("The Oregon Trail Deluxe", "Oregon Trail Deluxe, The (1992)"),
        ("Pocky & Rocky", "Pocky _ Rocky (USA)"),
        ("Super Metroid", "Super Metroid (Japan, USA) (En,Ja)"),
        ("doom", "DOOM (1993)"),
        ("Donkey Kong Country", "Donkey Kong Country (USA) (Rev 2)"),
        ("X-COM: UFO Defense", "X-COM - UFO Defense (1994)"),
    ],
)
def test_match_key_unifies_the_same_title(a, b):
    assert match_key(a) == match_key(b)


@pytest.mark.parametrize(
    "a,b",
    [
        # An equality test, not a prefix test. This is the pair that makes
        # the difference between "found the box art" and "found *a* box art".
        ("Prince of Persia", "Prince of Persia 2 - The Shadow and The Flame (1993)"),
        ("Sonic the Hedgehog", "Sonic the Hedgehog 2 (USA)"),
        ("Doom", "Doom II (1994)"),
        ("Final Fantasy", "Final Fantasy III (USA)"),
        ("SimCity", "SimCity 2000 - CD Collection (1994)"),
    ],
)
def test_match_key_keeps_different_titles_apart(a, b):
    assert match_key(a) != match_key(b)
