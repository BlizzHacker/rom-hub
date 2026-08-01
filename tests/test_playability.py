"""`playability` must agree with RomM's own core map, not with itself."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rom_hub import playability
from rom_hub.playability import (
    CATALOGUE_ONLY,
    EJS_CORES,
    EJS_NIGHTLY_CORES,
    NEEDS_NETPLAY,
    NETPLAY_ONLY,
    PLAYABLE,
    PLAYS,
    cores_for,
    import_warning,
    is_playable,
    verdict_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "romm" / "ejs_cores_map.ts"


def _parse(block: str) -> dict[str, list[str]]:
    """Read one `Record<string, string[]>` literal out of RomM's TypeScript.

    Deliberately a parser rather than a second copy of the data: a test
    that restated the table would only prove the restatement matched, and
    would go stale in exactly the same breath as the thing it checks.
    """
    out: dict[str, list[str]] = {}
    for match in re.finditer(
        r'(?:"([a-z0-9\-_]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*:\s*\[(.*?)\]', block, re.S
    ):
        key = match.group(1) or match.group(2)
        out[key] = re.findall(r'"([a-z0-9_]+)"', match.group(3))
    return out


def _romm_maps() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    text = FIXTURE.read_text(encoding="utf-8")

    def block(marker: str) -> str:
        start = text.index("{", text.index(marker))
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        raise AssertionError(f"unterminated literal after {marker!r}")

    return _parse(block("_EJS_CORES_MAP: Record")), _parse(
        block("_EJS_NIGHTLY_CORES_MAP: Record")
    )


# -- the vendored copy is the real thing ---------------------------------


def test_base_map_matches_romm_source():
    base, _ = _romm_maps()
    assert {k: list(v) for k, v in EJS_CORES.items()} == base


def test_nightly_map_matches_romm_source():
    _, nightly = _romm_maps()
    assert {k: list(v) for k, v in EJS_NIGHTLY_CORES.items()} == nightly


def test_the_fixture_is_not_empty():
    """A parser that silently found nothing would make both tests above pass."""
    base, nightly = _romm_maps()
    assert len(base) == 78
    assert len(nightly) == 23


def test_nightly_only_is_the_three_platforms_the_base_map_lacks():
    """Most nightly rows are extra cores for a platform that already plays.

    Only these three are platforms that exist nowhere else, and they are
    the whole reason the two maps are kept apart.
    """
    assert NETPLAY_ONLY == {"3ds", "new-nintendo-3ds", "intellivision"}


def test_playable_is_the_base_map_and_nothing_else():
    assert PLAYABLE == frozenset(EJS_CORES)
    assert not (PLAYABLE & NETPLAY_ONLY)


# -- the lookup mirrors getSupportedEJSCores -----------------------------


@pytest.mark.parametrize("slug", ["nes", "gb", "psx", "dos", "zxs", "amiga"])
def test_known_platforms_play(slug):
    assert is_playable(slug)
    assert verdict_for(slug).verdict == PLAYS
    assert verdict_for(slug).plays


@pytest.mark.parametrize(
    "slug", ["dc", "vectrex", "pokemon-mini", "tic-80", "wasm-4", "zx81", "scummvm"]
)
def test_platforms_with_no_core_are_catalogue_only(slug):
    """The six the audit named as having no equivalent, plus ScummVM.

    Named individually rather than derived, because these are the ones a
    future "just map it to something" patch would touch.
    """
    assert not is_playable(slug)
    assert not is_playable(slug, netplay=True)
    assert verdict_for(slug).verdict == CATALOGUE_ONLY
    assert verdict_for(slug).cores == ()


@pytest.mark.parametrize("slug", ["3ds", "new-nintendo-3ds", "intellivision"])
def test_nightly_platforms_need_netplay(slug):
    assert not is_playable(slug)
    assert is_playable(slug, netplay=True)
    result = verdict_for(slug)
    assert result.verdict == NEEDS_NETPLAY
    # False on purpose: the promise `plays` makes is unconditional.
    assert not result.plays


def test_netplay_merges_rather_than_extends():
    """RomM spreads the nightly object over the base one, so a shared slug
    is *replaced*. `snes` is the case that shows it."""
    assert cores_for("snes") == ("snes9x",)
    assert cores_for("snes", netplay=True) == ("snes9x", "bsnes")


def test_lookup_is_case_and_space_insensitive():
    assert cores_for("  NES  ") == cores_for("nes")
    assert verdict_for("DC").verdict == CATALOGUE_ONLY


@pytest.mark.parametrize("value", [None, "", 42, [], {}])
def test_junk_is_catalogue_only_rather_than_a_crash(value):
    assert not is_playable(value)
    assert verdict_for(value).verdict == CATALOGUE_ONLY


# -- the warning ---------------------------------------------------------


def test_a_playable_platform_produces_no_warning():
    assert import_warning("nes") == ""


def test_a_dead_platform_names_itself_and_says_what_happens():
    message = import_warning("dc")
    assert "'dc'" in message
    assert "do nothing when played" in message
    assert "--allow-unplayable" in message
    # It must not read as a refusal: the import is going ahead.
    assert "going ahead" in message


def test_a_nightly_platform_says_which_switch_it_needs():
    message = import_warning("3ds")
    assert "'3ds'" in message
    assert "netplay" in message
    assert "azahar" in message


def test_the_warning_is_ascii():
    """A Windows console defaults to cp1252 and this project has been bitten
    once already by a non-encodable character in CLI output."""
    for slug in ["dc", "3ds", "vectrex", "scummvm"]:
        import_warning(slug).encode("ascii")


def test_the_romm_version_is_stated():
    """A vendored copy that does not say what it is a copy of cannot be
    checked for staleness."""
    assert playability.ROMM_VERSION == "4.9.2"
