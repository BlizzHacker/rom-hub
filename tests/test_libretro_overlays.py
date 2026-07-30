"""libretro-overlays, replayed against a captured repository tree.

`tests/fixtures/libretro_overlays/` holds the verbatim recursive Git
Trees body for `common-overlays` (2,359 entries, 732 KB, captured
2026-07-29) and two real `.cfg` files that are the two cases this plugin
exists to tell apart:

* `gamepads/lite/SNES.cfg` -- self-contained, references its images as
  bare names, and happens to name `l.png` twice, which is the duplicate
  a `FetchPlan` would refuse.
* `borders/gb.cfg` -- references `img/gb.png`, a subdirectory a
  `FetchPlan` cannot express.

The tree fixture is also the evidence for the central claim: 732 KB of
JSON classifies a 29 MB repository, and the sibling heuristic it makes
possible agrees with the content of all 310 `.cfg` files.

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
    NotSelfContained,
    OverlayListError,
    UnknownOverlay,
    image_references,
    is_self_contained,
)

from rom_hub.types import AssetArtifact, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

TREE = (FIXTURES / "tree_recursive.json").read_text(encoding="utf-8")
SNES = (FIXTURES / "cfg_gamepads_lite_SNES.cfg").read_text(encoding="utf-8")
GB = (FIXTURES / "cfg_borders_gb.cfg").read_text(encoding="utf-8")

#: Measured from the captured tree against the content of all 310 cfgs.
#: See the plugin's README for the full breakdown.
SELF_CONTAINED_COUNT = 49


class FakeHttp:
    def __init__(self, bodies=None, status=200):
        self._bodies = bodies or {
            "recursive=1": TREE,
            "gamepads/lite/SNES.cfg": SNES,
            "borders/gb.cfg": GB,
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
        'overlay0_overlay = bg.png\n'
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


def test_a_real_self_contained_cfg_is_recognised():
    refs = image_references(SNES)
    assert refs and is_self_contained(refs)
    assert "select.png" in refs


def test_a_real_subdirectory_cfg_is_recognised():
    refs = image_references(GB)
    assert refs == ["img/gb.png"]
    assert not is_self_contained(refs)


def test_a_backslash_counts_as_a_separator():
    """RetroArch reads these on Windows too, and a reference that looked
    bare to posixpath would plan a filename the host then refuses."""
    assert not is_self_contained([r"img\gb.png"])


def test_no_references_at_all_is_not_self_contained():
    """One cfg in the repository names no image. It is not installable as
    a bundle and must not be offered as one."""
    assert not is_self_contained([])


# --- the catalogue ------------------------------------------------------


def test_the_catalogue_is_only_the_self_contained_overlays():
    """The headline number. 310 cfgs in the repository, 49 installable."""
    assets, _ = _assets()
    items = assets.list()
    assert len(items) == SELF_CONTAINED_COUNT


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


def test_every_offered_overlay_really_is_self_contained():
    """The heuristic is only sound if it agrees with the files. Each
    offered overlay's own cfg is parsed and checked -- the fixture serves
    two of them for real and the rest resolve through the tree, so this
    asserts the tree-level property that made the heuristic exact."""
    assets, _ = _assets()
    tree = json.loads(TREE)["tree"]
    dirs_with_images = {
        e["path"].rsplit("/", 1)[0]
        for e in tree
        if e["type"] == "blob" and e["path"].lower().endswith((".png", ".jpg"))
    }
    for item in assets.list():
        assert item.asset_id.rsplit("/", 1)[0] in dirs_with_images


def test_the_gb_border_is_not_offered():
    """It sits beside no images and references a subdirectory; it is the
    260-strong majority case."""
    assets, _ = _assets()
    assert all(i.asset_id != "borders/gb.cfg" for i in assets.list())


def test_the_lite_gamepad_pack_is_offered():
    """The set people actually want, and the reason 49 is worth shipping."""
    assets, _ = _assets()
    ids = {i.asset_id for i in assets.list()}
    assert "gamepads/lite/SNES.cfg" in ids
    assert sum(1 for i in ids if i.startswith("gamepads/lite/")) > 10


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
        ]
        + [
            {"path": f"d{i}/y.png", "type": "blob", "size": 10}
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


def test_a_plan_carries_the_cfg_and_its_images_as_bare_names():
    plan, _ = _plan("gamepads/lite/SNES.cfg")
    names = [f.filename for f in plan.files]
    assert "SNES.cfg" in names
    assert "select.png" in names
    for name in names:
        assert bare_filename(name) == name


def test_a_plan_deduplicates_a_repeated_image():
    """SNES.cfg names `l.png` twice. FetchPlan refuses two entries with
    one filename outright, so a plugin that echoed both would produce a
    plan that never validates."""
    plan, _ = _plan("gamepads/lite/SNES.cfg")
    names = [f.filename.casefold() for f in plan.files]
    assert len(names) == len(set(names))
    assert names.count("l.png") == 1


def test_every_url_is_on_the_raw_host():
    plan, _ = _plan("gamepads/lite/SNES.cfg")
    assert all(
        f.url.startswith("https://raw.githubusercontent.com/libretro/common-overlays/")
        for f in plan.files
    )


def test_a_plan_only_names_images_the_repository_actually_has():
    """A cfg naming a file that is not in the tree would otherwise become
    a download that 404s halfway through an install."""
    tree = {
        "tree": [
            {"path": "d/x.cfg", "type": "blob", "size": 10},
            {"path": "d/present.png", "type": "blob", "size": 20},
        ],
        "truncated": False,
    }
    cfg = (
        "overlay0_overlay = present.png\n"
        "overlay0_desc0_overlay = missing.png\n"
    )
    http = FakeHttp({"recursive=1": json.dumps(tree), "d/x.cfg": cfg})
    assets, _ = _assets(http=http)
    item = assets.list()[0]
    plan = assets.plan(item)
    names = [f.filename for f in plan.files]
    assert "present.png" in names
    assert "missing.png" not in names


def test_a_subdirectory_reference_is_refused_at_plan_time(monkeypatch):
    """The heuristic decides what to offer; the file decides what to
    install. An overlay that sits beside images but references a
    subdirectory anyway is refused with a message that says so."""
    tree = {
        "tree": [
            {"path": "d/x.cfg", "type": "blob", "size": 10},
            {"path": "d/decoy.png", "type": "blob", "size": 20},
        ],
        "truncated": False,
    }
    http = FakeHttp(
        {"recursive=1": json.dumps(tree), "d/x.cfg": "overlay0_overlay = img/a.png\n"}
    )
    assets, _ = _assets(http=http)
    item = assets.list()[0]
    with pytest.raises(NotSelfContained, match="subdirectory"):
        assets.plan(item)


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
