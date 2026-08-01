"""emulators, replayed against captured GitHub release documents.

`tests/fixtures/emulators/` holds a verbatim body of
`GET https://api.github.com/repos/{owner}/{repo}/releases/latest` for
every project this plugin offers -- twelve of them, plus the Dolphin 404
body. The original four were captured 2026-07-29 and the eight added in
0.2.0 on 2026-08-01.

Every project has a fixture because one would not prove anything. The
whole risk this plugin manages is that twelve projects spell the same
machine twelve different ways, in releases that also carry installers,
debug symbols, delta-update manifests, console homebrew ports and
byte-identical duplicates under legacy names. So the tests that matter
are the ones asserting that the Linux x86_64 pattern picks
`DuckStation-x64.AppImage` out of fourteen candidates, that xemu's picks
`xemu-0.8.136-x86_64.AppImage` and not `xemu-0.8.136-dbg-x86_64.AppImage`
out of eighteen, and that no pattern anywhere ever selects a `-symbols`,
a `-pdb`, an `-installer.exe` or a `.zsync`.

The Dolphin fixture is the 404 body, which is not an error state: it is
what GitHub returns for a repository that publishes no releases, and the
plugin has to say so in those words rather than reporting a fault.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "emulators"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "emulators"
sys.path.insert(0, str(PLUGIN_ROOT))

from emulators.cores import CoreListError, Cores  # noqa: E402
from emulators.filenames import FALLBACK, MAX_CHARS, safe_filename  # noqa: E402
from emulators.projects import (  # noqa: E402
    BY_ID,
    DECLINED,
    PROJECTS,
    AmbiguousAsset,
    NoAssetForTarget,
    UnknownProject,
    project_for,
)
from emulators.releases import ReleaseError, fetch_release, parse_release  # noqa: E402
from emulators.targets import TARGETS, NeedsMapping, target_for  # noqa: E402

from rom_hub.manifest import parse_manifest  # noqa: E402
from rom_hub.netpolicy import url_allowed  # noqa: E402
from rom_hub.types import CoreArtifact, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

#: project_id -> owner/repo, taken from the table rather than restated,
#: so a project added to `projects.py` without a fixture fails loudly here
#: instead of silently falling through to the 404 body.
REPOS = {p.project_id: p.repo for p in PROJECTS}

BODIES = {
    repo: (FIXTURES / f"{pid}_latest.json").read_text("utf-8")
    for pid, repo in REPOS.items()
}
DOLPHIN_404 = (FIXTURES / "dolphin_latest_404.json").read_text("utf-8")

MANIFEST = (PLUGIN_ROOT / "manifest.toml").read_text("utf-8")

#: Asset counts in the captured releases, asserted so that a re-capture
#: that quietly loses half a document is a failing test rather than a
#: smaller fixture nobody noticed.
ASSET_COUNTS = {
    "stenzek/duckstation": 14,
    "mgba-emu/mgba": 17,
    "PCSX2/pcsx2": 6,
    "melonDS-emu/melonDS": 10,
    "ares-emulator/ares": 7,
    "hrydgard/ppsspp": 10,
    "flyinghead/flycast": 6,
    "Vita3K/Vita3K": 11,
    "xemu-project/xemu": 18,
    "cemu-project/Cemu": 4,
    "mamedev/mame": 7,
    "simple64/simple64": 1,
}


class FakeGitHub:
    """Answers `releases/latest` from the captured bodies. Records URLs."""

    def __init__(self, bodies=None, status=200):
        self.bodies = BODIES if bodies is None else bodies
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        for repo, body in self.bodies.items():
            if url == f"https://api.github.com/repos/{repo}/releases/latest":
                return HttpResponse(status_code=self.status, text=body)
        return HttpResponse(status_code=404, text=DOLPHIN_404)


def make_cores(config=None, http=None):
    http = http or FakeGitHub()
    return Cores(PluginContext(config=config or {}, http=http)), http


# --------------------------------------------------------------- fixtures


@pytest.mark.parametrize("repo,count", sorted(ASSET_COUNTS.items()))
def test_every_captured_release_parses_completely(repo, count):
    """The fixture is a whole GitHub document and the parser reads all of it."""
    release = parse_release(BODIES[repo], repo)
    assert len(release.assets) == count
    assert len(json.loads(BODIES[repo])["assets"]) == count
    assert release.tag
    assert all(a.url.startswith("https://github.com/") for a in release.assets)


def test_captured_tags_are_what_upstream_published():
    assert parse_release(BODIES["stenzek/duckstation"], "x").tag == "latest"
    assert parse_release(BODIES["mgba-emu/mgba"], "x").tag == "0.10.5"
    assert parse_release(BODIES["PCSX2/pcsx2"], "x").tag == "v2.6.3"
    assert parse_release(BODIES["melonDS-emu/melonDS"], "x").tag == "1.1"
    # Two projects do not do numbered releases at all, and printing what
    # they actually tagged is more honest than substituting a date.
    assert parse_release(BODIES["Vita3K/Vita3K"], "x").tag == "continuous"
    assert parse_release(BODIES["mamedev/mame"], "x").tag == "mame0289"


def test_every_project_in_the_table_has_a_fixture():
    """A project added without one would silently be tested against the
    Dolphin 404 body, which is a passing test that proves nothing."""
    assert set(REPOS.values()) == set(BODIES)
    assert set(ASSET_COUNTS) == set(BODIES)


# --------------------------------------------------------- asset selection


@pytest.mark.parametrize(
    "project_id,target,expected",
    [
        ("duckstation", "linux/x86_64", "DuckStation-x64.AppImage"),
        ("duckstation", "linux/aarch64", "DuckStation-arm64.AppImage"),
        (
            "duckstation",
            "windows/x86_64",
            "duckstation-windows-x64-release.zip",
        ),
        (
            "duckstation",
            "windows/arm64",
            "duckstation-windows-arm64-release.zip",
        ),
        ("duckstation", "macos/universal", "duckstation-mac-release.zip"),
        ("mgba", "linux/x86_64", "mGBA-0.10.5-appimage-x64.appimage"),
        ("mgba", "linux/aarch64", "mGBA-0.10.5-appimage-arm64.appimage"),
        ("mgba", "windows/x86_64", "mGBA-0.10.5-win64.7z"),
        ("mgba", "windows/x86", "mGBA-0.10.5-win32.7z"),
        (
            "pcsx2",
            "linux/x86_64",
            "pcsx2-v2.6.3-linux-appimage-x64-Qt.AppImage",
        ),
        ("pcsx2", "windows/x86_64", "pcsx2-v2.6.3-windows-x64-Qt.7z"),
        ("pcsx2", "macos/universal", "pcsx2-v2.6.3-macos-Qt.tar.xz"),
        ("melonds", "linux/x86_64", "melonDS-1.1-appimage-x86_64.zip"),
        ("melonds", "linux/aarch64", "melonDS-1.1-appimage-aarch64.zip"),
        ("melonds", "windows/x86_64", "melonDS-1.1-windows-x86_64.zip"),
        ("melonds", "windows/arm64", "melonDS-1.1-windows-aarch64.zip"),
        ("melonds", "macos/universal", "melonDS-1.1-macOS-universal.zip"),
    ],
)
def test_each_target_picks_exactly_the_intended_asset(project_id, target, expected):
    """The whole plugin is this table being right against real releases."""
    project = BY_ID[project_id]
    names = parse_release(BODIES[project.repo], project.repo).names()
    assert project.select(target, names) == expected


def test_no_target_of_any_project_ever_selects_a_symbols_or_installer_build():
    """The three shapes that are *not* the emulator, held out explicitly.

    DuckStation's Windows x64 release alone offers
    `duckstation-windows-x64-release.zip`,
    `duckstation-windows-x64-sse2-release.zip` and
    `duckstation-windows-x64-release-symbols.7z`. A pattern loose enough
    to be convenient picks one of the last two, and the operator finds out
    when the emulator will not start.
    """
    for project in PROJECTS:
        names = parse_release(BODIES[project.repo], project.repo).names()
        for target in project.assets:
            chosen = project.select(target, names)
            lowered = chosen.lower()
            assert "symbols" not in lowered, (project.project_id, target, chosen)
            assert "installer" not in lowered, (project.project_id, target, chosen)
            assert not lowered.endswith(".flatpak"), (project.project_id, chosen)


def test_mgba_console_ports_and_distro_builds_are_never_selected():
    """3DS, Switch, Vita, Wii and the five ubuntu64 tarballs stay out."""
    project = BY_ID["mgba"]
    names = parse_release(BODIES[project.repo], project.repo).names()
    # They really are in the release -- otherwise this asserts nothing.
    assert any("3ds" in n.lower() for n in names)
    assert any("switch" in n.lower() for n in names)
    assert any("ubuntu64" in n.lower() for n in names)
    chosen = {project.select(t, names) for t in project.assets}
    for junk in ("3ds", "switch", "vita", "wii", "ubuntu64"):
        assert not any(junk in c.lower() for c in chosen), junk


def test_mgba_offers_no_macos_target_because_its_two_dmgs_are_ambiguous():
    """Refused by name rather than guessed. Both dmgs exist upstream and
    the release says nothing about which macOS either one is for."""
    project = BY_ID["mgba"]
    names = parse_release(BODIES[project.repo], project.repo).names()
    assert sum(1 for n in names if n.endswith(".dmg")) == 2
    assert "macos/universal" not in project.assets
    with pytest.raises(NoAssetForTarget) as exc:
        project.select("macos/universal", names)
    assert "macos/universal" in str(exc.value)
    assert "guess" in str(exc.value)


def test_an_ambiguous_pattern_refuses_rather_than_choosing():
    """Two matches is a bug in the table, and it fails loudly."""
    project = BY_ID["melonds"]
    names = parse_release(BODIES[project.repo], project.repo).names()
    loosened = project.__class__(
        project_id=project.project_id,
        display=project.display,
        repo=project.repo,
        system=project.system,
        license=project.license,
        license_note=project.license_note,
        assets={"linux/x86_64": r"melonDS-.+-x86_64\.zip"},
    )
    with pytest.raises(AmbiguousAsset) as exc:
        loosened.select("linux/x86_64", names)
    assert "matched 6 assets" in str(exc.value)


def test_a_renamed_asset_refuses_rather_than_falling_back():
    project = BY_ID["pcsx2"]
    with pytest.raises(NoAssetForTarget) as exc:
        project.select("linux/x86_64", ["pcsx2-v9-linux-something-else.AppImage"])
    assert "renamed" in str(exc.value)


# ------------------------------------------------------------ the licences


@pytest.mark.parametrize(
    "project_id,expected",
    [
        ("duckstation", "CC-BY-NC-ND-4.0"),
        ("mgba", "MPL-2.0"),
        ("pcsx2", "GPL-3.0"),
        ("melonds", "GPL-3.0"),
    ],
)
def test_each_project_states_the_licence_its_own_repository_declares(
    project_id, expected
):
    """Read from `GET /repos/{owner}/{repo}/license` on 2026-07-29.

    DuckStation is the one that matters: it is *not* open source. GitHub
    reports its SPDX id as NOASSERTION because its LICENSE file is
    Creative Commons BY-NC-ND 4.0.
    """
    assert BY_ID[project_id].license == expected


def test_the_licence_reaches_the_operator_in_the_listing_not_only_the_readme():
    cores, _ = make_cores()
    listed = cores.list()
    for core in listed:
        assert f"[{BY_ID[core.core_id].license}]" in core.name
        assert "Licence:" in (core.description or "")


def test_the_listing_is_pure_ascii_so_a_cp1252_console_can_print_it():
    """A Windows console defaults to cp1252. An em dash in `name` came out
    of `cores list` as a replacement character in the middle of every row,
    which is the same problem `rom_hub.catalog.symbol_for` carries ASCII
    fallbacks for -- except a plugin has nowhere to put a fallback."""
    cores, _ = make_cores()
    for core in cores.list():
        for field in (core.core_id, core.name, core.system, core.description):
            (field or "").encode("cp1252")
            assert (field or "").isascii(), field


def test_duckstations_non_commercial_no_derivatives_terms_are_spelled_out():
    note = BY_ID["duckstation"].license_note
    assert "NonCommercial" in note
    assert "NoDerivatives" in note
    assert "NOT an open-source licence" in note


# ------------------------------------------------------------- the listing


def test_the_default_target_lists_every_project_that_builds_for_it():
    cores, http = make_cores()
    listed = cores.list()
    # ares is absent and that is the point of this list: it publishes
    # Windows and macOS builds and `ares-source.tar.gz`, so Linux is a
    # target it does not build for rather than one this plugin missed.
    # MAME and simple64 are Windows-only for the same kind of reason.
    assert [c.core_id for c in listed] == [
        "duckstation",
        "mgba",
        "pcsx2",
        "melonds",
        "ppsspp",
        "flycast",
        "vita3k",
        "xemu",
        "cemu",
    ]
    assert len(http.calls) == len(PROJECTS)
    assert all(c.startswith("https://api.github.com/repos/") for c in http.calls)


def test_a_full_listing_costs_one_api_call_per_project():
    """Worth pinning rather than leaving implicit: unauthenticated GitHub
    API requests are 60 an hour per address, so twelve projects is roughly
    five listings an hour shared with everything else on that address.
    `only` is the answer and it narrows the requests, not just the
    output."""
    cores, http = make_cores()
    cores.list()
    assert len(http.calls) == len(PROJECTS) == 12


def test_a_target_only_some_projects_build_for_lists_only_those():
    """`windows/x86` is mGBA's alone. A project that does not build for the
    configured machine is absent, not an error row."""
    cores, _ = make_cores({"target": "windows/x86"})
    assert [c.core_id for c in cores.list()] == ["mgba"]


def test_macos_universal_lists_only_the_projects_that_ship_one_fat_build():
    cores, _ = make_cores({"target": "macos/universal"})
    assert [c.core_id for c in cores.list()] == [
        "duckstation",
        "pcsx2",
        "melonds",
        "ares",
        "ppsspp",
        "flycast",
        "xemu",
    ]


def test_the_per_architecture_mac_targets_are_not_synonyms_for_universal():
    """Vita3K and Cemu publish a separate file per Mac architecture, so
    they have `macos/x86_64` cells and no `macos/universal` one. Nothing
    is listed twice under two names, which is what makes three macOS
    targets honest rather than confusing."""
    universal = {c.core_id for c in make_cores({"target": "macos/universal"})[0].list()}
    x86 = {c.core_id for c in make_cores({"target": "macos/x86_64"})[0].list()}
    arm = {c.core_id for c in make_cores({"target": "macos/arm64"})[0].list()}
    assert x86 == {"vita3k", "cemu"}
    assert arm == {"vita3k"}
    assert universal.isdisjoint(x86 | arm)


def test_only_narrows_the_catalogue():
    cores, http = make_cores({"only": ["mgba", "pcsx2"]})
    assert [c.core_id for c in cores.list()] == ["mgba", "pcsx2"]
    # And it narrows the *requests*, not just the output.
    assert len(http.calls) == 2


def test_only_naming_a_project_that_does_not_exist_is_refused_by_name():
    cores, _ = make_cores({"only": ["mgba", "snes9x"]})
    with pytest.raises(CoreListError) as exc:
        cores.list()
    assert "snes9x" in str(exc.value)


def test_one_project_being_down_does_not_empty_the_catalogue():
    """Twelve projects mean twelve independent calls. A row that says why
    is better than a listing that silently lost one of them."""
    broken = dict(BODIES)
    broken["PCSX2/pcsx2"] = "<html>502 Bad Gateway</html>"
    cores, _ = make_cores(http=FakeGitHub(bodies=broken))
    listed = cores.list()
    assert "pcsx2" in [c.core_id for c in listed]
    assert len(listed) == 9
    pcsx2 = next(c for c in listed if c.core_id == "pcsx2")
    assert "unavailable" in pcsx2.name
    assert pcsx2.version is None


def test_the_version_column_is_the_upstream_tag_verbatim():
    cores, _ = make_cores()
    versions = {c.core_id: c.version for c in cores.list()}
    assert versions["duckstation"] == "latest"
    assert versions["mgba"] == "0.10.5"
    assert versions["pcsx2"] == "v2.6.3"
    assert versions["melonds"] == "1.1"
    # Vita3K's release really is tagged `continuous`. Printing that is
    # honest about a project that does not do numbered releases, where
    # substituting a date would invent a version upstream never issued.
    assert versions["vita3k"] == "continuous"


def test_every_listed_core_validates_as_a_core_artifact():
    """The host revalidates these off the wire; a field over its bound
    would surface as an opaque protocol error at the operator's terminal."""
    cores, _ = make_cores()
    for core in cores.list():
        CoreArtifact(**core.model_dump())


# ---------------------------------------------------------------- the plan


def test_plan_returns_the_projects_own_release_url():
    cores, _ = make_cores()
    plan = cores.plan(CoreArtifact(core_id="mgba", name="mGBA"))
    assert len(plan.files) == 1
    entry = plan.files[0]
    assert entry.url == (
        "https://github.com/mgba-emu/mgba/releases/download/0.10.5/"
        "mGBA-0.10.5-appimage-x64.appimage"
    )
    assert entry.filename == "mGBA-0.10.5-appimage-x64.appimage"
    assert entry.size_bytes == 25208000
    assert plan.platform == "Game Boy Advance"


def test_plan_re_reads_the_release_rather_than_trusting_the_artifact():
    """The artifact arrives as a dict this plugin did not construct. Only
    `core_id` is used from it; everything else comes from GitHub."""
    cores, http = make_cores()
    plan = cores.plan(
        CoreArtifact(
            core_id="pcsx2",
            name="totally different",
            version="99.99",
            system="Nintendo 64",
        )
    )
    assert http.calls == [
        "https://api.github.com/repos/PCSX2/pcsx2/releases/latest"
    ]
    assert plan.files[0].filename == "pcsx2-v2.6.3-linux-appimage-x64-Qt.AppImage"
    assert plan.platform == "Sony PlayStation 2"


def test_plan_for_a_project_that_does_not_build_for_the_target_refuses():
    cores, _ = make_cores({"target": "windows/x86"})
    with pytest.raises(NoAssetForTarget) as exc:
        cores.plan(CoreArtifact(core_id="pcsx2", name="PCSX2"))
    assert "windows/x86" in str(exc.value)


def test_plan_for_an_unknown_project_names_what_exists():
    cores, _ = make_cores()
    with pytest.raises(UnknownProject) as exc:
        cores.plan(CoreArtifact(core_id="snes9x", name="snes9x"))
    assert "duckstation" in str(exc.value)


@pytest.mark.parametrize("project_id", sorted(BY_ID))
def test_every_plan_url_is_permitted_by_the_manifests_own_allowlist(project_id):
    """The host checks this before opening a socket. A plugin whose own
    plan its own manifest forbids is broken in a way tests must catch."""
    allowlist = parse_manifest(MANIFEST).network
    project = BY_ID[project_id]
    for target in project.assets:
        cores, _ = make_cores({"target": target})
        plan = cores.plan(CoreArtifact(core_id=project_id, name="x"))
        assert url_allowed(plan.files[0].url, allowlist), plan.files[0].url


@pytest.mark.parametrize("project_id", sorted(BY_ID))
def test_every_plan_filename_is_one_the_host_will_accept(project_id):
    project = BY_ID[project_id]
    for target in project.assets:
        cores, _ = make_cores({"target": target})
        plan = cores.plan(CoreArtifact(core_id=project_id, name="x"))
        assert bare_filename(plan.files[0].filename) == plan.files[0].filename


# -------------------------------------------------------------- Dolphin


def test_dolphin_is_declined_by_name_with_the_reason():
    assert "dolphin" not in BY_ID
    with pytest.raises(UnknownProject) as exc:
        project_for("dolphin")
    message = str(exc.value)
    assert "404" in message
    assert "dolphin-emu.org" in message
    assert "anti-bot" in message


def test_a_repository_with_no_releases_is_reported_as_such_not_as_an_outage():
    """GitHub's 404 here means "publishes no releases", which is a real
    state. The captured body is Dolphin's."""

    class NotFound:
        def get(self, url, params=None):
            return HttpResponse(status_code=404, text=DOLPHIN_404)

    with pytest.raises(ReleaseError) as exc:
        fetch_release(NotFound(), BY_ID["mgba"])
    assert "publishes no GitHub releases" in str(exc.value)
    assert "not an outage" in str(exc.value)


def test_rate_limiting_says_to_wait_rather_than_that_the_plugin_is_broken():
    class Throttled:
        def get(self, url, params=None):
            return HttpResponse(status_code=403, text='{"message":"rate limit"}')

    with pytest.raises(ReleaseError) as exc:
        fetch_release(Throttled(), BY_ID["mgba"])
    assert "60 per" in str(exc.value)


def test_a_200_that_is_not_json_is_refused():
    with pytest.raises(ReleaseError) as exc:
        parse_release("<html>maintenance</html>", "a/b")
    assert "not JSON" in str(exc.value)


def test_a_release_with_no_assets_is_refused():
    with pytest.raises(ReleaseError) as exc:
        parse_release(json.dumps({"tag_name": "v1", "assets": []}), "a/b")
    assert "no assets" in str(exc.value)


def test_a_hostile_asset_size_becomes_unknown_rather_than_raising():
    """`FetchFile.size_bytes` is `ge=0`. A negative or boolean size from
    upstream must not raise out of plan()."""
    body = json.dumps(
        {
            "tag_name": "v1",
            "assets": [
                {"name": "a.zip", "browser_download_url": "https://x/a", "size": -5},
                {"name": "b.zip", "browser_download_url": "https://x/b", "size": True},
            ],
        }
    )
    assert [a.size_bytes for a in parse_release(body, "a/b").assets] == [None, None]


# --------------------------------------------------------------- targets


def test_an_unknown_target_is_refused_by_name_and_never_defaulted():
    with pytest.raises(NeedsMapping) as exc:
        target_for("linux/riscv64")
    assert "linux/riscv64" in str(exc.value)
    assert "linux/x86_64" in str(exc.value)


@pytest.mark.parametrize("spelling", ["LINUX/X86_64", " linux/x86_64 ", "linux\\x86_64"])
def test_target_spelling_is_forgiving_about_case_and_separator(spelling):
    assert target_for(spelling).key == "linux/x86_64"


def test_every_declared_target_is_one_some_project_actually_builds_for():
    """A target no project ships would be a name whose only possible
    result is an empty catalogue."""
    buildable = set()
    for project in PROJECTS:
        buildable |= set(project.assets)
    assert set(TARGETS) == buildable


# ------------------------------------------------------------- filenames


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "C:evil.zip",
        "sub/dir/x.zip",
        "sub\\dir\\x.zip",
        "NUL.zip",
        "COM1.AppImage",
        "trailing. ",
        "...",
        "",
        "x" * 500 + ".tar.xz",
    ],
)
def test_sanitised_names_are_always_names_the_host_accepts(raw):
    name = safe_filename(raw)
    assert bare_filename(name) == name
    assert len(name) <= MAX_CHARS


def test_sanitising_preserves_the_compound_suffix():
    long = "y" * 400 + ".tar.xz"
    assert safe_filename(long).endswith(".tar.xz")
    assert safe_filename("z" * 400 + ".AppImage").endswith(".AppImage")


def test_sanitising_is_deterministic():
    assert safe_filename("a/b/c.zip") == safe_filename("a/b/c.zip") == "c.zip"


def test_a_name_that_sanitises_to_nothing_gets_the_fallback():
    assert safe_filename("   ...   ") == FALLBACK


# -------------------------------------------------------------- manifest


def test_the_manifest_declares_the_redirect_target_and_nothing_spare():
    """GitHub release assets 302 to release-assets.githubusercontent.com,
    and the host re-checks the allowlist on every hop -- so leaving it out
    breaks every install at the moment the bytes would arrive. Verified
    live 2026-07-29: exactly one hop, to that host."""
    manifest = parse_manifest(MANIFEST)
    assert sorted(manifest.network) == [
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
    ]
    assert url_allowed(
        "https://release-assets.githubusercontent.com/x/y", manifest.network
    )
    # No standing permission for hosts not in today's chain.
    assert not url_allowed(
        "https://objects.githubusercontent.com/x", manifest.network
    )


def test_the_manifest_declares_cores_and_asks_for_no_romm_scopes():
    manifest = parse_manifest(MANIFEST)
    assert set(manifest.capabilities) == {"cores"}
    assert manifest.romm_api == []


def test_declined_projects_are_recorded_rather_than_merely_absent():
    assert set(DECLINED) == {"dolphin", "retroarch", "bizhawk"}
    assert set(DECLINED).isdisjoint(BY_ID)


@pytest.mark.parametrize(
    "project_id,evidence",
    [
        # Each reason has to be checkable from the text, not asserted.
        ("dolphin", "404"),
        ("retroarch", "retroarch-sourceonly-1.22.2.tar.xz"),
        ("bizhawk", "minefield"),
    ],
)
def test_each_refusal_carries_its_own_evidence(project_id, evidence):
    assert evidence in DECLINED[project_id]


def test_asking_for_a_declined_project_gets_the_reason_not_a_shrug():
    for project_id in DECLINED:
        with pytest.raises(UnknownProject) as exc:
            project_for(project_id)
        assert len(str(exc.value)) > 200, project_id
