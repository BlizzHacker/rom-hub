"""open-bios, against its own fixed catalogue. No test opens a socket.

The plugin makes no request at all -- its catalogue is static and the host
is what fetches -- so what there is to test is everything *around* the
bytes: that every item states a licence, that no item can be filed under
a guessed platform, that the URLs it hands over stay inside the hosts its
manifest declares, and that operator config cannot bend a URL into
something else.

The catalogue's *contents* are claims about other people's projects and
are checked in `catalogue.py` against those projects' own repositories.
What is asserted here is the shape those claims have to keep.
"""

import sys
import tomllib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "open-bios"
sys.path.insert(0, str(PLUGIN_ROOT))

from open_bios import catalogue  # noqa: E402
from open_bios.firmware import ConfigError, Firmware, UnknownFirmware  # noqa: E402
from open_bios.platforms import (  # noqa: E402
    SYSTEM_PLATFORMS,
    NeedsMapping,
    platform_for,
)

from rom_hub.manifest import parse_manifest  # noqa: E402
from rom_hub.netpolicy import url_allowed  # noqa: E402
from rom_hub.types import FirmwareArtifact  # noqa: E402

MANIFEST = parse_manifest(
    (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
)


class Ctx:
    def __init__(self, config=None):
        self.config = config or {}
        self.data_assets = {}


def _plugin(config=None) -> Firmware:
    return Firmware(Ctx(config))


# --- the manifest --------------------------------------------------------


def test_the_manifest_declares_firmware_and_nothing_else():
    assert MANIFEST.slug == "open-bios"
    assert set(MANIFEST.capabilities) == {"firmware"}
    assert MANIFEST.romm_api == []


def test_the_declared_default_matches_the_code_default():
    """Two places name the SameBoy release: the manifest (which the host
    passes in as config) and the module (the fallback when config carries
    nothing). A disagreement would mean the plugin fetched one release and
    the README documented another."""
    raw = tomllib.loads((PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8"))
    declared = raw["config"]["sameboy_release"]["default"]
    assert declared == catalogue.DEFAULT_SAMEBOY_RELEASE


# --- every URL stays inside the allowlist --------------------------------


def test_every_planned_url_is_permitted_by_this_plugin_s_own_manifest():
    """The host checks this at install time and would refuse a violation.
    Asserting it here means the refusal is never how anyone finds out."""
    plugin = _plugin()
    for item in plugin.list():
        plan = plugin.plan(item)
        for entry in plan.files:
            assert url_allowed(entry.url, MANIFEST.network), entry.url


def test_the_declared_hosts_are_all_actually_used():
    """An allowlist entry nobody needs is permission granted for nothing.

    `release-assets.githubusercontent.com` is the exception and is
    deliberate: a GitHub release download 302s there, and the Hub
    re-checks every hop, so the redirect target has to be declared even
    though no planned URL names it.
    """
    plugin = _plugin()
    planned = {
        entry.url for item in plugin.list() for entry in plugin.plan(item).files
    }
    used = {
        host
        for host in MANIFEST.network
        if any(f"//{host}/" in url for url in planned)
    }
    assert used == {"raw.githubusercontent.com", "github.com"}
    assert "release-assets.githubusercontent.com" in MANIFEST.network


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.example/bios.bin",
        "http://raw.githubusercontent.com/x/bios.bin",
    ],
)
def test_an_undeclared_or_cleartext_url_would_not_be_permitted(evil):
    """The gate itself, stated against this plugin's real allowlist."""
    assert not url_allowed(evil, MANIFEST.network)


# --- the catalogue -------------------------------------------------------


def test_every_item_states_a_licence():
    """The entire value of this plugin. An item without one is an item an
    operator cannot tell apart from a dump."""
    for item in _plugin().list():
        assert item.license.strip(), item.firmware_id


def test_every_item_names_its_upstream_project():
    """So the licence claim is checkable rather than asserted."""
    for item in _plugin().list():
        assert "https://github.com/" in (item.description or "")


def test_every_item_is_a_valid_artifact_and_has_a_platform():
    items = _plugin().list()
    assert {i.firmware_id for i in items} == {
        "cult-of-gba",
        "sameboy-dmg",
        "sameboy-cgb",
    }
    for item in items:
        assert isinstance(item, FirmwareArtifact)
        assert item.platform in set(SYSTEM_PLATFORMS.values())


def test_the_archive_items_declare_the_members_they_want():
    items = {i.firmware_id: i for i in _plugin().list()}
    assert items["sameboy-dmg"].archive == "zip"
    assert items["sameboy-dmg"].members == ["dmg_boot.bin", "mgb_boot.bin"]
    assert items["sameboy-cgb"].members == [
        "cgb_boot.bin",
        "cgb0_boot.bin",
        "agb_boot.bin",
    ]
    # And the direct one declares none, so nothing is unpacked.
    assert items["cult-of-gba"].archive is None


def test_the_gba_bios_is_planned_at_a_pinned_commit_with_its_size():
    """A branch name in a firmware URL means the bytes can move under a
    plugin that claims to know what it serves."""
    plugin = _plugin()
    item = next(i for i in plugin.list() if i.firmware_id == "cult-of-gba")
    entry = plugin.plan(item).files[0]
    assert catalogue.CULT_OF_GBA_COMMIT in entry.url
    assert "/master/" not in entry.url
    assert entry.filename == "gba_bios.bin"
    assert entry.size_bytes == catalogue.GBA_BIOS_BYTES


def test_an_archive_item_plans_exactly_one_download():
    """The host refuses an archive item that plans more than one file."""
    plugin = _plugin()
    for item in plugin.list():
        if item.archive:
            assert len(plugin.plan(item).files) == 1


def test_planning_an_item_this_plugin_does_not_have_says_what_it_does():
    plugin = _plugin()
    stranger = FirmwareArtifact(
        firmware_id="psx-openbios", name="x", platform="gba", license="GPL-2.0"
    )
    with pytest.raises(UnknownFirmware, match="cult-of-gba"):
        plugin.plan(stranger)


def test_plan_re_reads_the_catalogue_rather_than_trusting_the_artifact():
    """The artifact made a round trip through the host and the operator's
    command line. Building a URL out of its fields would be building one
    out of a value this plugin did not construct."""
    plugin = _plugin()
    item = next(i for i in plugin.list() if i.firmware_id == "cult-of-gba")
    tampered = item.model_copy(update={"platform": "n64", "license": "Proprietary"})
    plan = plugin.plan(tampered)
    assert plan.platform == "gba"
    assert catalogue.CULT_OF_GBA_COMMIT in plan.files[0].url


# --- platforms -----------------------------------------------------------


def test_an_unmapped_system_needs_mapping_and_names_itself():
    with pytest.raises(NeedsMapping, match="PlayStation"):
        platform_for("PlayStation")


def test_the_refusal_names_what_the_table_does_know():
    """A refusal an operator cannot act on is a dead end."""
    with pytest.raises(NeedsMapping, match="Game Boy Advance"):
        platform_for("Nintendo DS")


def test_every_catalogue_system_is_mapped():
    """A source added without a mapping row fails at `firmware list`,
    loudly, rather than shipping an item that cannot be filed."""
    for source in catalogue.SOURCES:
        assert platform_for(source.system)


def test_the_three_game_boy_generations_are_not_folded_together():
    """They take different boot ROMs and an emulator will not accept one
    for another, so "close enough" is exactly the silent failure this
    table exists to prevent."""
    slugs = [SYSTEM_PLATFORMS[s] for s in SYSTEM_PLATFORMS]
    assert len(set(slugs)) == len(slugs)


# --- configuration -------------------------------------------------------


def test_a_newer_release_tag_moves_the_asset_url():
    plugin = _plugin({"sameboy_release": "v1.0.4"})
    item = next(i for i in plugin.list() if i.firmware_id == "sameboy-dmg")
    entry = plugin.plan(item).files[0]
    assert entry.url.endswith("/v1.0.4/sameboy_winsdl_v1.0.4.zip")
    assert entry.filename == "sameboy_winsdl_v1.0.4.zip"
    assert url_allowed(entry.url, MANIFEST.network)


@pytest.mark.parametrize(
    "evil",
    [
        "../../../../etc/passwd",
        "v1.0.3/../../../..",
        "v1.0.3?x=1",
        "v1 0 3",
        "https://evil.example/x",
    ],
)
def test_a_release_tag_that_is_not_a_tag_is_refused_by_name(evil):
    """It becomes part of a URL path and of a filename. The host's
    allowlist catches a host swap; this catches the path."""
    plugin = _plugin({"sameboy_release": evil})
    with pytest.raises(ConfigError, match="not a plausible git tag"):
        plugin.list()


def test_a_bad_tag_is_caught_by_list_not_only_by_install():
    """So an operator learns before they pick something."""
    with pytest.raises(ConfigError):
        _plugin({"sameboy_release": "a/b"}).list()


def test_an_empty_release_falls_back_to_the_verified_one():
    plugin = _plugin({"sameboy_release": ""})
    item = next(i for i in plugin.list() if i.firmware_id == "sameboy-dmg")
    assert item.version == catalogue.DEFAULT_SAMEBOY_RELEASE


def test_listing_makes_no_request(monkeypatch):
    """The catalogue is static, which is why `firmware list` works offline.

    The context has no `http` at all here, so any attempt to use one is an
    AttributeError rather than a silently-passing test.
    """
    items = _plugin().list()
    assert len(items) == 3
