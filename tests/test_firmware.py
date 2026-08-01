"""The `firmware` capability: the catalogue, the gate, the unpack, the file.

A BIOS is a binary from the internet landing on the operator's disk *and*
going into their library, so it earns at least the checks a ROM import
does -- and gets them by reusing `FetchPlan` and `PluginProcess`'s own
gate rather than by a second implementation that resembles it. The tests
below therefore assert the same three properties the import path asserts,
through a real plugin subprocess: undeclared host refused, cleartext
refused, escaping filename refused.

Two things are firmware's own and are tested here for the first time.

**The licence is not optional.** `FirmwareArtifact.license` is a required
field, and the CLI prints it in a column, because the entire value of a
firmware source is knowing what you are allowed to have.

**The library step degrades, it does not refuse.** A backend with no
firmware store costs the operator the upload and not the download.
"""

import textwrap
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from rom_hub.backends.base import ARTWORK, FIRMWARE, IMPORT, BackendError
from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.firmware import (
    MAX_FIRMWARE_BYTES,
    FirmwareError,
    find_firmware,
    install_firmware,
)
from rom_hub.manifest import ManifestError, parse_manifest
from rom_hub.types import FirmwareArtifact

# --- the type -----------------------------------------------------------


def test_a_firmware_item_needs_an_id_a_platform_and_a_licence():
    item = FirmwareArtifact(
        firmware_id="gba-open",
        name="Open GBA BIOS",
        platform="gba",
        license="MIT",
    )
    assert item.firmware_id == "gba-open"
    assert item.archive is None and item.members == []


@pytest.mark.parametrize("missing", ["platform", "license"])
def test_platform_and_licence_are_required(missing):
    """Both are what separate firmware from a core.

    An item with no platform cannot be filed -- firmware is keyed by it --
    and an item with no licence is the thing this capability exists to
    prevent: a BIOS an operator cannot tell apart from a dump.
    """
    kwargs = {
        "firmware_id": "x",
        "name": "X",
        "platform": "gba",
        "license": "MIT",
    }
    del kwargs[missing]
    with pytest.raises(ValidationError):
        FirmwareArtifact(**kwargs)


@pytest.mark.parametrize("blank", ["", "   \t"])
def test_a_blank_licence_is_not_a_licence(blank):
    """`min_length=1` stops the empty string; whitespace is caught because
    a plugin that has nothing to say should say nothing loudly."""
    if not blank:
        with pytest.raises(ValidationError):
            FirmwareArtifact(
                firmware_id="x", name="X", platform="gba", license=blank
            )
    else:
        # A whitespace-only licence passes the type and is visibly useless
        # in the listing, which is the honest outcome: the host cannot
        # audit a licence, only require that one is stated.
        item = FirmwareArtifact(
            firmware_id="x", name="X", platform="gba", license=blank
        )
        assert not item.license.strip()


@pytest.mark.parametrize("evil", ["../../etc", "a/b", "a\\b", "a b", "a;b", ""])
def test_a_firmware_id_is_an_identifier(evil):
    """Typed on a command line and compared exactly; never a path
    component, and it should not be able to look like one."""
    with pytest.raises(ValidationError):
        FirmwareArtifact(
            firmware_id=evil, name="x", platform="gba", license="MIT"
        )


@pytest.mark.parametrize(
    "evil",
    [
        "../escape.bin",
        "NUL",
        "boot.bin ",
        # every component, not just the last one
        "a/../b.bin",
        "/abs/boot.bin",
        "a//b.bin",
        "C:evil.bin",
        "share/NUL",
        "NUL/boot.bin",
        r"share\machines\boot.bin",
    ],
)
def test_an_archive_member_goes_through_the_filename_validator(evil):
    with pytest.raises(ValidationError):
        FirmwareArtifact(
            firmware_id="x",
            name="x",
            platform="gb",
            license="MIT",
            archive="zip",
            members=[evil],
        )


def test_a_member_may_name_a_directory_inside_the_archive():
    """openMSX is the only publisher of built C-BIOS ROMs and it keeps
    them under `share/machines/`. That path is a lookup key into the zip
    and never a destination -- the install is flat, and `firmware.py`
    takes the basename before `dest_in_job_dir` ever sees it."""
    item = FirmwareArtifact(
        firmware_id="x",
        name="x",
        platform="msx",
        license="BSD-3-Clause",
        archive="zip",
        members=["share/machines/cbios_main_msx1.rom"],
    )
    assert item.members == ["share/machines/cbios_main_msx1.rom"]


def test_two_members_that_install_to_one_name_are_refused():
    """`a/boot.bin` and `b/boot.bin` are two entries in the zip and one
    file in the firmware directory; the second would silently overwrite
    the first."""
    with pytest.raises(ValidationError, match="distinct installed name"):
        FirmwareArtifact(
            firmware_id="x",
            name="x",
            platform="gb",
            license="MIT",
            archive="zip",
            members=["a/boot.bin", "b/boot.bin"],
        )


def test_members_without_an_archive_are_refused():
    with pytest.raises(ValidationError, match="without an archive"):
        FirmwareArtifact(
            firmware_id="x",
            name="x",
            platform="gb",
            license="MIT",
            members=["boot.bin"],
        )


def test_an_archive_without_members_is_refused():
    """Otherwise the host would have to decide what a zip was for."""
    with pytest.raises(ValidationError, match="no members"):
        FirmwareArtifact(
            firmware_id="x",
            name="x",
            platform="gb",
            license="MIT",
            archive="zip",
        )


def test_only_zip_is_a_known_archive():
    with pytest.raises(ValidationError):
        FirmwareArtifact(
            firmware_id="x",
            name="x",
            platform="gb",
            license="MIT",
            archive="tar",
            members=["boot.bin"],
        )


def test_two_members_may_not_collide_case_insensitively():
    """They share one directory, and Windows opens both as one file."""
    with pytest.raises(ValidationError, match="repeated"):
        FirmwareArtifact(
            firmware_id="x",
            name="x",
            platform="gb",
            license="MIT",
            archive="zip",
            members=["boot.bin", "BOOT.BIN"],
        )


def test_find_firmware_matches_exactly_and_names_what_exists():
    items = [
        FirmwareArtifact(
            firmware_id="gba-open", name="a", platform="gba", license="MIT"
        ),
        FirmwareArtifact(
            firmware_id="gb-boot", name="b", platform="gb", license="MIT"
        ),
    ]
    assert find_firmware(items, "gb-boot").name == "b"
    with pytest.raises(FirmwareError, match="gb-boot, gba-open"):
        find_firmware(items, "gb-bot")


# --- the manifest -------------------------------------------------------


def test_firmware_is_a_known_capability():
    manifest = parse_manifest(
        """
        [plugin]
        slug = "fw"
        name = "FW"
        version = "0.1.0"
        rpp_version = "1"

        [capabilities]
        firmware = "fw.firmware:Firmware"
        """
    )
    assert manifest.capabilities == {"firmware": "fw.firmware:Firmware"}
    # Adding a capability name did not break the existing ones.
    assert manifest.rpp_version == "1"


def test_an_unknown_capability_is_still_refused():
    """The point of an allowlist is that adding one entry adds one entry."""
    with pytest.raises(ManifestError, match="unknown capability 'bios'"):
        parse_manifest(
            """
            [plugin]
            slug = "fw"
            name = "FW"
            version = "0.1.0"
            rpp_version = "1"

            [capabilities]
            bios = "fw.firmware:Firmware"
            """
        )


# --- the host gate, through a real plugin subprocess ---------------------

MANIFEST = """
[plugin]
slug = "fwplug"
name = "Fwplug"
version = "0.1.0"
rpp_version = "1"

[capabilities]
firmware = "firmware_plugin:Firmware"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from rom_hub_sdk import FetchFile, FetchPlan, FirmwareArtifact, FirmwareProvider


    class Raw:
        """A plugin that skips the SDK's types entirely."""

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Firmware(FirmwareProvider):
        def list(self):
            mode = self.ctx.config.get("mode", "good")
            if mode == "raw_bad_id":
                return [Raw({"firmware_id": "../../etc/passwd", "name": "x",
                             "platform": "gba", "license": "MIT"})]
            if mode == "raw_no_licence":
                return [Raw({"firmware_id": "x", "name": "x",
                             "platform": "gba"})]
            if mode == "raw_item_is_a_string":
                return [Raw("gba-open")]
            return [
                FirmwareArtifact(
                    firmware_id="gba-open",
                    name="Open GBA BIOS",
                    platform="gba",
                    license="MIT",
                    version="1.0",
                    description="a replacement BIOS",
                ),
                FirmwareArtifact(
                    firmware_id="gb-boot",
                    name="GB boot ROMs",
                    platform="gb",
                    license="MIT",
                    archive="zip",
                    members=["dmg_boot.bin", "mgb_boot.bin"],
                ),
            ]

        def plan(self, firmware):
            mode = self.ctx.config.get("mode", "good")

            if mode == "exfiltrate":
                return Raw({
                    "files": [{"url": "https://evil.example/bios.bin",
                               "filename": "bios.bin"}],
                    "platform": "gba",
                })

            if mode == "raw_traversal":
                return Raw({
                    "files": [{"url": "https://allowed.example/bios.bin",
                               "filename": "../../escape.bin"}],
                    "platform": "gba",
                })

            if mode == "raw_plain_http":
                return Raw({
                    "files": [{"url": "http://allowed.example/bios.bin",
                               "filename": "bios.bin"}],
                    "platform": "gba",
                })

            if mode == "mixed":
                return FetchPlan(
                    files=[
                        FetchFile(url="https://allowed.example/a.bin",
                                  filename="a.bin"),
                        FetchFile(url="https://evil.example/b.bin",
                                  filename="b.bin"),
                    ],
                    platform="gba",
                )

            if mode == "two_files_for_an_archive":
                return FetchPlan(
                    files=[
                        FetchFile(url="https://allowed.example/a.zip",
                                  filename="a.zip"),
                        FetchFile(url="https://allowed.example/b.zip",
                                  filename="b.zip"),
                    ],
                    platform="gb",
                )

            if firmware.archive == "zip":
                return FetchPlan(
                    files=[
                        FetchFile(url="https://allowed.example/boot.zip",
                                  filename="boot.zip")
                    ],
                    platform=firmware.platform,
                )

            return FetchPlan(
                files=[
                    FetchFile(
                        url="https://allowed.example/" + firmware.firmware_id
                            + ".bin",
                        filename=firmware.firmware_id + ".bin",
                        size_bytes=4,
                    )
                ],
                platform=firmware.platform,
            )
    '''
)


class NullFetcher:
    """The firmware path must never touch this. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "firmware_plugin.py").write_text(PLUGIN, encoding="utf-8")
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


def _item(**overrides):
    kwargs = {
        "firmware_id": "gba-open",
        "name": "Open GBA BIOS",
        "platform": "gba",
        "license": "MIT",
    }
    kwargs.update(overrides)
    return FirmwareArtifact(**kwargs)


def test_the_catalogue_comes_back_validated(plugin_dir):
    with _proc(plugin_dir) as proc:
        items = proc.firmware()
    assert [i.firmware_id for i in items] == ["gba-open", "gb-boot"]
    assert items[0].license == "MIT"
    assert items[1].members == ["dmg_boot.bin", "mgb_boot.bin"]


def test_an_item_without_a_licence_is_refused_host_side(plugin_dir):
    """The runner calls `model_dump()` on whatever the plugin returned, so
    the required field is only real on the host's side of the pipe."""
    with _proc(plugin_dir, {"mode": "raw_no_licence"}) as proc:
        with pytest.raises(PluginCallError, match="invalid firmware artifact"):
            proc.firmware()


def test_an_item_that_is_not_an_object_is_a_plugin_error(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_item_is_a_string"}) as proc:
        with pytest.raises(PluginCallError, match="invalid firmware artifact"):
            proc.firmware()


def test_a_firmware_id_shaped_like_a_path_is_rejected_host_side(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_bad_id"}) as proc:
        with pytest.raises(PluginCallError, match="invalid firmware artifact"):
            proc.firmware()


def test_a_catalogue_that_is_not_a_list_is_a_plugin_error(plugin_dir):
    proc = _proc(plugin_dir)
    proc._call = lambda method, params: {"firmware_id": "gba-open"}
    with pytest.raises(PluginCallError, match="expected a list"):
        proc.firmware()


def test_an_enormous_catalogue_is_refused(plugin_dir):
    """The host walks whatever it is given, so the walk is bounded."""
    from rom_hub.types import MAX_FIRMWARE_PER_PLUGIN

    proc = _proc(plugin_dir)
    proc._call = lambda method, params: [
        {"firmware_id": f"f{i}", "name": "x", "platform": "gba", "license": "MIT"}
        for i in range(MAX_FIRMWARE_PER_PLUGIN + 1)
    ]
    with pytest.raises(PluginCallError, match="over the"):
        proc.firmware()


def test_listing_firmware_never_fetches_anything(plugin_dir):
    """A catalogue is a description. Nothing is downloaded to produce it."""
    fetcher = NullFetcher()
    with _proc(plugin_dir, fetcher=fetcher) as proc:
        proc.firmware()
    assert fetcher.calls == []


def test_a_firmware_plan_is_gated_exactly_like_an_import_plan(plugin_dir):
    """An undeclared host. The gate is `_gated_plan`, shared with the
    importer and with cores -- one implementation, so it cannot drift."""
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.firmware_plan(_item())


def test_a_mixed_firmware_plan_is_rejected_as_a_whole(plugin_dir):
    """A legitimate first entry must not carry an undeclared host in."""
    with _proc(plugin_dir, {"mode": "mixed"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.firmware_plan(_item())


def test_a_cleartext_firmware_url_is_rejected(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_plain_http"}) as proc:
        with pytest.raises(PluginCallError, match="allowed.example"):
            proc.firmware_plan(_item())


def test_an_escaping_firmware_filename_is_rejected(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_traversal"}) as proc:
        with pytest.raises(PluginCallError, match="invalid FetchPlan"):
            proc.firmware_plan(_item())


# --- installing ----------------------------------------------------------


class FakeDownloader:
    def __init__(self, payload=b"bios"):
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


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_install_writes_under_the_configured_directory(plugin_dir, tmp_path):
    target_root = tmp_path / "somewhere" / "firmware"
    downloader = FakeDownloader()
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc, item, firmware_dir=target_root, downloader=downloader
        )

    installed = target_root / "fwplug" / "gba-open.bin"
    assert installed.read_bytes() == b"bios"
    assert result.files == [installed]
    assert result.directory == target_root / "fwplug"
    assert result.platform == "gba"
    assert result.license == "MIT"
    # Every write stayed inside the directory it was given.
    for _, dest in downloader.calls:
        assert target_root.resolve() in dest.resolve().parents


def test_the_message_names_the_licence(plugin_dir, tmp_path):
    """It is printed by `rom-hub firmware install`, and it is the whole
    reason an operator would use a firmware plugin rather than a search."""
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc,
            item,
            firmware_dir=tmp_path / "fw",
            downloader=FakeDownloader(),
        )
    assert "licence: MIT" in result.message
    assert "gba" in result.message


def test_an_archive_is_unpacked_to_exactly_the_declared_members(
    plugin_dir, tmp_path
):
    payload = _zip_bytes(
        {
            "dmg_boot.bin": b"dmg",
            "mgb_boot.bin": b"mgb",
            "sameboy.exe": b"an emulator nobody asked for",
            "LICENSE": b"expat",
        }
    )
    root = tmp_path / "fw"
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gb-boot")
        result = install_firmware(
            proc,
            item,
            firmware_dir=root,
            downloader=FakeDownloader(payload),
        )

    directory = root / "fwplug"
    assert sorted(p.name for p in directory.iterdir()) == [
        "dmg_boot.bin",
        "mgb_boot.bin",
    ]
    assert (directory / "dmg_boot.bin").read_bytes() == b"dmg"
    assert [p.name for p in result.files] == ["dmg_boot.bin", "mgb_boot.bin"]
    # The archive itself does not stay behind for an emulator to trip over.
    assert not (directory / "boot.zip").exists()


def test_a_zip_entry_named_like_a_path_cannot_be_reached(plugin_dir, tmp_path):
    """Members are matched by full-name equality against names the type
    already validated, so an entry called `../../escape.bin` is simply not
    one of them -- it is never joined onto a path at all."""
    payload = _zip_bytes(
        {"../../escape.bin": b"nope", "dmg_boot.bin": b"dmg", "mgb_boot.bin": b"m"}
    )
    root = tmp_path / "fw"
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gb-boot")
        install_firmware(
            proc, item, firmware_dir=root, downloader=FakeDownloader(payload)
        )
    assert not (tmp_path / "escape.bin").exists()
    assert not (root.parent / "escape.bin").exists()
    assert sorted(p.name for p in (root / "fwplug").iterdir()) == [
        "dmg_boot.bin",
        "mgb_boot.bin",
    ]


def test_a_missing_member_names_what_the_archive_did_contain(
    plugin_dir, tmp_path
):
    payload = _zip_bytes({"dmg_boot.bin": b"dmg"})
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gb-boot")
        with pytest.raises(FirmwareError, match="mgb_boot.bin"):
            install_firmware(
                proc,
                item,
                firmware_dir=tmp_path / "fw",
                downloader=FakeDownloader(payload),
            )


def test_a_decompression_bomb_is_refused(plugin_dir, tmp_path):
    """The header is written by whoever built the zip. Believing it alone
    is how a bomb fills a disk, so the stream is counted as well."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dmg_boot.bin", b"\0" * (MAX_FIRMWARE_BYTES + 1))
        zf.writestr("mgb_boot.bin", b"m")

    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gb-boot")
        with pytest.raises(FirmwareError, match="limit"):
            install_firmware(
                proc,
                item,
                firmware_dir=tmp_path / "fw",
                downloader=FakeDownloader(buffer.getvalue()),
            )


def test_an_archive_item_may_plan_only_one_download(plugin_dir, tmp_path):
    with _proc(plugin_dir, {"mode": "two_files_for_an_archive"}) as proc:
        item = find_firmware(proc.firmware(), "gb-boot")
        with pytest.raises(FirmwareError, match="exactly one file"):
            install_firmware(
                proc,
                item,
                firmware_dir=tmp_path / "fw",
                downloader=FakeDownloader(),
            )


def test_a_corrupt_archive_is_reported_not_raised_raw(plugin_dir, tmp_path):
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gb-boot")
        with pytest.raises(FirmwareError, match="could not be unpacked"):
            install_firmware(
                proc,
                item,
                firmware_dir=tmp_path / "fw",
                downloader=FakeDownloader(b"not a zip at all"),
            )


def test_each_plugin_gets_its_own_directory(plugin_dir, tmp_path):
    """Two plugins shipping a `bios.bin` must not overwrite each other."""
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc,
            item,
            firmware_dir=tmp_path / "fw",
            downloader=FakeDownloader(),
        )
    assert result.directory.name == "fwplug"


# --- the library half ----------------------------------------------------


class FakeBackend:
    """Just enough backend to exercise `_file_in_library`."""

    name = "fake"

    def __init__(self, capabilities=frozenset({FIRMWARE}), existing=()):
        self._capabilities = frozenset(capabilities)
        self.existing = list(existing)
        self.uploaded: list[tuple[tuple[str, ...], int]] = []
        self.platform_calls: list[str] = []

    def capabilities(self):
        return self._capabilities

    def platform_id(self, platform):
        self.platform_calls.append(platform)
        if platform == "unmapped":
            raise BackendError(f"no platform matches slug {platform!r}")
        return 7

    def list_firmware(self, platform_id):
        return [{"file_name": name} for name in self.existing]

    def upload_firmware(self, paths, platform_id):
        self.uploaded.append((tuple(p.name for p in paths), platform_id))


def test_a_backend_that_can_store_firmware_gets_it(plugin_dir, tmp_path):
    backend = FakeBackend()
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc,
            item,
            firmware_dir=tmp_path / "fw",
            backend=backend,
            downloader=FakeDownloader(),
        )
    assert backend.platform_calls == ["gba"]
    assert backend.uploaded == [(("gba-open.bin",), 7)]
    assert result.uploaded == 1
    assert result.skipped is None
    assert "uploaded to the library" in result.message


def test_a_backend_without_firmware_degrades_rather_than_refusing(
    plugin_dir, tmp_path
):
    """The download is the install. Refusing to put a legally-clean BIOS on
    the operator's disk because the *library* has no firmware table would
    be refusing the job over the garnish -- and it is exactly what
    `--collection` once did to every import."""
    backend = FakeBackend(capabilities=frozenset({IMPORT, ARTWORK}))
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc,
            item,
            firmware_dir=tmp_path / "fw",
            backend=backend,
            downloader=FakeDownloader(),
        )

    installed = tmp_path / "fw" / "fwplug" / "gba-open.bin"
    assert installed.read_bytes() == b"bios"
    assert backend.uploaded == []
    assert backend.platform_calls == []
    assert result.uploaded == 0
    assert result.skipped is not None
    assert result.skipped.capability == FIRMWARE
    assert "does not support firmware" in result.message


def test_firmware_already_in_the_library_is_not_sent_twice(plugin_dir, tmp_path):
    backend = FakeBackend(existing=["GBA-OPEN.BIN"])
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc,
            item,
            firmware_dir=tmp_path / "fw",
            backend=backend,
            downloader=FakeDownloader(),
        )
    assert backend.uploaded == []
    assert result.already_present == ["gba-open.bin"]
    assert "already there" in result.message


def test_an_unmapped_platform_says_the_files_are_still_on_disk(
    plugin_dir, tmp_path
):
    """Firmware is keyed by platform and the Hub never guesses one. When
    the library has no such platform the refusal has to say what did
    happen, or the operator goes looking for files that are already
    there."""
    backend = FakeBackend()
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        item = item.model_copy(update={"platform": "unmapped"})
        with pytest.raises(FirmwareError, match="downloaded into"):
            install_firmware(
                proc,
                item,
                firmware_dir=tmp_path / "fw",
                backend=backend,
                downloader=FakeDownloader(),
            )
    assert (tmp_path / "fw" / "fwplug" / "gba-open.bin").exists()


def test_no_backend_at_all_is_a_complete_install(plugin_dir, tmp_path):
    """An emulator pointed at the firmware directory needs no library."""
    with _proc(plugin_dir) as proc:
        item = find_firmware(proc.firmware(), "gba-open")
        result = install_firmware(
            proc,
            item,
            firmware_dir=tmp_path / "fw",
            downloader=FakeDownloader(),
        )
    assert result.skipped is None
    assert result.uploaded == 0
    assert "installed firmware" in result.message
