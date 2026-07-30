"""libretro-cheats, replayed against captured GitHub tree listings.

`tests/fixtures/libretro_cheats/` holds two verbatim Git Trees API
bodies, captured 2026-07-29:

* `tree_cht.json` -- the 44 system directories under `cht/`, 12 KB. The
  call the first run makes to tell an operator what to choose from.
* `tree_gameboy.json` -- `cht/Nintendo - Game Boy`, 1,496 cheat files,
  473 KB.

Together they are the evidence for the size claim: 485 KB of JSON reaches
a catalogue inside a repository that is 795 MB to clone, and the Game Boy
listing is 1,496 entries -- which the contents API would have silently
capped at 1,000.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-cheats"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro_cheats"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_cheats.assets import (  # noqa: E402
    LICENSE,
    MAX_ASSETS,
    Assets,
    CheatListError,
    NeedsNarrowing,
    UnknownCheat,
    UnknownSystem,
)

from rom_hub.types import AssetArtifact, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

CHT = (FIXTURES / "tree_cht.json").read_text(encoding="utf-8")
GAMEBOY = (FIXTURES / "tree_gameboy.json").read_text(encoding="utf-8")

GB = "Nintendo - Game Boy"
#: Counted from the captured body. Notably over the contents API's silent
#: 1,000-entry cap, which is why this plugin uses the Trees API.
GAMEBOY_COUNT = 1496
SYSTEM_COUNT = 44


class FakeHttp:
    def __init__(self, bodies=None, status=200):
        self._bodies = bodies or {
            "master:cht/Nintendo%20-%20Game%20Boy": GAMEBOY,
            "master:cht": CHT,
        }
        self.status = status
        self.urls: list[str] = []

    def get(self, url, params=None):
        self.urls.append(url)
        # Longest fragment first, so "master:cht" does not shadow
        # "master:cht/Nintendo%20-%20Game%20Boy".
        for fragment in sorted(self._bodies, key=len, reverse=True):
            if fragment in url:
                return HttpResponse(
                    status_code=self.status, text=self._bodies[fragment]
                )
        return HttpResponse(status_code=404, text='{"message":"Not Found"}')


def _assets(config=None, http=None):
    http = http or FakeHttp()
    return Assets(PluginContext(config=config or {}, http=http)), http


# --- the first run ------------------------------------------------------


def test_with_no_systems_it_names_the_ones_that_exist():
    """Not an empty list. An empty catalogue would be technically true and
    useless; the message is the instructions."""
    assets, http = _assets()
    with pytest.raises(NeedsNarrowing) as exc:
        assets.list()
    message = str(exc.value)
    assert "`systems` config key" in message
    assert "Nintendo - Game Boy" in message
    assert "Sony - PlayStation" in message


def test_the_first_run_costs_exactly_one_request():
    """The point of answering with the real list rather than a guess is
    that it is cheap enough to always do."""
    assets, http = _assets()
    with pytest.raises(NeedsNarrowing):
        assets.list()
    assert len(http.urls) == 1
    assert http.urls[0].endswith("master:cht")


def test_the_repository_holds_the_systems_we_think_it_does():
    assets, _ = _assets()
    with pytest.raises(NeedsNarrowing) as exc:
        assets.list()
    # The captured tree is 44 directories and no loose files; the plugin
    # counts only the directories, which is why this is asserted against
    # the message rather than against len(tree).
    listed = str(exc.value).split("It holds: ")[1].rstrip(".")
    assert len(listed.split(", ")) == SYSTEM_COUNT


def test_an_unknown_system_is_refused_against_the_real_list():
    assets, _ = _assets({"systems": ["Nintendo - Gameboy"]})
    with pytest.raises(UnknownSystem, match="Nintendo - Game Boy"):
        assets.list()


# --- the catalogue ------------------------------------------------------


def test_a_chosen_system_gives_its_cheat_files():
    assets, _ = _assets({"systems": [GB], "match": "zelda"})
    items = assets.list()
    assert items
    assert all(i.kind == "cheat" for i in items)
    assert all(i.system == GB for i in items)
    assert all("zelda" in i.asset_id.lower() for i in items)


def test_every_item_states_the_share_alike_licence():
    assets, _ = _assets({"systems": [GB], "match": "zelda"})
    assert {i.license for i in assets.list()} == {"CC-BY-SA-4.0"}
    assert LICENSE == "CC-BY-SA-4.0"


def test_the_asset_id_is_the_path_within_the_repository():
    assets, _ = _assets({"systems": [GB], "match": "tetris"})
    items = assets.list()
    assert items
    assert all(i.asset_id.startswith(f"cht/{GB}/") for i in items)


def test_the_captured_listing_is_past_the_contents_api_cap():
    """The trap this plugin exists to avoid, asserted against real data:
    a contents-API implementation would have returned 1,000 of these."""
    entries = [
        e
        for e in json.loads(GAMEBOY)["tree"]
        if e["type"] == "blob" and e["path"].lower().endswith(".cht")
    ]
    assert len(entries) == GAMEBOY_COUNT
    assert GAMEBOY_COUNT > 1000


def test_an_unnarrowed_big_system_names_the_match_key():
    """1,496 Game Boy cheats is over the 512 a plugin may return, so the
    real fixture exercises the real ceiling."""
    assets, _ = _assets({"systems": [GB]})
    with pytest.raises(CheatListError, match="`match` config key"):
        assets.list()


def test_match_is_case_insensitive():
    lower, _ = _assets({"systems": [GB], "match": "zelda"})
    upper, _ = _assets({"systems": [GB], "match": "ZELDA"})
    assert [i.asset_id for i in lower.list()] == [i.asset_id for i in upper.list()]


def test_every_asset_id_survives_the_wire_type():
    """These are No-Intro and GoodTools names: commas, apostrophes,
    ampersands, parentheses and square brackets. A hand-written character
    allowlist in `AssetArtifact` had already dropped `[` and `]`, which
    silently refused every `[!]`-tagged file -- so this runs a wide slice
    of the real catalogue through the wire type rather than a sample.

    `match` is set only to stay under the 512-item ceiling; 235 files is
    plenty to be representative."""
    assets, _ = _assets({"systems": [GB], "match": "the"})
    items = assets.list()
    assert len(items) > 100
    for item in items:
        AssetArtifact(**item.model_dump())


def test_the_goodtools_bracket_names_are_offered():
    """The regression that the character allowlist bug would have caused:
    these list fine only if `[` and `]` survive validation."""
    assets, _ = _assets({"systems": [GB], "match": "[!]"})
    assert assets.list()


def test_the_catalogue_is_sorted_so_a_listing_is_stable():
    assets, _ = _assets({"systems": [GB], "match": "mario"})
    ids = [i.asset_id for i in assets.list()]
    assert ids == sorted(ids, key=str.lower)


def test_a_truncated_listing_is_refused():
    http = FakeHttp(
        {
            "master:cht": CHT,
            "master:cht/Nintendo%20-%20Game%20Boy": json.dumps(
                {"tree": [], "truncated": True}
            ),
        }
    )
    assets, _ = _assets({"systems": [GB]}, http=http)
    with pytest.raises(CheatListError, match="truncated"):
        assets.list()


def test_a_non_200_is_reported_with_the_status():
    assets, _ = _assets({"systems": [GB]}, http=FakeHttp(status=503))
    with pytest.raises(CheatListError, match="503"):
        assets.list()


# --- the plan -----------------------------------------------------------


def _one(match="tetris"):
    assets, http = _assets({"systems": [GB], "match": match})
    return assets, assets.list()[0]


def test_a_plan_names_one_raw_url_and_a_bare_filename():
    assets, item = _one()
    plan = assets.plan(item)
    assert len(plan.files) == 1
    entry = plan.files[0]
    assert entry.url.startswith(
        "https://raw.githubusercontent.com/libretro/libretro-database/master/cht/"
    )
    assert "Nintendo%20-%20Game%20Boy" in entry.url
    assert bare_filename(entry.filename) == entry.filename
    assert entry.filename.endswith(".cht")


def test_the_plan_carries_the_size_from_the_listing():
    assets, item = _one()
    assert assets.plan(item).files[0].size_bytes


def test_the_plan_re_reads_the_tree_rather_than_trusting_the_artifact():
    assets, _ = _assets({"systems": [GB]})
    forged = AssetArtifact(
        asset_id=f"cht/{GB}/Not A Real Game.cht",
        name="x",
        kind="cheat",
        license="CC-BY-SA-4.0",
    )
    with pytest.raises(UnknownCheat, match="Not A Real Game.cht"):
        assets.plan(forged)


def test_a_malformed_id_is_refused_rather_than_becoming_a_url():
    assets, _ = _assets({"systems": [GB]})
    forged = AssetArtifact(
        asset_id="cht", name="x", kind="cheat", license="CC-BY-SA-4.0"
    )
    with pytest.raises(UnknownCheat, match="not a cheat id"):
        assets.plan(forged)


def test_the_plan_url_is_built_from_the_tree_not_the_artifact_name():
    assets, item = _one()
    tampered = item.model_copy(update={"name": "../../etc/passwd"})
    plan = assets.plan(tampered)
    assert "passwd" not in plan.files[0].url
    assert "passwd" not in plan.files[0].filename


def test_every_cheat_in_a_large_slice_plans_a_valid_bare_filename():
    """Real No-Intro names through the sanitiser and the wire type. One
    unrepresentable name would be an item that lists fine and fails only
    when somebody tries to install it."""
    from libretro_cheats.filenames import safe_filename

    for entry in json.loads(GAMEBOY)["tree"]:
        if entry["type"] != "blob":
            continue
        name = safe_filename(entry["path"])
        assert bare_filename(name) == name
