"""libretro-overlays, replayed against a captured repository tree.

`tests/fixtures/libretro_overlays/` holds the verbatim recursive Git
Trees body for `common-overlays` (2,359 entries, captured 2026-07-29) and
three real `.cfg` files covering the three shapes this plugin has to get
right:

* `gamepads/lite/SNES.cfg` -- flat, references its images as bare names,
  and happens to name `l.png` twice, which is the duplicate a `FetchPlan`
  would refuse.
* `borders/gb.cfg` -- references `img/gb.png`, one level down. The shape
  260 of the repository's 310 overlays have, and the one that used to be
  unofferable.
* `effects/scanlines/nesguy_scanlines/3x-scanlines1-1280x720.cfg` -- the
  deepest form: a cfg three directories down referencing a fourth. Its
  install path is the longest this repository produces.

The headline change these tests pin: the catalogue is **310, not 49**,
and a nested bundle installs at its own path in the tree.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-overlays"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro_overlays"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_overlays.assets import (  # noqa: E402
    LICENSE,
    MAX_ASSETS,
    Assets,
    BrokenOverlay,
    OverlayListError,
    UnknownOverlay,
    image_references,
    resolve_reference,
)
from libretro_overlays.filenames import (  # noqa: E402
    PathNotExpressible,
    check_repo_path,
    is_expressible,
    split_repo_path,
)

from rom_hub.types import AssetArtifact, FetchPlan, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

TREE = (FIXTURES / "tree_recursive.json").read_text(encoding="utf-8")
SNES = (FIXTURES / "cfg_gamepads_lite_SNES.cfg").read_text(encoding="utf-8")
GB = (FIXTURES / "cfg_borders_gb.cfg").read_text(encoding="utf-8")
NESGUY = (FIXTURES / "cfg_effects_nesguy_3x.cfg").read_text(encoding="utf-8")

#: Every `.cfg` in the captured tree. The whole repository is offered now,
#: which is what this plugin's second release is for.
CFG_COUNT = 310

NESGUY_ID = "effects/scanlines/nesguy_scanlines/3x-scanlines1-1280x720.cfg"


class FakeHttp:
    def __init__(self, bodies=None, status=200):
        self._bodies = bodies or {
            "recursive=1": TREE,
            "gamepads/lite/SNES.cfg": SNES,
            "borders/gb.cfg": GB,
            "3x-scanlines1-1280x720.cfg": NESGUY,
        }
        self.status = status
        self.urls: list[str] = []

    def get(self, url, params=None):
        self.urls.append(url)
        for fragment, body in self._bodies.items():
            if fragment in url:
                return HttpResponse(status_code=self.status, text=body)
        return HttpResponse(status_code=404, text='{"message":"Not Found"}')


def _assets(config=None, http=None):
    http = http or FakeHttp()
    return Assets(PluginContext(config=config or {}, http=http)), http


# --- the cfg parser -----------------------------------------------------


def test_it_finds_both_reference_forms():
    """The format has exactly two: whole-screen and per-control."""
    refs = image_references(
        "overlays = 1\n"
        "overlay0_overlay = bg.png\n"
        "overlay0_desc0_overlay = a.png\n"
    )
    assert refs == ["bg.png", "a.png"]


def test_it_strips_quotes_and_deduplicates_preserving_order():
    refs = image_references(
        'overlay0_overlay = "bg.png"\n'
        "overlay0_desc0_overlay = a.png\n"
        "overlay0_desc1_overlay = bg.png\n"
    )
    assert refs == ["bg.png", "a.png"]


def test_a_real_flat_cfg_parses():
    refs = image_references(SNES)
    assert "select.png" in refs
    assert all("/" not in r for r in refs)


def test_a_real_subdirectory_cfg_parses():
    assert image_references(GB) == ["img/gb.png"]


# --- resolving a reference ----------------------------------------------


def test_a_reference_resolves_against_the_cfgs_own_directory():
    assert resolve_reference("borders", "img/gb.png") == "borders/img/gb.png"
    assert resolve_reference("gamepads/lite", "a.png") == "gamepads/lite/a.png"
    assert resolve_reference("", "a.png") == "a.png"


def test_a_reference_that_leaves_the_repository_is_refused():
    """Not one of the 310 does this today, which is not a property of a
    repository taking contributions continuously."""
    with pytest.raises(BrokenOverlay, match="outside the repository"):
        resolve_reference("borders", "../../../etc/passwd")
    with pytest.raises(BrokenOverlay, match="outside the repository"):
        resolve_reference("", "../x.png")


def test_a_backslash_reference_is_refused():
    """RetroArch reads these on Windows too, so a reference that looked
    bare to posixpath would mean two things on two machines."""
    with pytest.raises(BrokenOverlay, match="backslash"):
        resolve_reference("borders", r"img\gb.png")


def test_a_reference_that_climbs_and_comes_back_is_fine():
    """`../lite/a.png` from `gamepads/flat` is inside the repository, and
    normalising it is the whole reason resolution happens here."""
    assert (
        resolve_reference("gamepads/flat", "../lite/a.png") == "gamepads/lite/a.png"
    )


# --- expressing a path verbatim -----------------------------------------


def test_a_repo_path_splits_into_a_subdir_and_a_bare_name():
    assert split_repo_path("borders/img/gb.png") == ("borders/img", "gb.png")
    assert split_repo_path("gb.png") == (None, "gb.png")


@pytest.mark.parametrize(
    "path",
    [
        "borders/img/gb.png",
        "gamepads/lite/SNES.cfg",
        "effects/scanlines/nesguy_scanlines/img/3x-scanlines1-1280x720.png",
        "Nintendo - Game Boy.cfg",
    ],
)
def test_real_repository_paths_are_expressible(path):
    assert is_expressible(path)


@pytest.mark.parametrize(
    "path",
    [
        "img/NUL.png",
        "CON/a.png",
        "img/trailing./a.png",
        "im:g/a.png",
        "img/a:b.png",
        "a/a/a/a/a/a/a/a/a/x.png",
    ],
)
def test_a_path_the_host_would_refuse_is_not_expressible(path):
    assert not is_expressible(path)


def test_the_check_names_the_offending_component():
    with pytest.raises(PathNotExpressible, match="NUL"):
        check_repo_path("img", "NUL")


# --- the catalogue ------------------------------------------------------


def test_the_whole_repository_is_offered():
    """The headline number, and the point of this release: 310 cfgs in
    the repository, 310 offered. The previous release offered 49."""
    assets, _ = _assets()
    assert len(assets.list()) == CFG_COUNT


def test_the_catalogue_needs_exactly_one_request():
    """A catalogue that read 310 cfg bodies would be 310 requests."""
    assets, http = _assets()
    assets.list()
    assert len(http.urls) == 1
    assert "recursive=1" in http.urls[0]


def test_every_item_states_the_cc_by_licence():
    assets, _ = _assets()
    assert {i.license for i in assets.list()} == {"CC-BY-4.0"}
    assert LICENSE == "CC-BY-4.0"


def test_the_overlays_that_used_to_be_dropped_are_offered_now():
    """`borders/gb.cfg` references `img/gb.png` and was the 260-strong
    majority case that the bare-name rule excluded."""
    assets, _ = _assets()
    ids = {i.asset_id for i in assets.list()}
    assert "borders/gb.cfg" in ids
    assert NESGUY_ID in ids


def test_the_flat_pack_is_still_offered():
    assets, _ = _assets()
    ids = {i.asset_id for i in assets.list()}
    assert "gamepads/lite/SNES.cfg" in ids


def test_section_narrows_the_catalogue():
    assets, _ = _assets({"section": "gamepads"})
    items = assets.list()
    assert items and all(i.asset_id.startswith("gamepads/") for i in items)


def test_the_system_column_carries_the_directory():
    assets, _ = _assets()
    item = next(i for i in assets.list() if i.asset_id == "gamepads/lite/SNES.cfg")
    assert item.system == "gamepads/lite"


def test_every_asset_id_survives_the_wire_type():
    assets, _ = _assets()
    for item in assets.list():
        AssetArtifact(**item.model_dump())


def test_the_catalogue_is_sorted_so_a_listing_is_stable():
    assets, _ = _assets()
    ids = [i.asset_id for i in assets.list()]
    assert ids == sorted(ids, key=str.lower)


def test_a_truncated_tree_is_refused():
    http = FakeHttp({"recursive=1": json.dumps({"tree": [], "truncated": True})})
    assets, _ = _assets(http=http)
    with pytest.raises(OverlayListError, match="truncated"):
        assets.list()


def test_a_non_200_is_reported_with_the_status():
    assets, _ = _assets(http=FakeHttp(status=503))
    with pytest.raises(OverlayListError, match="503"):
        assets.list()


def test_a_catalogue_over_the_limit_names_the_config_key():
    tree = {
        "tree": [
            {"path": f"d{i}/x.cfg", "type": "blob", "size": 10}
            for i in range(MAX_ASSETS + 1)
        ],
        "truncated": False,
    }
    assets, _ = _assets(http=FakeHttp({"recursive=1": json.dumps(tree)}))
    with pytest.raises(OverlayListError, match="`section` config key"):
        assets.list()


# --- the plan -----------------------------------------------------------


def _plan(asset_id, config=None):
    assets, http = _assets(config)
    item = next(i for i in assets.list() if i.asset_id == asset_id)
    return assets.plan(item), http


def test_a_flat_bundle_still_plans_flat_within_its_own_directory():
    plan, _ = _plan("gamepads/lite/SNES.cfg")
    entry = next(f for f in plan.files if f.filename == "SNES.cfg")
    assert entry.subdir == "gamepads/lite"
    # Every sprite sits beside the cfg, so every subdir is the same one.
    assert {f.subdir for f in plan.files} == {"gamepads/lite"}
    for f in plan.files:
        assert bare_filename(f.filename) == f.filename


def test_a_nested_bundle_plans_the_cfg_and_its_subdirectory():
    """The case the previous release could not express at all."""
    plan, _ = _plan("borders/gb.cfg")
    destinations = {f.relative_path() for f in plan.files}
    assert destinations == {"borders/gb.cfg", "borders/img/gb.png"}


def test_the_deepest_bundle_in_the_repository_plans():
    plan, _ = _plan(NESGUY_ID)
    destinations = sorted(f.relative_path() for f in plan.files)
    assert destinations == [
        "effects/scanlines/nesguy_scanlines/3x-scanlines1-1280x720.cfg",
        "effects/scanlines/nesguy_scanlines/img/3x-scanlines1-1280x720.png",
    ]
    # Four directory components, inside the eight a plugin may nest.
    assert max(len(f.subdir.split("/")) for f in plan.files) == 4


def test_nothing_is_renamed():
    """An overlay's cfg references its sprites by name, so a sanitised
    filename is a sprite the cfg no longer finds. Verbatim or refused."""
    plan, _ = _plan("borders/gb.cfg")
    assert [f.filename for f in plan.files] == ["gb.cfg", "gb.png"]


def test_the_plan_validates_as_a_real_fetchplan():
    """The host type is the arbiter, so it is exercised rather than
    approximated: every subdir here goes through `relative_subdir`."""
    plan, _ = _plan(NESGUY_ID)
    assert FetchPlan(**plan.model_dump()).files


def test_a_plan_deduplicates_a_repeated_image():
    """SNES.cfg names `l.png` twice. FetchPlan refuses two entries with
    one destination outright, so a plugin that echoed both would produce
    a plan that never validates."""
    plan, _ = _plan("gamepads/lite/SNES.cfg")
    paths = [f.relative_path().casefold() for f in plan.files]
    assert len(paths) == len(set(paths))
    assert sum(1 for p in paths if p.endswith("/l.png")) == 1


def test_every_url_is_on_the_raw_host():
    plan, _ = _plan("borders/gb.cfg")
    assert all(
        f.url.startswith("https://raw.githubusercontent.com/libretro/common-overlays/")
        for f in plan.files
    )


def test_a_plan_only_names_images_the_repository_actually_has():
    """Six of the 310 name at least one image the repository does not
    have; a plan carrying one would 404 halfway through an install."""
    tree = {
        "tree": [
            {"path": "d/x.cfg", "type": "blob", "size": 10},
            {"path": "d/img/present.png", "type": "blob", "size": 20},
        ],
        "truncated": False,
    }
    cfg = (
        "overlay0_overlay = img/present.png\n"
        "overlay0_desc0_overlay = img/missing.png\n"
    )
    http = FakeHttp({"recursive=1": json.dumps(tree), "d/x.cfg": cfg})
    assets, _ = _assets(http=http)
    plan = assets.plan(assets.list()[0])
    destinations = {f.relative_path() for f in plan.files}
    assert destinations == {"d/x.cfg", "d/img/present.png"}


def test_a_reference_escaping_the_repository_is_refused_at_plan_time():
    tree = {
        "tree": [
            {"path": "d/x.cfg", "type": "blob", "size": 10},
            {"path": "secret.png", "type": "blob", "size": 20},
        ],
        "truncated": False,
    }
    http = FakeHttp(
        {
            "recursive=1": json.dumps(tree),
            "d/x.cfg": "overlay0_overlay = ../../../secret.png\n",
        }
    )
    assets, _ = _assets(http=http)
    with pytest.raises(BrokenOverlay, match="outside the repository"):
        assets.plan(assets.list()[0])


def test_a_path_the_host_cannot_write_refuses_the_whole_overlay():
    """Not "install what we can": a bundle missing a sprite is a bundle
    that does not load, and a renamed one is worse."""
    tree = {
        "tree": [
            {"path": "d/x.cfg", "type": "blob", "size": 10},
            {"path": "d/NUL/a.png", "type": "blob", "size": 20},
        ],
        "truncated": False,
    }
    http = FakeHttp(
        {"recursive=1": json.dumps(tree), "d/x.cfg": "overlay0_overlay = NUL/a.png\n"}
    )
    assets, _ = _assets(http=http)
    with pytest.raises(BrokenOverlay, match="verbatim"):
        assets.plan(assets.list()[0])


def test_an_overlay_with_no_images_plans_just_its_cfg():
    """One cfg in the repository names no image at all. It is a real
    config file and installs as one, rather than being hidden."""
    tree = {"tree": [{"path": "d/x.cfg", "type": "blob", "size": 10}], "truncated": False}
    http = FakeHttp({"recursive=1": json.dumps(tree), "d/x.cfg": "overlays = 0\n"})
    assets, _ = _assets(http=http)
    plan = assets.plan(assets.list()[0])
    assert [f.relative_path() for f in plan.files] == ["d/x.cfg"]


def test_the_plan_re_reads_the_tree_rather_than_trusting_the_artifact():
    assets, _ = _assets()
    forged = AssetArtifact(
        asset_id="gamepads/lite/NOPE.cfg",
        name="x",
        kind="overlay",
        license="CC-BY-4.0",
    )
    with pytest.raises(UnknownOverlay, match="NOPE.cfg"):
        assets.plan(forged)


def test_a_bundle_over_the_file_limit_is_refused():
    """The largest real bundle is 180 files. This asserts the guard, not
    the repository."""
    from libretro_overlays.assets import MAX_FILES

    tree = {
        "tree": [{"path": "d/x.cfg", "type": "blob", "size": 10}]
        + [
            {"path": f"d/img/a{i}.png", "type": "blob", "size": 10}
            for i in range(MAX_FILES + 1)
        ],
        "truncated": False,
    }
    cfg = "".join(
        f"overlay0_desc{i}_overlay = img/a{i}.png\n" for i in range(MAX_FILES + 1)
    )
    http = FakeHttp({"recursive=1": json.dumps(tree), "d/x.cfg": cfg})
    assets, _ = _assets(http=http)
    with pytest.raises(OverlayListError, match="over the"):
        assets.plan(assets.list()[0])
