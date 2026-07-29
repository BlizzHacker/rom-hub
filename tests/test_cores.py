"""The `cores` capability: the catalogue, the gate, and the install.

A core is a binary from the internet landing on the operator's disk, so it
earns exactly the checks a ROM import does -- and gets them by reusing
`FetchPlan` and `PluginProcess`'s own gate rather than by a second
implementation that resembles it. The tests below therefore assert the
same three properties the import path asserts, through a real plugin
subprocess: undeclared host refused, cleartext refused, escaping filename
refused.

Where the bytes land is configuration. Nothing here may write outside the
directory it was given.
"""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from romm_hub.broker.host import PluginCallError, PluginProcess
from romm_hub.cores import CoreError, find_core, install_core
from romm_hub.manifest import parse_manifest
from romm_hub.types import CoreArtifact

# --- the type -----------------------------------------------------------


def test_a_core_needs_an_id_and_a_name():
    core = CoreArtifact(core_id="dosbox", name="DOSBox", version="0.74", system="dos")
    assert core.core_id == "dosbox"


@pytest.mark.parametrize("evil", ["../../etc", "a/b", "a\\b", "a b", "a;b", ""])
def test_a_core_id_is_an_identifier(evil):
    """It is typed on a command line and compared exactly; it is never a
    path component, and it should not be able to look like one."""
    with pytest.raises(ValidationError):
        CoreArtifact(core_id=evil, name="x")


def test_find_core_matches_exactly_and_names_what_exists():
    cores = [
        CoreArtifact(core_id="dosbox", name="DOSBox"),
        CoreArtifact(core_id="vice", name="VICE"),
    ]
    assert find_core(cores, "vice").name == "VICE"
    with pytest.raises(CoreError, match="dosbox, vice"):
        find_core(cores, "dosbo")


# --- the host gate, through a real plugin subprocess ---------------------

MANIFEST = """
[plugin]
slug = "coreplug"
name = "Coreplug"
version = "0.1.0"
rpp_version = "1"

[capabilities]
cores = "cores_plugin:Cores"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from romm_hub_sdk import CoreArtifact, CoreProvider, FetchFile, FetchPlan


    class Raw:
        """A plugin that skips the SDK's types entirely."""

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Cores(CoreProvider):
        def list(self):
            if self.ctx.config.get("mode") == "raw_bad_core_id":
                return [Raw({"core_id": "../../etc/passwd", "name": "x"})]
            if self.ctx.config.get("mode") == "raw_core_is_a_string":
                return [Raw("dosbox")]
            return [
                CoreArtifact(
                    core_id="dosbox",
                    name="DOSBox",
                    version="0.74-3",
                    system="dos",
                    description="MS-DOS emulator",
                ),
                CoreArtifact(core_id="vice", name="VICE", system="c64"),
            ]

        def plan(self, core):
            mode = self.ctx.config.get("mode", "good")

            if mode == "exfiltrate":
                return Raw({
                    "files": [{"url": "https://evil.example/core.wasm",
                               "filename": "core.wasm"}],
                    "platform": "dos",
                })

            if mode == "raw_traversal":
                return Raw({
                    "files": [{"url": "https://allowed.example/core.wasm",
                               "filename": "../../escape.wasm"}],
                    "platform": "dos",
                })

            if mode == "raw_plain_http":
                return Raw({
                    "files": [{"url": "http://allowed.example/core.wasm",
                               "filename": "core.wasm"}],
                    "platform": "dos",
                })

            if mode == "mixed":
                return FetchPlan(
                    files=[
                        FetchFile(url="https://allowed.example/a.wasm",
                                  filename="a.wasm"),
                        FetchFile(url="https://evil.example/b.wasm",
                                  filename="b.wasm"),
                    ],
                    platform="dos",
                )

            return FetchPlan(
                files=[
                    FetchFile(
                        url="https://allowed.example/" + core.core_id + ".wasm",
                        filename=core.core_id + ".wasm",
                        size_bytes=4,
                    )
                ],
                platform=core.system or "unknown",
            )
    '''
)


class NullFetcher:
    """The cores path must never touch this. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "cores_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return tmp_path


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
        cores = proc.cores()
    assert [c.core_id for c in cores] == ["dosbox", "vice"]
    assert cores[0].version == "0.74-3"
    assert cores[0].system == "dos"


def test_a_core_that_is_not_an_object_is_a_plugin_error(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_core_is_a_string"}) as proc:
        with pytest.raises(PluginCallError, match="invalid core"):
            proc.cores()


def test_a_catalogue_that_is_not_a_list_is_a_plugin_error(plugin_dir):
    """Reached by stubbing the call, not by a plugin.

    The runner builds its reply with a list comprehension, so a plugin
    cannot currently make this frame arrive as anything but a list -- the
    guard is against the wire, not against plugin code, and `search` has
    the identical one. Asserting it through a stub says exactly that,
    rather than leaving an untested branch in the trusted path.
    """
    proc = _proc(plugin_dir)
    proc._call = lambda method, params: {"core_id": "dosbox"}
    with pytest.raises(PluginCallError, match="expected a list"):
        proc.cores()


def test_an_enormous_catalogue_is_refused(plugin_dir):
    """The host walks whatever it is given, so the walk is bounded."""
    from romm_hub.types import MAX_CORES_PER_PLUGIN

    proc = _proc(plugin_dir)
    proc._call = lambda method, params: [
        {"core_id": f"c{i}", "name": "x"} for i in range(MAX_CORES_PER_PLUGIN + 1)
    ]
    with pytest.raises(PluginCallError, match="over the"):
        proc.cores()


def test_a_core_id_shaped_like_a_path_is_rejected_host_side(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_bad_core_id"}) as proc:
        with pytest.raises(PluginCallError, match="invalid core"):
            proc.cores()


def test_listing_cores_never_fetches_anything(plugin_dir):
    """A catalogue is a description. Nothing is downloaded to produce it."""
    fetcher = NullFetcher()
    with _proc(plugin_dir, fetcher=fetcher) as proc:
        proc.cores()
    assert fetcher.calls == []


def test_a_core_plan_is_gated_exactly_like_an_import_plan(plugin_dir):
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.core_plan(CoreArtifact(core_id="dosbox", name="DOSBox"))


def test_a_mixed_core_plan_is_rejected_as_a_whole(plugin_dir):
    with _proc(plugin_dir, {"mode": "mixed"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.core_plan(CoreArtifact(core_id="dosbox", name="DOSBox"))


def test_a_cleartext_core_url_is_rejected(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_plain_http"}) as proc:
        with pytest.raises(PluginCallError, match="allowed.example"):
            proc.core_plan(CoreArtifact(core_id="dosbox", name="DOSBox"))


def test_an_escaping_core_filename_is_rejected(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_traversal"}) as proc:
        with pytest.raises(PluginCallError, match="invalid FetchPlan"):
            proc.core_plan(CoreArtifact(core_id="dosbox", name="DOSBox"))


# --- installing ----------------------------------------------------------


class FakeDownloader:
    def __init__(self, payload=b"core"):
        self.payload = payload
        self.calls: list[tuple[str, Path]] = []

    def download(self, url, dest, expected_size=None):
        self.calls.append((url, Path(dest)))
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        return dest

    def close(self):
        pass


def test_install_writes_under_the_configured_directory(plugin_dir, tmp_path):
    target_root = tmp_path / "somewhere" / "cores"
    downloader = FakeDownloader()
    with _proc(plugin_dir) as proc:
        core = find_core(proc.cores(), "dosbox")
        result = install_core(
            proc, core, cores_dir=target_root, downloader=downloader
        )

    installed = target_root / "coreplug" / "dosbox.wasm"
    assert installed.read_bytes() == b"core"
    assert result.files == [installed]
    assert result.directory == target_root / "coreplug"
    # Every write stayed inside the directory it was given.
    for _, dest in downloader.calls:
        assert target_root.resolve() in dest.resolve().parents


def test_each_plugin_gets_its_own_directory(plugin_dir, tmp_path):
    """Two plugins shipping a core of the same name must not overwrite
    each other's files."""
    with _proc(plugin_dir) as proc:
        core = find_core(proc.cores(), "vice")
        result = install_core(
            proc, core, cores_dir=tmp_path / "cores", downloader=FakeDownloader()
        )
    assert result.directory.name == "coreplug"


def test_an_install_of_an_unknown_core_names_the_ones_that_exist(plugin_dir, tmp_path):
    with _proc(plugin_dir) as proc:
        with pytest.raises(CoreError, match="dosbox"):
            find_core(proc.cores(), "nonesuch")


def test_a_refused_plan_installs_nothing(plugin_dir, tmp_path):
    """The gate fires inside core_plan(), so the download never starts."""
    target_root = tmp_path / "cores"
    downloader = FakeDownloader()
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(CoreError, match="evil.example"):
            install_core(
                proc,
                CoreArtifact(core_id="dosbox", name="DOSBox"),
                cores_dir=target_root,
                downloader=downloader,
            )
    assert downloader.calls == []
    assert not target_root.exists()


def test_a_download_failure_is_reported_not_propagated_raw(plugin_dir, tmp_path):
    class Broken(FakeDownloader):
        def download(self, url, dest, expected_size=None):
            raise OSError("connection reset")

    with _proc(plugin_dir) as proc:
        core = find_core(proc.cores(), "dosbox")
        with pytest.raises(CoreError, match="connection reset"):
            install_core(
                proc, core, cores_dir=tmp_path / "cores", downloader=Broken()
            )


def test_the_cores_directory_is_configuration_not_a_constant(tmp_path, monkeypatch):
    """`/opt/romm-stream/cores` is the deployment target's path, not the
    Hub's: hard-coding it would put a plugin-supplied download outside
    ROMM_HUB_HOME on every other host."""
    from romm_hub.cli import cores_dir

    monkeypatch.delenv("ROMM_HUB_CORES_DIR", raising=False)
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert cores_dir() == tmp_path / "home" / "var" / "cores"

    monkeypatch.setenv("ROMM_HUB_CORES_DIR", str(tmp_path / "elsewhere"))
    assert cores_dir() == tmp_path / "elsewhere"
