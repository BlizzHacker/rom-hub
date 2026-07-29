"""The RomM platform slug -> libretro system directory table.

The table is only useful if both of its sides are real, so both sides are
checked against captured reality rather than against themselves:

* every **value** must be a directory the live service actually serves --
  `tests/fixtures/libretro/root_index.html` is the unedited root listing
  of `https://thumbnails.libretro.com/` as it stood on 2026-07-29;
* every **key** must be a platform slug RomM actually has --
  `tests/fixtures/libretro/romm_platform_slugs.json` is the `slug` field
  of all 458 entries from `GET /api/platforms/supported` on RomM 4.9.2.

A typo on either side would otherwise be invisible: a wrong directory
name 404s forever, and a wrong slug simply never matches.
"""

import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-thumbnails"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_thumbnails.systems import SYSTEMS, NeedsMapping, system_for  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro"


def _libretro_directories() -> set[str]:
    text = (FIXTURES / "root_index.html").read_text(encoding="utf-8")
    return {
        unquote(html.unescape(m.group(1))).rstrip("/")
        for m in re.finditer(r'href="([^"?/][^"]*/)"', text)
    }


def _romm_slugs() -> set[str]:
    return set(json.loads((FIXTURES / "romm_platform_slugs.json").read_text()))


@pytest.mark.parametrize("slug,directory", sorted(SYSTEMS.items()))
def test_every_mapped_directory_exists_on_the_live_service(slug, directory):
    assert directory in _libretro_directories(), (
        f"{slug!r} maps to {directory!r}, which is not a directory "
        f"thumbnails.libretro.com serves"
    )


@pytest.mark.parametrize("slug", sorted(SYSTEMS))
def test_every_mapped_slug_is_a_real_romm_platform(slug):
    assert slug in _romm_slugs(), (
        f"{slug!r} is not a platform slug RomM 4.9.2 knows, so nothing would "
        f"ever match it"
    )


def test_the_table_is_not_a_token_gesture():
    """A three-entry table would pass every check above and be useless."""
    assert len(SYSTEMS) >= 90
    assert len(set(SYSTEMS.values())) >= 80


def test_the_lookup_is_exact_but_forgiving_about_case_and_space():
    assert system_for("snes") == "Nintendo - Super Nintendo Entertainment System"
    assert system_for(" SNES ") == "Nintendo - Super Nintendo Entertainment System"


def test_an_unmapped_platform_names_itself_in_the_refusal():
    with pytest.raises(NeedsMapping) as exc:
        system_for("switch")
    assert "'switch' needs mapping" in str(exc.value)
    assert "systems.py" in str(exc.value)


@pytest.mark.parametrize("slug", ["c128", "new-nintendo-3ds", "msx-turbo", "msx2plus"])
def test_machines_libretro_does_not_carry_separately_stay_unmapped(slug):
    """Each is a real RomM platform whose software libretro files nowhere
    of its own. Folding it into its nearest neighbour is the misfiling
    this table exists to prevent, so it stays a visible gap."""
    assert slug in _romm_slugs()
    with pytest.raises(NeedsMapping):
        system_for(slug)


def test_the_families_that_really_do_share_a_directory_do_share_one():
    """No-Intro files Famicom and Super Famicom carts in the NES and SNES
    DATs, and the thumbnails are named from those DATs."""
    assert SYSTEMS["famicom"] == SYSTEMS["nes"]
    assert SYSTEMS["sfam"] == SYSTEMS["snes"]
    assert SYSTEMS["neogeoaes"] == SYSTEMS["neogeomvs"]
    assert SYSTEMS["atari800"] == SYSTEMS["atari8bit"]
    # ... but the Disk System is its own DAT and its own directory.
    assert SYSTEMS["fds"] != SYSTEMS["nes"]
