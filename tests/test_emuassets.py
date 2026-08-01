"""The `assets` capability: the catalogue, the gate, the install, the map.

An emulator support file is a file from the internet landing on the
operator's disk, so it earns exactly the checks a ROM import does -- and
gets them by reusing `FetchPlan` and `PluginProcess`'s own gate rather
than by a second implementation that resembles it. The tests below
therefore assert the same three properties the import path asserts,
through a real plugin subprocess: **undeclared host refused**, cleartext
refused, escaping filename refused.

Two things are specific to this capability and tested here rather than
inherited.

**The kind -> directory map.** `kind` is the only reason these four kinds
of file are one capability, so a kind the host cannot place must be
impossible, and where a kind lands must be the operator's to configure.

**No backend, ever.** `install_asset` takes no backend and opens none.
That is asserted directly, because "we did not pass one" is not the same
guarantee as "one cannot be reached".

No test opens a socket.
"""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from rom_hub.backends import ALL_CAPABILITIES, BACKEND_INDEPENDENT_CAPABILITIES
from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.emuassets import (
    KIND_DIRECTORIES,
    KIND_ENV_VARS,
    AssetInstallError,
    directory_for,
    find_asset,
    install_asset,
)
from rom_hub.manifest import parse_manifest
from rom_hub.types import KNOWN_ASSET_KINDS, AssetArtifact

# --- the type -----------------------------------------------------------


def test_an_asset_needs_an_id_a_kind_and_a_licence():
    asset = AssetArtifact(
        asset_id="udev/8BitDo_ Wired_Xbox.cfg",
        name="8BitDo Wired Controller for Xbox",
        kind="controller",
        license="MIT",
        system="udev",
    )
    assert asset.kind == "controller"
    assert asset.license == "MIT"


@pytest.mark.parametrize("field", ["kind", "license"])
def test_kind_and_licence_are_not_optional(field):
    """Both are columns an operator reads while deciding. `kind` also
    decides where the file lands, so a default would be the host guessing."""
    kwargs = {
        "asset_id": "a.cfg",
        "name": "x",
        "kind": "overlay",
        "license": "CC-BY-4.0",
    }
    del kwargs[field]
    with pytest.raises(ValidationError):
        AssetArtifact(**kwargs)


def test_an_unknown_kind_is_refused_off_the_wire():
    """The host must be able to choose a destination for every kind it
    accepts; `kind="config"` would be asking it to invent one."""
    with pytest.raises(ValidationError):
        AssetArtifact(asset_id="a", name="x", kind="config", license="MIT")


@pytest.mark.parametrize(
    "evil",
    ["../escape.cfg", "a/../b", "..", "/leading", "trailing/", "a\\b", "a;b", ""],
)
def test_an_asset_id_may_not_look_like_a_traversal(evil):
    """An asset id is a path *within a source tree*, so unlike a core id it
    permits `/`. It is still never joined onto a filesystem path -- but a
    value an operator copies out of a listing should not read like an
    escape either."""
    with pytest.raises(ValidationError):
        AssetArtifact(asset_id=evil, name="x", kind="cheat", license="CC-BY-SA-4.0")


def test_an_asset_id_keeps_the_punctuation_real_filenames_have():
    """Real ids out of these sources: parentheses, commas, apostrophes,
    ampersands and spaces all occur in libretro's own file names."""
    asset = AssetArtifact(
        asset_id="cht/Nintendo - Nintendo 64/Doom 64 (USA) (Rev A).cht",
        name="Doom 64",
        kind="cheat",
        license="CC-BY-SA-4.0",
    )
    assert asset.asset_id.endswith("(Rev A).cht")


# --- find_asset ---------------------------------------------------------


def _asset(asset_id, kind="cheat"):
    return AssetArtifact(
        asset_id=asset_id, name=asset_id, kind=kind, license="CC-BY-SA-4.0"
    )


def test_find_asset_matches_exactly():
    items = [_asset("a.cht"), _asset("b.cht")]
    assert find_asset(items, "b.cht").asset_id == "b.cht"


def test_a_miss_suggests_near_matches_rather_than_the_alphabet():
    """The distinguishing problem of this capability: a catalogue is
    thousands of items long, so `cores`' "here is the whole list" would be
    a wall of text. A substring hit is what a typo actually needs."""
    items = [_asset(f"zz{i}.cht") for i in range(50)] + [_asset("Sonic.cht")]
    with pytest.raises(AssetInstallError, match="did you mean") as exc:
        find_asset(items, "sonic")
    assert "'Sonic.cht'" in str(exc.value)


def test_a_miss_with_no_near_match_truncates_and_says_how_many_more():
    items = [_asset(f"zz{i:03d}.cht") for i in range(50)]
    with pytest.raises(AssetInstallError, match="and 40 more") as exc:
        find_asset(items, "nothing-like-this")
    assert "assets list" in str(exc.value)


def test_an_empty_catalogue_says_so_plainly():
    with pytest.raises(AssetInstallError, match="nothing at all"):
        find_asset([], "x")


# --- the kind -> directory map ------------------------------------------


def test_every_known_kind_has_a_directory_and_an_override():
    """A kind the host accepts off the wire but cannot place would be a
    plugin able to reach an install with nowhere to put the bytes."""
    assert set(KIND_DIRECTORIES) == set(KNOWN_ASSET_KINDS)
    assert set(KIND_ENV_VARS) == set(KNOWN_ASSET_KINDS)


def test_the_default_leaf_names_are_retroarchs_own():
    """So that pointing ROM_HUB_ASSETS_DIR at a RetroArch config directory
    lands every file where RetroArch already looks."""
    assert KIND_DIRECTORIES == {
        "shader": "shaders",
        "overlay": "overlays",
        "cheat": "cheats",
        "controller": "autoconfig",
    }


def test_a_kind_lands_under_the_configured_root(tmp_path):
    assert directory_for("overlay", assets_dir=tmp_path) == tmp_path / "overlays"


def test_an_override_wins_outright_rather_than_being_joined(tmp_path):
    """An operator naming their real cheat directory is not describing a
    child of wherever the shaders went."""
    elsewhere = tmp_path / "somewhere" / "else"
    got = directory_for(
        "cheat", assets_dir=tmp_path, overrides={"cheat": str(elsewhere)}
    )
    assert got == elsewhere


def test_a_blank_override_is_not_an_override(tmp_path):
    """`env.get` returns "" for an unset variable, and "" must not become
    a relative path at the process's current directory."""
    got = directory_for("cheat", assets_dir=tmp_path, overrides={"cheat": "   "})
    assert got == tmp_path / "cheats"


def test_a_kind_with_no_destination_is_refused_not_defaulted(tmp_path):
    with pytest.raises(AssetInstallError, match="unknown asset kind"):
        directory_for("nonsense", assets_dir=tmp_path)


# --- the host gate, through a real plugin subprocess ---------------------

MANIFEST = """
[plugin]
slug = "assetplug"
name = "Assetplug"
version = "0.1.0"
rpp_version = "1"

[capabilities]
assets = "assets_plugin:Assets"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from rom_hub_sdk import AssetArtifact, AssetProvider, FetchFile, FetchPlan


    class Raw:
        """A plugin that skips the SDK's types entirely."""

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Assets(AssetProvider):
        def list(self):
            mode = self.ctx.config.get("mode", "good")
            if mode == "raw_bad_asset_id":
                return [Raw({"asset_id": "../../etc/passwd", "name": "x",
                             "kind": "cheat", "license": "MIT"})]
            if mode == "raw_asset_is_a_string":
                return [Raw("sonic.cht")]
            if mode == "raw_unknown_kind":
                return [Raw({"asset_id": "a", "name": "x",
                             "kind": "config", "license": "MIT"})]
            if mode == "too_many":
                return [
                    AssetArtifact(asset_id="a%d.cht" % i, name="x",
                                  kind="cheat", license="CC-BY-SA-4.0")
                    for i in range(513)
                ]
            return [
                AssetArtifact(
                    asset_id="borders/gb.cfg",
                    name="Game Boy border",
                    kind="overlay",
                    license="CC-BY-4.0",
                    system="Nintendo - Game Boy",
                    size_bytes=172,
                ),
                AssetArtifact(
                    asset_id="udev/pad.cfg",
                    name="A pad",
                    kind="controller",
                    license="MIT",
                ),
            ]

        def plan(self, asset):
            mode = self.ctx.config.get("mode", "good")

            if mode == "exfiltrate":
                return Raw({
                    "files": [{"url": "https://evil.example/x.cfg",
                               "filename": "x.cfg"}],
                    "platform": "gb",
                })

            if mode == "raw_traversal":
                return Raw({
                    "files": [{"url": "https://allowed.example/x.cfg",
                               "filename": "../../escape.cfg"}],
                    "platform": "gb",
                })

            if mode == "raw_plain_http":
                return Raw({
                    "files": [{"url": "http://allowed.example/x.cfg",
                               "filename": "x.cfg"}],
                    "platform": "gb",
                })

            if mode == "raw_subdir_filename":
                return Raw({
                    "files": [{"url": "https://allowed.example/img/gb.png",
                               "filename": "img/gb.png"}],
                    "platform": "gb",
                })

            if mode == "nested":
                return FetchPlan(
                    files=[
                        FetchFile(url="https://allowed.example/gb.cfg",
                                  filename="gb.cfg"),
                        FetchFile(url="https://allowed.example/img/a.png",
                                  filename="a.png", subdir="img"),
                        FetchFile(url="https://allowed.example/deep/img/a.png",
                                  filename="a.png", subdir="borders/lite/img"),
                    ],
                    platform="gb",
                )

            if mode.startswith("raw_subdir:"):
                return Raw({
                    "files": [{"url": "https://allowed.example/x.png",
                               "filename": "x.png",
                               "subdir": mode.split(":", 1)[1]}],
                    "platform": "gb",
                })

            if mode == "mixed":
                return FetchPlan(
                    files=[
                        FetchFile(url="https://allowed.example/a.cfg",
                                  filename="a.cfg"),
                        FetchFile(url="https://evil.example/b.png",
                                  filename="b.png"),
                    ],
                    platform="gb",
                )

            if mode == "explodes":
                raise RuntimeError("upstream said no")

            return FetchPlan(
                files=[
                    FetchFile(
                        url="https://allowed.example/" + asset.asset_id,
                        filename="gb.cfg",
                        size_bytes=172,
                    )
                ],
                platform=asset.system or "unknown",
            )
    '''
)


class NullFetcher:
    """The assets path must never touch this. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


class RecordingDownloader:
    """Writes a marker file so a test can prove where bytes would land."""

    def __init__(self):
        self.calls: list[tuple[str, Path]] = []

    def download(self, url, dest, expected_size=None):
        self.calls.append((url, Path(dest)))
        Path(dest).write_bytes(b"overlays = 1\n")

    def close(self):
        pass


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugin"
    d.mkdir()
    (d / "assets_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return d


def _proc(plugin_dir, config=None, fetcher=None):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=fetcher or NullFetcher(),
        timeout=30.0,
        # Windows cannot seccomp; the host is fail-closed by default.
        allow_unsandboxed=True,
    )


def test_the_catalogue_comes_back_validated(plugin_dir):
    with _proc(plugin_dir) as proc:
        items = proc.assets()
    assert [i.asset_id for i in items] == ["borders/gb.cfg", "udev/pad.cfg"]
    assert items[0].kind == "overlay"
    assert items[0].license == "CC-BY-4.0"


def test_a_catalogue_over_the_limit_is_refused(plugin_dir):
    with _proc(plugin_dir, {"mode": "too_many"}) as proc:
        with pytest.raises(PluginCallError, match="over the 512 limit"):
            proc.assets()


def test_an_asset_that_is_not_an_object_is_a_plugin_error(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_asset_is_a_string"}) as proc:
        with pytest.raises(PluginCallError, match="invalid asset"):
            proc.assets()


def test_an_asset_id_that_escapes_is_refused_host_side(plugin_dir):
    """The plugin bypassed AssetArtifact entirely; the host re-establishes
    it on the trusted side of the pipe."""
    with _proc(plugin_dir, {"mode": "raw_bad_asset_id"}) as proc:
        with pytest.raises(PluginCallError, match="invalid asset"):
            proc.assets()


def test_a_kind_the_host_cannot_place_is_refused_host_side(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_unknown_kind"}) as proc:
        with pytest.raises(PluginCallError, match="invalid asset"):
            proc.assets()


# --- THE allowlist tests ------------------------------------------------


def test_an_undeclared_host_is_refused(plugin_dir, tmp_path):
    """The one that matters. The plugin declares `allowed.example` and
    plans a download from `evil.example`; the host refuses the plan before
    a socket is opened, and nothing is written.

    This is `_gated_plan`, shared verbatim with the ROM, core and firmware
    paths -- so this test is also the evidence that adding a fourth
    capability did not add a fourth chance to get the gate wrong.
    """
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises(AssetInstallError, match="not permitted by manifest"):
            install_asset(
                proc, asset, assets_dir=tmp_path, downloader=downloader
            )
    assert downloader.calls == []
    assert not list(tmp_path.rglob("*.cfg"))


def test_one_undeclared_url_refuses_the_whole_plan(plugin_dir, tmp_path):
    """A plan whose first entry is legitimate must not carry a second one
    in behind it -- and no partial install may happen on the good half."""
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": "mixed"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises(AssetInstallError, match="evil.example"):
            install_asset(
                proc, asset, assets_dir=tmp_path, downloader=downloader
            )
    assert downloader.calls == []


def test_cleartext_is_refused(plugin_dir, tmp_path):
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": "raw_plain_http"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises(AssetInstallError, match="not permitted by manifest"):
            install_asset(
                proc, asset, assets_dir=tmp_path, downloader=downloader
            )
    assert downloader.calls == []


def test_a_filename_that_escapes_is_refused(plugin_dir, tmp_path):
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": "raw_traversal"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises(AssetInstallError):
            install_asset(
                proc, asset, assets_dir=tmp_path, downloader=downloader
            )
    assert downloader.calls == []
    assert not (tmp_path.parent / "escape.cfg").exists()


def test_a_subdirectory_filename_is_still_refused(plugin_dir, tmp_path):
    """`filename` was NOT widened when `subdir` arrived.

    A plugin that wants to nest says so in `subdir`, which is validated
    separately and component by component. `filename` still means one bare
    name, so a plugin that puts a path in it is refused exactly as it was
    before nesting was possible at all -- otherwise there would be two
    ways to express a destination and only one of them checked."""
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": "raw_subdir_filename"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises(AssetInstallError):
            install_asset(
                proc, asset, assets_dir=tmp_path, downloader=downloader
            )
    assert downloader.calls == []


# --- nesting, and the hostile inputs it must refuse ---------------------


def test_an_asset_may_nest_inside_its_own_install_directory(plugin_dir, tmp_path):
    """The 6x coverage gap this field exists to close.

    A RetroArch overlay is a `.cfg` that names its sprites relative to
    itself, so 260 of `common-overlays`' 310 overlays cannot be expressed
    as a flat list of bare names. They can be expressed as bare names plus
    a validated relative directory, and this is that."""
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": "nested"}) as proc:
        result = install_asset(
            proc, proc.assets()[0], assets_dir=tmp_path, downloader=downloader
        )

    root = tmp_path / "overlays" / "assetplug"
    assert result.files == [
        root / "gb.cfg",
        root / "img" / "a.png",
        root / "borders" / "lite" / "img" / "a.png",
    ]
    for path in result.files:
        assert path.is_file()
    # Two files named a.png, in two places, is the ordinary case for an
    # overlay pack -- so the plan's distinctness rule compares
    # destinations and the message prints them relatively.
    assert "img/a.png" in result.message
    assert "borders/lite/img/a.png" in result.message


#: Every one of these is refused, on every platform, by the same
#: `bare_filename` the `filename` field uses -- applied per component.
#: The list is deliberately the hostile-input list from
#: `test_fetchplan_types`, because a rule that is weaker here than there
#: would be a second, looser way to name a destination.
@pytest.mark.parametrize(
    "evil",
    [
        "..",
        "../..",
        "img/../..",
        "/etc",
        "img/",
        "/img",
        "img//deep",
        ".",
        "img/.",
        "C:evil.zip",
        "C:",
        "C:/evil",
        "\\\\server\\share",
        "img\\deep",
        "NUL",
        "img/NUL",
        "img/CON",
        "COM1",
        "img\x00",
        "img/a\x00b",
        "img/trailing.",
        "img/trailing ",
        "...",
        "a/a/a/a/a/a/a/a/a",
        "x" * 250,
    ],
)
def test_a_hostile_subdir_is_refused(plugin_dir, tmp_path, evil):
    downloader = RecordingDownloader()
    with _proc(plugin_dir, {"mode": f"raw_subdir:{evil}"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises((AssetInstallError, PluginCallError)):
            install_asset(
                proc, asset, assets_dir=tmp_path, downloader=downloader
            )
    assert downloader.calls == []
    assert not (tmp_path.parent / "x.png").exists()


def test_a_plugin_that_raises_while_planning_is_reported_not_propagated(
    plugin_dir, tmp_path
):
    with _proc(plugin_dir, {"mode": "explodes"}) as proc:
        asset = proc.assets()[0]
        with pytest.raises(AssetInstallError, match="could not plan a download"):
            install_asset(proc, asset, assets_dir=tmp_path)


# --- the install --------------------------------------------------------


def test_the_install_lands_under_kind_then_plugin_slug(plugin_dir, tmp_path):
    downloader = RecordingDownloader()
    with _proc(plugin_dir) as proc:
        asset = proc.assets()[0]
        result = install_asset(
            proc, asset, assets_dir=tmp_path, downloader=downloader
        )

    # kind -> "overlays", then one directory per plugin so two plugins
    # shipping a gb.cfg cannot overwrite each other.
    assert result.directory == tmp_path / "overlays" / "assetplug"
    assert result.files == [tmp_path / "overlays" / "assetplug" / "gb.cfg"]
    assert result.files[0].read_bytes() == b"overlays = 1\n"
    assert result.license == "CC-BY-4.0"
    assert result.kind == "overlay"


def test_the_install_honours_a_per_kind_override(plugin_dir, tmp_path):
    elsewhere = tmp_path / "retroarch" / "overlay"
    downloader = RecordingDownloader()
    with _proc(plugin_dir) as proc:
        asset = proc.assets()[0]
        result = install_asset(
            proc,
            asset,
            assets_dir=tmp_path / "unused",
            overrides={"overlay": str(elsewhere)},
            downloader=downloader,
        )
    assert result.directory == elsewhere / "assetplug"
    assert not (tmp_path / "unused").exists()


def test_the_message_names_the_licence_and_the_destination(plugin_dir, tmp_path):
    downloader = RecordingDownloader()
    with _proc(plugin_dir) as proc:
        result = install_asset(
            proc, proc.assets()[0], assets_dir=tmp_path, downloader=downloader
        )
    assert "CC-BY-4.0" in result.message
    assert "overlay" in result.message
    assert str(result.directory) in result.message


def test_the_catalogue_call_opens_no_sockets_of_its_own(plugin_dir):
    """`assets()` is a catalogue. Any network the plugin does is its own
    `ctx.http`, which is allowlist-gated; the host fetches nothing here."""
    fetcher = NullFetcher()
    with _proc(plugin_dir, fetcher=fetcher) as proc:
        proc.assets()
    assert fetcher.calls == []


# --- no backend, ever ---------------------------------------------------


def test_install_asset_takes_no_backend():
    """Asserted against the signature, not by omitting an argument: "we
    did not pass one" is a weaker claim than "one cannot be passed"."""
    import inspect

    params = inspect.signature(install_asset).parameters
    assert "backend" not in params


def test_the_installer_does_not_import_the_backends_package():
    """The strongest available form of "this capability is
    backend-independent": the module cannot reach a library server because
    it does not know the package exists."""
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rom_hub"
        / "emuassets.py"
    ).read_text(encoding="utf-8")
    assert "import rom_hub.backends" not in source
    assert "from rom_hub.backends" not in source
    assert "from .backends" not in source


def test_assets_is_classified_as_backend_independent():
    assert "assets" in BACKEND_INDEPENDENT_CAPABILITIES
    # And is not a *backend* capability -- `rom-hub backend info` must not
    # print it under "cannot" for every backend ever written.
    assert "assets" not in ALL_CAPABILITIES
