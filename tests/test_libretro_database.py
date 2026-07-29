"""The libretro-database `metadata` capability.

**Where the fixtures come from.** Both `.dat` files under
`fixtures/libretro_database/` are real captures from
`raw.githubusercontent.com/libretro/libretro-database/master/metadat/`,
taken 2026-07-29. The `clrmamepro` headers and every `game (...)` block
are byte-for-byte as served; only the *selection* is ours, and each entry
is there for a reason a test below states.

The whole files are 526,473 bytes (Game Boy, no-intro) and 3,909,142
bytes (PlayStation, redump), which is why they are sliced rather than
checked in.

No test opens a socket: the DAT arrives through a fake `ctx.http`.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-database"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_database.clrmamepro import (  # noqa: E402
    DatError,
    index_by_filename,
    index_by_hash,
    parse,
)
from libretro_database.metadata import (  # noqa: E402
    RAW,
    FetchFailed,
    Metadata,
    NoMatch,
)
from libretro_database.systems import SYSTEMS, NeedsMapping, dats_for  # noqa: E402

from rom_hub.types import RomRef  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro_database"
GAME_BOY = (FIXTURES / "nintendo-game-boy.dat").read_text(encoding="utf-8")
PLAYSTATION = (FIXTURES / "sony-playstation.dat").read_text(encoding="utf-8")

TETRIS_CRC = "46DF91AD"
TETRIS_MD5 = "982ED5D2B12A0377EB14BCDC4123744E"
TETRIS_SHA1 = "74591CC9501AF93873F9A5D3EB12DA12C0723BBC"


class FakeRaw:
    """Answers like raw.githubusercontent.com does, from real DAT text."""

    def __init__(self, bodies=None, status_code=200, raises=None):
        self.bodies = bodies if bodies is not None else {"no-intro": GAME_BOY}
        self.status_code = status_code
        self.raises = raises
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        for key, body in self.bodies.items():
            if f"/metadat/{key}/" in url:
                return HttpResponse(status_code=self.status_code, text=body)
        return HttpResponse(status_code=404, text="404: Not Found")


def _provider(http=None, **config):
    http = http or FakeRaw()
    return Metadata(PluginContext(config=dict(config), http=http)), http


def _ref(**kwargs):
    base = {
        "rom_id": 7,
        "name": "Tetris (World) (Rev 1)",
        "filename": "Tetris (World) (Rev 1).gb",
        "platform": "gb",
        "extra": {},
    }
    base.update(kwargs)
    return RomRef(**base)


# -- the parser ----------------------------------------------------------


def test_the_header_carries_the_system_the_dat_names_itself():
    header, _games = parse(GAME_BOY)
    assert header["name"] == "Nintendo - Game Boy"


def test_a_game_title_and_a_rom_filename_are_kept_apart():
    """The entire design. In the real Game Boy set the game titled
    `14 Juillet (World) (Fr)` has a rom named
    `14 Juillet (World) (Fr) (Aftermarket) (Unl).gb`, and writing the
    second into a library as a name is the mistake libretro-thumbnails
    documented rather than made."""
    _header, games = parse(GAME_BOY)
    juillet = next(g for g in games if g.title.startswith("14 Juillet"))
    assert juillet.title == "14 Juillet (World) (Fr)"
    assert juillet.roms[0]["name"] == "14 Juillet (World) (Fr) (Aftermarket) (Unl).gb"
    assert juillet.title != juillet.roms[0]["name"]


def test_parentheses_inside_a_quoted_value_do_not_end_the_block():
    _header, games = parse(GAME_BOY)
    assert "Tetris (World) (Rev 1)" in {g.title for g in games}


def test_an_apostrophe_in_a_title_survives():
    _header, games = parse(GAME_BOY)
    assert "Kirby's Dream Land (USA, Europe)" in {g.title for g in games}


def test_a_redump_entry_carries_its_serial():
    _header, games = parse(PLAYSTATION)
    ridge = next(g for g in games if g.title.startswith("Ridge Racer"))
    assert ridge.serial == "SCES-00001"
    assert ridge.roms[0]["serial"] == "SCES-00001"


def test_hashes_are_indexed_upper_case_as_the_dat_writes_them():
    _header, games = parse(GAME_BOY)
    index = index_by_hash(games)
    assert index[("sha1", TETRIS_SHA1)][0].title == "Tetris (World) (Rev 1)"
    assert index[("crc", TETRIS_CRC)][0].title == "Tetris (World) (Rev 1)"


def test_the_filename_index_holds_both_the_rom_name_and_its_stem():
    _header, games = parse(GAME_BOY)
    index = index_by_filename(games)
    assert "TETRIS (WORLD) (REV 1).GB" in index
    assert "TETRIS (WORLD) (REV 1)" in index


def test_text_that_is_not_a_dat_is_refused():
    with pytest.raises(DatError, match="not a clrmamepro DAT"):
        parse("<html><body>404: Not Found</body></html>")


# -- resolving a name ----------------------------------------------------


def test_the_catalogue_title_is_written_and_never_the_rom_filename():
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.name == "Tetris (World) (Rev 1)"


def test_the_aftermarket_rom_resolves_to_the_plain_game_title():
    """The rom file is `... (Aftermarket) (Unl).gb`; the game is not."""
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(filename="14 Juillet (World) (Fr) (Aftermarket) (Unl).gb", name="")
    )
    assert patch.name == "14 Juillet (World) (Fr)"


def test_a_rom_is_found_by_sha1_md5_or_crc():
    for digest in (TETRIS_SHA1, TETRIS_MD5, TETRIS_CRC):
        provider, _ = _provider()
        patch = provider.enrich(
            _ref(filename="my-own-name.gb", name="", extra={"source_id": digest})
        )
        assert patch.name == "Tetris (World) (Rev 1)", digest


def test_a_lower_case_digest_matches_the_upper_case_dat():
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(filename="x.gb", name="", extra={"source_id": TETRIS_SHA1.lower()})
    )
    assert patch.name == "Tetris (World) (Rev 1)"


def test_a_hash_from_the_host_is_used_when_there_is_no_source_id():
    provider, _ = _provider()
    assert (
        provider.enrich(_ref(filename="x.gb", name="", extra={"md5": TETRIS_MD5})).name
        == "Tetris (World) (Rev 1)"
    )


def test_a_filename_match_is_exact_and_never_a_prefix():
    """`Tetris 2 (USA)` is in the fixture for exactly this."""
    provider, _ = _provider()
    assert provider.enrich(_ref(filename="Tetris 2 (USA).gb", name="")).name == (
        "Tetris 2 (USA)"
    )


def test_the_extension_may_differ_between_library_and_dat():
    provider, _ = _provider()
    assert provider.enrich(_ref(filename="Tetris (World) (Rev 1).zip")).name == (
        "Tetris (World) (Rev 1)"
    )


def test_a_hash_that_misses_does_not_fall_back_to_the_filename():
    """The operator named a specific dump. Answering about a different one
    because its *name* happens to match is answering a question nobody
    asked."""
    provider, _ = _provider()
    with pytest.raises(NoMatch, match="sha1:"):
        provider.enrich(_ref(extra={"source_id": "0" * 40}))


def test_set_name_false_leaves_the_patch_empty():
    provider, _ = _provider(set_name=False)
    patch = provider.enrich(_ref())
    assert patch.is_empty()


def test_the_patch_sets_a_name_and_touches_nothing_else():
    """No artwork -- a DAT contains no images. No provider ids."""
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.form_fields() == {"name": "Tetris (World) (Rev 1)"}
    assert patch.artwork_url is None
    assert patch.provider_ids == {}
    assert patch.raw_metadata == {}


def test_libretro_id_is_never_set_because_romm_means_something_else_by_it():
    """RomM's `libretro_id_for()` defines that field as the SHA-1 of a
    libretro *thumbnail filename*, for its artwork-only libretro source.
    A DAT entry is not a thumbnail."""
    provider, _ = _provider()
    assert "libretro_id" not in provider.enrich(_ref()).provider_ids


# -- which DAT -----------------------------------------------------------


def test_the_url_names_the_set_the_file_and_the_ref():
    provider, http = _provider()
    provider.enrich(_ref())
    assert http.calls == [
        RAW + "master/metadat/no-intro/Nintendo%20-%20Game%20Boy.dat"
    ]


def test_the_ref_can_be_pinned():
    provider, http = _provider(ref="v1.9.0")
    provider.enrich(_ref())
    assert http.calls[0].startswith(RAW + "v1.9.0/metadat/")


def test_a_platform_in_both_sets_tries_redump_first_then_no_intro():
    """`psp` is the clear case: Redump has the pressed discs, No-Intro the
    digital titles."""
    assert dats_for("psp", ("no-intro", "redump"))[0][0] == "redump"


def test_a_playstation_rom_is_looked_up_in_redump():
    provider, http = _provider(FakeRaw({"redump": PLAYSTATION}))
    patch = provider.enrich(
        _ref(
            platform="psx",
            filename="Ridge Racer (Europe) (Track 01).bin",
            name="",
        )
    )
    assert patch.name == "Ridge Racer (Europe)"
    assert "/metadat/redump/Sony%20-%20PlayStation.dat" in http.calls[0]


def test_narrowing_sets_narrows_which_files_are_fetched():
    provider, http = _provider(FakeRaw({"redump": PLAYSTATION}), sets=["redump"])
    provider.enrich(
        _ref(platform="psx", filename="Ridge Racer (Europe) (Track 01).bin", name="")
    )
    assert all("/metadat/redump/" in url for url in http.calls)


def test_a_set_this_plugin_does_not_read_is_refused_by_name():
    provider, _ = _provider(sets=["tosec"])
    with pytest.raises(NoMatch, match="tosec"):
        provider.enrich(_ref())


def test_a_platform_only_in_a_set_you_excluded_says_so():
    provider, _ = _provider(sets=["no-intro"])
    with pytest.raises(NeedsMapping, match="catalogued only in"):
        provider.enrich(_ref(platform="psx"))


# -- refusals ------------------------------------------------------------


def test_an_unmapped_platform_raises_needs_mapping_and_names_itself():
    provider, _ = _provider()
    with pytest.raises(NeedsMapping, match="needs mapping: RomM platform 'dos'"):
        provider.enrich(_ref(platform="dos"))


def test_a_rom_the_dat_does_not_carry_is_refused_with_what_was_tried():
    provider, _ = _provider()
    with pytest.raises(NoMatch, match="Nonexistent Game"):
        provider.enrich(_ref(filename="Nonexistent Game (USA).gb", name=""))


def test_a_missing_dat_is_reported_as_a_rename_not_a_miss():
    provider, _ = _provider(FakeRaw({"nothing": ""}))
    with pytest.raises(FetchFailed, match="has no no-intro/Nintendo - Game Boy.dat"):
        provider.enrich(_ref())


def test_a_size_refusal_from_the_host_is_passed_through_named():
    """The largest DATs are close to the Hub's 4 MiB per-response ceiling,
    so this is a real possibility rather than a defensive branch."""
    provider, _ = _provider(
        FakeRaw(raises=RuntimeError("response exceeded the 4194304-byte limit"))
    )
    with pytest.raises(FetchFailed, match="4 MiB"):
        provider.enrich(_ref())


def test_a_body_that_is_not_a_dat_is_reported_as_such():
    provider, _ = _provider(FakeRaw({"no-intro": "<html>rate limited</html>"}))
    with pytest.raises(FetchFailed, match="did not parse as a DAT"):
        provider.enrich(_ref())


# -- the table -----------------------------------------------------------


def test_every_dat_names_a_set_this_plugin_reads():
    for slug, dats in SYSTEMS.items():
        for set_name, stem in dats:
            assert set_name in ("no-intro", "redump"), (slug, set_name)
            assert stem and not stem.endswith(".dat"), (slug, stem)


def test_the_table_is_keyed_by_real_romm_slugs():
    thumbnails = PLUGIN_ROOT.parent / "libretro-thumbnails"
    sys.path.insert(0, str(thumbnails))
    from libretro_thumbnails.systems import SYSTEMS as SLUGS  # noqa: PLC0415

    known = set(SLUGS) | {"atari-jaguar-cd"}
    assert set(SYSTEMS) <= known, sorted(set(SYSTEMS) - known)


def test_the_manifest_declares_exactly_the_one_host_that_is_fetched():
    import tomllib

    manifest = tomllib.loads(
        (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
    )
    assert manifest["permissions"]["network"] == ["raw.githubusercontent.com"]
    assert RAW.startswith("https://raw.githubusercontent.com/")
