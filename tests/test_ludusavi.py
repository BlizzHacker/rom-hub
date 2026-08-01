"""The ludusavi plugin, replayed against a slice of the real manifest.

**Where the fixture comes from.** `tests/fixtures/ludusavi/manifest-slice.yaml`
is eleven entries lifted byte-for-byte out of
`mtkennerly/ludusavi-manifest` at commit `39c7638` — the same commit and
sha256 the plugin's `[[data_assets]]` pins. Only the selection is ours;
the whole file is 17,460,574 bytes and 52,886 games, which is not a thing
to check into a test suite. Each entry earns its place:

* `Prince of Persia`, `Duke Nukem 3D`, `Fallout` — DOS games with real
  save and config paths, OS conditions, and a `cloud` block
* `$1 Ride` — registry only, with an **unquoted** key containing spaces
  and a `$`
* `Accounting` and `Accounting+` — two titles, one normalised key: the
  ambiguity refusal
* `"LIFE" not found` — a store id and nothing else, which is the shape of
  30,789 of the manifest's entries, and a title whose quoting carries
  escaped quotes
* `!Anyway!`, `!LABrpgUP!` — punctuation that vanishes under normalisation
* `!4RC4N01D! 2: Retro Edition` — a quoted key carrying a colon, which a
  naive split would cut in half
* `Смешарики: Параллельные миры` — a non-Latin title, and a save path in
  Cyrillic

`Duke Nukem 3D`'s `launch` block carries `"-conf \\"..\\\\DUKE3d.conf\\" ..."`,
which is the escaped-quote scalar the key splitter has to survive.

No test opens a socket, and this plugin makes no HTTP call at runtime
either: its source is a file the host fetches and verifies before the
plugin starts.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "ludusavi"
FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "ludusavi" / "manifest-slice.yaml"
)
sys.path.insert(0, str(PLUGIN_ROOT))

from ludusavi.manifest_data import (  # noqa: E402
    ManifestUnreadable,
    find,
    parse_block,
    parse_game,
)
from ludusavi.metadata import BLOB_KEY, Metadata, NoSaveData  # noqa: E402
from ludusavi.platforms import PC_PLATFORMS  # noqa: E402
from ludusavi.titles import (  # noqa: E402
    MIN_KEY_CHARS,
    candidates,
    drop_extension,
    normalise,
    strip_decorations,
    unshuffle_article,
)

from rom_hub.types import MetadataPatch, RomRef  # noqa: E402
from rom_hub_sdk.context import DataAssetUnavailable, PluginContext  # noqa: E402


def context(config=None, assets=True) -> PluginContext:
    return PluginContext(
        config=dict(config or {}),
        # No http at all. If this plugin ever calls ctx.http, the test
        # suite finds out by AttributeError rather than by a live request.
        http=None,
        data_assets={"manifest.yaml": str(FIXTURE)} if assets else {},
    )


def rom(**kwargs) -> RomRef:
    base = {"rom_id": 1, "platform": "dos"}
    base.update(kwargs)
    return RomRef(**base)


def blob(patch: MetadataPatch) -> dict:
    return patch.raw_metadata["raw_manual_metadata"][BLOB_KEY]


# ------------------------------------------------------------ normalising


def test_normalisation_is_unicode_aware_not_ascii():
    """An ASCII character class empties every non-Latin title. Measured on
    the real manifest: 280 titles collapse into one bucket that way, so a
    rom with a non-Latin name would match all 280 at once."""
    assert normalise("Смешарики: Параллельные миры") == "смешарики параллельные миры"
    assert normalise("レイジングループ") == "レイジングループ"
    assert normalise("!Anyway!") == "anyway"


def test_an_ampersand_and_the_word_are_the_same_title():
    assert normalise("Sonic & Knuckles") == normalise("Sonic and Knuckles")


def test_bracketed_groups_always_go_and_parentheses_only_sometimes():
    assert strip_decorations("Prince of Persia (1990) [!]") == "Prince of Persia"
    assert strip_decorations("Doom (USA, Europe)") == "Doom"
    assert strip_decorations("Game (Rev A) (Disk 1 of 2)") == "Game"
    # Not decoration, so it stays -- and the key simply fails to match
    # rather than aiming at a different game.
    assert (
        strip_decorations("Dungeons & Dragons (Chronicles of Mystara)")
        == "Dungeons & Dragons (Chronicles of Mystara)"
    )


def test_the_no_intro_article_move_is_undone():
    assert unshuffle_article("Legend of Zelda, The") == "The Legend of Zelda"
    assert unshuffle_article("Bard's Tale, The - Tales of the Unknown") == (
        "The Bard's Tale - Tales of the Unknown"
    )
    assert unshuffle_article("Sonic the Hedgehog") == "Sonic the Hedgehog"


def test_only_a_real_looking_extension_is_dropped():
    assert drop_extension("Fallout.exe") == "Fallout"
    assert drop_extension("Myst.img") == "Myst"
    # `S.T.A.L.K.E.R.` must not become `S.T.A.L.K.E.R`.
    assert drop_extension("S.T.A.L.K.E.R.") == "S.T.A.L.K.E.R."
    assert drop_extension("Half-Life") == "Half-Life"


def test_a_key_shorter_than_the_floor_is_not_a_candidate():
    assert candidates(["Arc"]) == []
    assert candidates(["21"]) == []
    assert candidates(["***"]) == []
    assert MIN_KEY_CHARS == 4


def test_candidates_are_respellings_and_never_additions():
    keys = candidates(["Prince of Persia (1990) [!].zip"])
    assert keys == ["prince of persia 1990", "prince of persia"]


# ------------------------------------------------------- the YAML subset


def test_the_slice_parses_every_entry_it_contains():
    keys = {
        normalise(t)
        for t in (
            "Prince of Persia",
            "Duke Nukem 3D",
            "Fallout",
            "$1 Ride",
            "Accounting",
            '"LIFE" not found',
            "!Anyway!",
            "!4RC4N01D! 2: Retro Edition",
            "Смешарики: Параллельные миры",
            "!LABrpgUP!",
        )
    }
    found = find(str(FIXTURE), keys, normalise)
    assert set(found) == keys


def test_files_carry_their_tags_and_conditions():
    (game,) = find(str(FIXTURE), {"prince of persia"}, normalise)["prince of persia"]
    assert game.title == "Prince of Persia"
    assert [location.where for location in game.files] == [
        "<base>/CONFIG.DAT",
        "<base>/PRINCE.SAV",
        "<base>/SETUP.DAT",
    ]
    save = [f for f in game.files if "save" in f.tags]
    assert [f.where for f in save] == ["<base>/PRINCE.SAV"]
    assert save[0].when == ({"os": "dos"},)


def test_one_entry_may_carry_several_when_clauses():
    (game,) = find(str(FIXTURE), {"fallout"}, normalise)["fallout"]
    (save,) = [f for f in game.files if "save" in f.tags]
    assert save.where == "<base>/data/SAVEGAME"
    assert save.when == ({"os": "dos"}, {"os": "windows"})
    assert game.cloud == {"epic": True, "gog": True, "steam": True}
    assert game.steam_id == 38400


def test_an_unquoted_registry_key_with_spaces_and_punctuation_survives():
    (game,) = find(str(FIXTURE), {"1 ride"}, normalise)["1 ride"]
    assert not game.files
    assert [r.where for r in game.registry] == [
        "HKEY_CURRENT_USER/Software/Back to Basics Gaming/1$ Ride"
    ]
    assert set(game.registry[0].tags) == {"config", "save"}


def test_a_quoted_key_carrying_a_colon_is_not_cut_in_half():
    key = normalise("!4RC4N01D! 2: Retro Edition")
    (game,) = find(str(FIXTURE), {key}, normalise)[key]
    assert game.title == "!4RC4N01D! 2: Retro Edition"
    assert game.steam_id == 791550


def test_escaped_quotes_in_a_scalar_do_not_derail_the_parser():
    """`Duke Nukem 3D`'s launch arguments are
    `"-conf \\"..\\\\DUKE3d.conf\\" -noconsole -c"`."""
    (game,) = find(str(FIXTURE), {"duke nukem 3d"}, normalise)["duke nukem 3d"]
    assert [f.where for f in game.files] == ["<base>/DUKE3D.CFG", "<base>/GAME*.SAV"]
    assert game.cloud == {"gog": True}


def test_a_yaml_feature_outside_the_subset_is_refused_not_skipped():
    """Quietly skipping what a reader does not understand turns an upstream
    format change into wrong save paths instead of a visible failure."""
    for line in ("  files: &anchor", "  files: *alias", "  files: !!python/object"):
        with pytest.raises(ManifestUnreadable) as exc:
            parse_block([line], label="X")
        assert "does not implement" in str(exc.value)


def test_a_tab_is_refused_because_a_tab_is_not_indentation():
    with pytest.raises(ManifestUnreadable) as exc:
        parse_block(["\tfiles:"], label="X")
    assert "tab" in str(exc.value)


def test_a_line_that_is_neither_key_nor_item_is_refused():
    with pytest.raises(ManifestUnreadable):
        parse_block(["  just some text"], label="X")


def test_empty_collections_parse_as_empty():
    parsed = parse_block(["  installDir:", "    Anyway: {}", "  ids: []"])
    assert parsed == {"installDir": {"Anyway": {}}, "ids": []}


def test_a_block_that_is_not_a_mapping_is_refused():
    with pytest.raises(ManifestUnreadable) as exc:
        parse_game("X", ["  - one", "  - two"])
    assert "mapping" in str(exc.value)


# ------------------------------------------- the platform gate (the guard)


def test_the_default_platforms_are_the_pc_ones_ludusavi_describes():
    assert set(PC_PLATFORMS) == {"dos", "win", "win3x", "linux", "mac"}
    assert set(PC_PLATFORMS.values()) == {"dos", "windows", "linux", "mac"}


def test_a_console_rom_is_refused_before_any_lookup_happens():
    """`Sonic the Hedgehog` really is in the manifest -- as a PC release.
    A Mega Drive dump of the same name must never acquire its save path."""
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(
            rom(platform="genesis", name="Sonic the Hedgehog", filename="sonic.md")
        )
    message = str(exc.value)
    assert "genesis" in message
    assert "PC" in message
    assert "Nothing was written" in message


def test_a_rom_with_no_platform_is_refused():
    with pytest.raises(NoSaveData):
        Metadata(context()).enrich(rom(platform=None, name="Fallout"))


def test_the_platform_set_is_configurable_and_the_default_is_the_tight_one():
    patch = Metadata(context({"platforms": ["genesis"]})).enrich(
        rom(platform="genesis", name="Fallout")
    )
    assert blob(patch)["matched_title"] == "Fallout"


# ------------------------------------------------------------- enrichment


def test_a_dos_rom_gets_the_save_path_the_manifest_records():
    patch = Metadata(context()).enrich(
        rom(name="Prince of Persia", filename="Prince of Persia (1990) [!].zip")
    )
    payload = blob(patch)
    assert payload["matched_title"] == "Prince of Persia"
    assert payload["matched_from"] == "name"
    assert payload["save_paths"] == ["<base>/PRINCE.SAV"]
    assert {f["path"] for f in payload["files"]} == {
        "<base>/CONFIG.DAT",
        "<base>/PRINCE.SAV",
        "<base>/SETUP.DAT",
    }
    assert payload["source"].endswith("ludusavi-manifest")
    assert payload["source_license"] == "MIT"


def test_the_filename_is_used_when_the_name_does_not_match():
    patch = Metadata(context()).enrich(
        rom(name="Untitled ROM 4711", filename="Duke Nukem 3D (Rev A) [!].zip")
    )
    payload = blob(patch)
    assert payload["matched_title"] == "Duke Nukem 3D"
    assert payload["matched_from"] == "filename"
    assert payload["save_paths"] == ["<base>/GAME*.SAV"]


def test_registry_locations_are_reported_separately_from_files():
    patch = Metadata(context()).enrich(rom(name="$1 Ride"))
    payload = blob(patch)
    assert payload["files"] == []
    assert payload["registry"][0]["path"].endswith("1$ Ride")
    assert sorted(payload["registry"][0]["tags"]) == ["config", "save"]
    # `save_paths` is derived from `files` only, so it is empty rather than
    # quietly presenting a registry key as a path.
    assert payload["save_paths"] == []


def test_config_entries_are_reported_with_their_tags_never_as_saves():
    payload = blob(Metadata(context()).enrich(rom(name="Fallout")))
    config = [f for f in payload["files"] if f["tags"] == ["config"]]
    assert [f["path"] for f in config] == ["<base>/fallout.cfg"]
    assert "<base>/fallout.cfg" not in payload["save_paths"]


def test_a_non_latin_title_matches_and_keeps_its_path():
    patch = Metadata(context()).enrich(rom(name="Смешарики: Параллельные миры"))
    payload = blob(patch)
    assert payload["matched_title"] == "Смешарики: Параллельные миры"
    assert payload["save_paths"] == [
        "<winAppData>/Смешарики - Параллельные миры/settings.sav"
    ]


def test_steam_id_and_cloud_are_carried_when_the_manifest_has_them():
    payload = blob(Metadata(context()).enrich(rom(name="Fallout")))
    assert payload["steam_id"] == 38400
    assert payload["cloud"] == {"epic": True, "gog": True, "steam": True}


def test_a_game_with_neither_is_not_padded_with_empty_keys():
    payload = blob(Metadata(context()).enrich(rom(name="Prince of Persia")))
    assert "steam_id" not in payload
    assert "cloud" not in payload


# -------------------------------------------------------------- refusals


def test_an_ambiguous_key_refuses_and_names_every_candidate():
    """243 normalised keys in the real manifest are shared by two or more
    titles, covering 491 entries. This is the ordinary case, not a corner
    one."""
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(rom(name="Accounting"))
    message = str(exc.value)
    assert "'Accounting'" in message and "'Accounting+'" in message
    assert "--source-id" in message
    assert "exactly as spelled above" in message
    assert "Nothing was written" in message


def test_source_id_resolves_an_ambiguity_the_library_cannot():
    patch = Metadata(context()).enrich(
        rom(name="Accounting", extra={"source_id": "Accounting+"})
    )
    payload = blob(patch)
    assert payload["matched_title"] == "Accounting+"
    assert payload["matched_from"] == "source_id"


def test_source_id_is_used_instead_of_the_library_name_not_alongside_it():
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(
            rom(name="Fallout", extra={"source_id": "No Such Game At All"})
        )
    assert "no such game at all" in str(exc.value)
    assert "fallout" not in str(exc.value)


def test_an_entry_with_no_locations_refuses_rather_than_writing_nothing_useful():
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(rom(name='"LIFE" not found'))
    assert "records no save or config locations" in str(exc.value)


def test_a_miss_names_every_key_it_tried():
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(
            rom(name="Definitely Not A Real Game", filename="dnarg.zip")
        )
    message = str(exc.value)
    assert "'definitely not a real game'" in message
    assert "no fuzzy" in message


def test_a_title_too_short_to_be_evidence_refuses_by_saying_so():
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(rom(name="Arc", filename="arc"))
    assert f"{MIN_KEY_CHARS} characters" in str(exc.value)


def test_a_rom_with_no_name_and_no_filename_refuses():
    with pytest.raises(NoSaveData) as exc:
        Metadata(context()).enrich(rom(name="", filename=""))
    assert "keyed by title alone" in str(exc.value)


def test_a_missing_data_asset_refuses_with_the_manifest_key_to_add():
    with pytest.raises(DataAssetUnavailable) as exc:
        Metadata(context(assets=False)).enrich(rom(name="Fallout"))
    assert "[[data_assets]]" in str(exc.value)


# ---------------------------------------------- what actually gets written


def test_the_patch_touches_exactly_one_field_and_it_is_the_ungated_one():
    """RomM 4.9.2 drops a raw_*_metadata blob unless the matching provider
    id is written with it -- for seven of the eight fields. Inventing an
    hltb_id to unlock one would be putting a fabricated provider id in
    somebody's library."""
    patch = Metadata(context()).enrich(rom(name="Fallout"))
    assert set(patch.raw_metadata) == {"raw_manual_metadata"}
    assert patch.provider_ids == {}
    assert patch.name is None
    assert patch.artwork_url is None and patch.artwork_base64 is None
    assert set(patch.form_fields()) == {"raw_manual_metadata"}


def test_absent_means_leave_alone_is_preserved():
    """Everything the plugin does not know stays out of the request, so a
    curated name or igdb_id on the rom is untouched."""
    fields = Metadata(context()).enrich(rom(name="Fallout")).form_fields()
    assert list(fields) == ["raw_manual_metadata"]
    written = json.loads(fields["raw_manual_metadata"])
    assert list(written) == [BLOB_KEY]


def test_the_blob_is_namespaced_and_says_where_it_came_from():
    payload = blob(Metadata(context()).enrich(rom(name="Fallout")))
    assert payload["source"] == "https://github.com/mtkennerly/ludusavi-manifest"
    assert payload["matched_key"] == "fallout"
    assert "placeholders" in payload["note"]


def test_the_patch_is_never_empty_when_it_returns_at_all():
    patch = Metadata(context()).enrich(rom(name="Fallout"))
    assert not patch.is_empty()


# --------------------------------------------------------------- manifest


def test_the_manifest_pins_the_dataset_to_a_commit_with_a_sha256():
    from rom_hub.manifest import parse_manifest

    manifest = parse_manifest((PLUGIN_ROOT / "manifest.toml").read_text("utf-8"))
    (asset,) = manifest.data_assets
    assert asset.name == "manifest.yaml"
    assert asset.host == "raw.githubusercontent.com"
    assert len(asset.sha256) == 64
    assert asset.size_bytes == 17460574
    # A commit sha, not a branch: a floating URL and a declared hash cannot
    # both be right. The repository publishes no releases and no tags, so a
    # sha is the only immutable handle available.
    assert "/master/" not in asset.url and "/main/" not in asset.url
    assert manifest.network == ["raw.githubusercontent.com"]
    assert set(manifest.capabilities) == {"metadata"}
