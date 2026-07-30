"""retroarch-autoconfig, replayed against captured GitHub tree listings.

`tests/fixtures/retroarch_autoconfig/` holds two verbatim Git Trees API
bodies, captured 2026-07-29: `udev` (437 controller profiles) and `mfi`
(1). Both are here because the pair is what proves the `driver` config
key does any work -- a plugin that ignored it would still pass a
single-fixture test while handing a Linux user Apple's profiles.

The `udev` body is also the evidence for the size claim this whole
capability rests on: 138 KB of JSON enumerates a repository that is
2.6 MB to clone, and the same mechanism enumerates libretro-database's
795 MB.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = (
    Path(__file__).resolve().parents[1] / "plugins-dev" / "retroarch-autoconfig"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "retroarch_autoconfig"
sys.path.insert(0, str(PLUGIN_ROOT))

from retroarch_autoconfig.assets import (  # noqa: E402
    DRIVERS,
    MAX_ASSETS,
    Assets,
    ProfileListError,
    UnknownDriver,
    UnknownProfile,
)
from retroarch_autoconfig.filenames import safe_filename  # noqa: E402
from retroarch_autoconfig.github import (  # noqa: E402
    TreeError,
    blobs,
    parse_tree,
    raw_url,
    tree_url,
)

from rom_hub.types import AssetArtifact, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

UDEV = (FIXTURES / "tree_udev.json").read_text(encoding="utf-8")
MFI = (FIXTURES / "tree_mfi.json").read_text(encoding="utf-8")

#: Counted from the captured bodies rather than asserted from memory.
UDEV_COUNT = 437
MFI_COUNT = 1


class FakeHttp:
    """Serves a captured body per URL. Records what was asked for."""

    def __init__(self, bodies: dict[str, str], status: int = 200):
        self._bodies = bodies
        self.status = status
        self.urls: list[str] = []

    def get(self, url, params=None):
        self.urls.append(url)
        for fragment, body in self._bodies.items():
            if fragment in url:
                return HttpResponse(status_code=self.status, text=body)
        return HttpResponse(status_code=404, text='{"message":"Not Found"}')


def _assets(config=None, bodies=None) -> tuple[Assets, FakeHttp]:
    http = FakeHttp(bodies or {"master:udev": UDEV, "master:mfi": MFI})
    ctx = PluginContext(config=config or {}, http=http)
    return Assets(ctx), http


# --- the github helper --------------------------------------------------


def test_the_tree_url_encodes_a_path_with_spaces_but_keeps_the_colon():
    """Every libretro platform directory has spaces in its name, and the
    colon is meaningful to the endpoint rather than part of the path."""
    url = tree_url("libretro", "libretro-database", "master", "cht/Sega - Saturn")
    assert url.startswith("https://api.github.com/repos/libretro/libretro-database/")
    assert "master:cht/Sega%20-%20Saturn" in url


def test_the_raw_url_encodes_everything_but_the_separators():
    url = raw_url("libretro", "x", "master", "udev/Sony PLAYSTATION(R)3.cfg")
    assert url == (
        "https://raw.githubusercontent.com/libretro/x/master/"
        "udev/Sony%20PLAYSTATION%28R%293.cfg"
    )


def test_a_truncated_listing_is_refused_not_returned_short():
    """The property the whole mechanism depends on. A catalogue quietly
    missing half its entries is worse than one that failed: nobody goes
    looking for what they were not told is absent."""
    body = json.dumps({"tree": [], "truncated": True})
    with pytest.raises(TreeError, match="truncated"):
        parse_tree(body, what="a directory")


def test_a_github_error_body_is_reported_with_its_message():
    body = json.dumps({"message": "Not Found"})
    with pytest.raises(TreeError, match="Not Found"):
        parse_tree(body, what="a directory")


def test_a_non_json_body_is_refused():
    with pytest.raises(TreeError, match="was not JSON"):
        parse_tree("<html>502</html>", what="a directory")


def test_blobs_ignores_subdirectories():
    entries = parse_tree(
        json.dumps(
            {
                "tree": [
                    {"path": "a.cfg", "type": "blob", "size": 10},
                    {"path": "img", "type": "tree"},
                ],
                "truncated": False,
            }
        ),
        what="x",
    )
    assert [e["path"] for e in blobs(entries, ".cfg")] == ["a.cfg"]


# --- the catalogue ------------------------------------------------------


def test_the_catalogue_comes_from_the_configured_driver():
    assets, http = _assets({"driver": "udev"})
    items = assets.list()
    assert len(items) == UDEV_COUNT
    assert all(i.kind == "controller" for i in items)
    assert all(i.system == "udev" for i in items)
    assert "master:udev" in http.urls[0]


def test_a_different_driver_is_a_different_catalogue():
    """The pair is the point: a plugin ignoring `driver` would pass the
    test above and still hand a Linux user Apple's profiles."""
    assets, http = _assets({"driver": "mfi"})
    items = assets.list()
    assert len(items) == MFI_COUNT
    assert "master:mfi" in http.urls[0]


def test_every_item_states_the_mit_licence():
    assets, _ = _assets()
    assert {i.license for i in assets.list()} == {"MIT"}


def test_the_asset_id_is_the_path_within_the_repository():
    assets, _ = _assets()
    items = assets.list()
    assert all(i.asset_id.startswith("udev/") for i in items)
    assert any(i.asset_id == "udev/8BitDo_ Wired_Xbox.cfg" for i in items)


def test_every_asset_id_survives_the_wire_type():
    """These ids are real filenames full of spaces, parentheses, commas
    and ampersands. If AssetArtifact refused any of them the catalogue
    would be silently short, so all 437 are checked rather than a sample."""
    assets, _ = _assets()
    for item in assets.list():
        AssetArtifact(**item.model_dump())


def test_the_size_comes_from_the_listing_not_a_download():
    assets, _ = _assets()
    items = assets.list()
    assert all(i.size_bytes and i.size_bytes > 0 for i in items)


def test_match_narrows_the_catalogue_case_insensitively():
    assets, _ = _assets({"driver": "udev", "match": "8BitDo"})
    items = assets.list()
    assert 0 < len(items) < UDEV_COUNT
    assert all("8bitdo" in i.asset_id.lower() for i in items)


def test_the_catalogue_is_sorted_so_a_listing_is_stable():
    assets, _ = _assets()
    ids = [i.asset_id for i in assets.list()]
    assert ids == sorted(ids, key=str.lower)


def test_an_unknown_driver_is_refused_by_name(monkeypatch):
    assets, http = _assets({"driver": "wayland"})
    with pytest.raises(UnknownDriver, match="udev"):
        assets.list()
    # And refused before any request goes out.
    assert http.urls == []


def test_every_declared_driver_is_a_real_directory_name():
    assert "udev" in DRIVERS and "xinput" in DRIVERS
    assert len(set(DRIVERS)) == len(DRIVERS)


def test_a_catalogue_over_the_host_limit_names_the_config_key_that_fixes_it():
    """The host refuses over MAX_ASSETS anyway; this message is what makes
    that refusal actionable instead of a dead end."""
    entries = {
        "tree": [
            {"path": f"pad{i:04d}.cfg", "type": "blob", "size": 10}
            for i in range(MAX_ASSETS + 1)
        ],
        "truncated": False,
    }
    assets, _ = _assets({"driver": "udev"}, {"master:udev": json.dumps(entries)})
    with pytest.raises(ProfileListError, match="`match` config key"):
        assets.list()


def test_a_non_200_is_reported_with_the_status():
    http = FakeHttp({"master:udev": UDEV}, status=503)
    ctx = PluginContext(config={}, http=http)
    with pytest.raises(ProfileListError, match="503"):
        Assets(ctx).list()


# --- the plan -----------------------------------------------------------


def _plan_for(asset_id, config=None):
    assets, http = _assets(config)
    item = next(i for i in assets.list() if i.asset_id == asset_id)
    return assets.plan(item), http


def test_a_plan_names_one_raw_url_and_a_bare_filename():
    plan, _ = _plan_for("udev/8BitDo_ Wired_Xbox.cfg")
    assert len(plan.files) == 1
    entry = plan.files[0]
    assert entry.url.startswith("https://raw.githubusercontent.com/libretro/")
    assert "udev/8BitDo_%20Wired_Xbox.cfg" in entry.url
    # bare_filename would have raised in FetchFile already; asserted so the
    # property is stated rather than implied.
    assert bare_filename(entry.filename) == entry.filename


def test_the_plan_re_reads_the_tree_rather_than_trusting_the_artifact():
    """The artifact arrives as a dict this plugin did not construct. A
    forged id must not become a URL."""
    assets, _ = _assets()
    forged = AssetArtifact(
        asset_id="udev/not-really-there.cfg",
        name="x",
        kind="controller",
        license="MIT",
    )
    with pytest.raises(UnknownProfile, match="not-really-there.cfg"):
        assets.plan(forged)


def test_the_plan_url_is_built_from_the_tree_not_the_artifact_name():
    """`name` is free text off the wire; it must reach no URL."""
    assets, _ = _assets()
    real = next(i for i in assets.list() if i.asset_id == "udev/8BitDo_ Wired_Xbox.cfg")
    tampered = real.model_copy(update={"name": "../../etc/passwd"})
    plan = assets.plan(tampered)
    assert "passwd" not in plan.files[0].url
    assert "passwd" not in plan.files[0].filename


def test_the_plan_carries_the_size_from_the_listing():
    plan, _ = _plan_for("udev/8BitDo_ Wired_Xbox.cfg")
    assert plan.files[0].size_bytes and plan.files[0].size_bytes > 0


def test_every_profile_in_the_catalogue_plans_a_valid_bare_filename():
    """437 real upstream names through the sanitiser and the wire type.
    A single unrepresentable name would be an item that lists fine and
    fails only when somebody tries to install it."""
    assets, _ = _assets()
    for item in assets.list():
        filename = safe_filename(item.asset_id.split("/", 1)[-1])
        assert bare_filename(filename) == filename


# --- the filename sanitiser --------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8BitDo_ Wired_Xbox.cfg", "8BitDo_ Wired_Xbox.cfg"),
        ("Sony PLAYSTATION(R)3 Controller.cfg", "Sony PLAYSTATION(R)3 Controller.cfg"),
        ("udev/nested.cfg", "nested.cfg"),
        ("../../escape.cfg", "escape.cfg"),
        ("NUL.cfg", "_NUL.cfg"),
    ],
)
def test_the_sanitiser_keeps_real_names_and_defuses_the_rest(raw, expected):
    assert safe_filename(raw) == expected


def test_the_sanitiser_is_deterministic_under_truncation():
    """FetchPlan refuses colliding filenames, so a plan must not depend on
    iteration order to be valid."""
    long = "x" * 400 + ".cfg"
    assert safe_filename(long) == safe_filename(long)
    assert len(safe_filename(long)) <= 200
    assert safe_filename(long).endswith(".cfg")
