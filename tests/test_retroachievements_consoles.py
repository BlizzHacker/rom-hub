"""The RomM platform slug -> RetroAchievements console id table.

`romm_platform_ra_ids.json` is captured, not composed: it is the `slug`
and `ra_id` of every platform RomM 4.9.2 returns from
`GET /api/platforms/supported` that carries an `ra_id` at all, read from a
live RomM on 2026-07-29. RomM already knows this mapping and a plugin
cannot ask it, so `consoles.CONSOLES` is a copy -- and a copy is only
worth having if something notices when it drifts.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "retroachievements"
sys.path.insert(0, str(PLUGIN_ROOT))

from retroachievements.consoles import (  # noqa: E402
    CONSOLES,
    WHOLE_FILE_MD5,
    NeedsMapping,
    console_for,
    hashes_whole_file,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "retroachievements"
ROMM = json.loads((FIXTURES / "romm_platform_ra_ids.json").read_text())


def test_the_table_is_exactly_what_romm_itself_reports():
    assert CONSOLES == ROMM


@pytest.mark.parametrize("slug,console_id", sorted(CONSOLES.items()))
def test_every_console_id_is_in_ras_range(slug, console_id):
    """rc_consoles.h numbers them from 1 upward with no gaps worth trusting
    at the top end; anything outside this is a typo, not a console."""
    assert isinstance(console_id, int)
    assert 1 <= console_id <= 200, slug


def test_the_families_ra_folds_together_are_folded():
    """RA has one console for the Neo Geo Pocket line and one for the
    WonderSwan line; RomM has two slugs for each."""
    assert CONSOLES["neo-geo-pocket"] == CONSOLES["neo-geo-pocket-color"] == 14
    assert CONSOLES["wonderswan"] == CONSOLES["wonderswan-color"] == 53
    assert CONSOLES["snes"] == CONSOLES["sfam"] == 3
    assert CONSOLES["nes"] == CONSOLES["famicom"] == 7


def test_an_unmapped_platform_names_itself_and_says_why_it_will_not_guess():
    with pytest.raises(NeedsMapping) as exc:
        console_for("switch")
    message = str(exc.value)
    assert "'switch' needs mapping" in message
    assert "consoles.py" in message
    assert "wrong game list" in message


def test_the_lookup_forgives_case_and_space_but_nothing_else():
    assert console_for(" GENESIS ") == 1
    with pytest.raises(NeedsMapping):
        console_for("gene sis")


# -- which consoles hash the whole file --------------------------------


@pytest.mark.parametrize(
    "slug",
    ["genesis", "gb", "gba", "gbc", "sms", "gamegear", "sega32", "atari2600"],
)
def test_consoles_rcheevos_hashes_whole_are_marked_so(slug):
    """`rc_hash_from_buffer()` sends each of these straight to
    `rc_hash_buffer(..., iterator->buffer, iterator->buffer_size, ...)`, so
    RomM's md5 is the RA hash."""
    assert hashes_whole_file(CONSOLES[slug])


@pytest.mark.parametrize(
    "slug,what_rcheevos_does",
    [
        ("nes", "skips the 16-byte iNES header"),
        ("snes", "drops a copier header when it finds one"),
        ("n64", "byte-swaps"),
        ("lynx", "skips a 64-byte header"),
        ("atari7800", "skips a 128-byte header"),
        ("psx", "hashes the executable inside the disc image"),
        ("saturn", "hashes content inside the disc image"),
        ("segacd", "hashes content inside the disc image"),
        ("arcade", "uses the filename, not the bytes"),
    ],
)
def test_consoles_rcheevos_hashes_differently_are_not_marked(
    slug, what_rcheevos_does
):
    """Marking one of these would produce the most misleading message this
    plugin can emit: "RomM's md5 is the right value" when it is not."""
    assert not hashes_whole_file(CONSOLES[slug]), what_rcheevos_does


def test_the_whole_file_set_is_a_subset_of_consoles_that_exist():
    assert WHOLE_FILE_MD5
    assert all(isinstance(cid, int) and cid >= 1 for cid in WHOLE_FILE_MD5)
    # Most of them are consoles RomM can reach; a few (Oric, TI-83, ZX
    # Spectrum) have no RomM slug carrying an ra_id, and that is fine --
    # the set describes rcheevos, not RomM.
    assert len(WHOLE_FILE_MD5 & set(CONSOLES.values())) >= 25
