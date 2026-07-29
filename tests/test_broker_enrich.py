"""The MetadataPatch gate, exercised through a real plugin subprocess.

`enrich()` is the third way a plugin can make the host reach a host: it
hands back an artwork URL and asks the host to go and fetch it. Same class
of hole as a FetchPlan URL, so it gets the same `check_url` against the
same manifest allowlist -- otherwise `network = [...]` means nothing for
metadata.

As in test_broker_plan.py, several cases return a duck-typed object whose
`model_dump()` emits whatever it likes: a hostile plugin is under no
obligation to use the SDK's types, so everything the host trusts is
re-established on the host side of the pipe.
"""

import textwrap
from pathlib import Path

import pytest

from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.manifest import parse_manifest
from rom_hub.types import RomRef

MANIFEST = """
[plugin]
slug = "meta"
name = "Meta"
version = "0.1.0"
rpp_version = "1"

[capabilities]
metadata = "meta_plugin:Metadata"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    '''
    from rom_hub_sdk import MetadataPatch, MetadataProvider


    class Raw:
        """A plugin that skips the SDK's types entirely."""

        def __init__(self, payload):
            self._payload = payload

        def model_dump(self):
            return self._payload


    class Metadata(MetadataProvider):
        def enrich(self, rom):
            mode = self.ctx.config.get("mode", "good")

            if mode == "exfiltrate":
                return Raw({
                    "name": "Doom",
                    "artwork_url": "https://evil.example/cover.png",
                })

            if mode == "raw_traversal":
                return Raw({
                    "artwork_url": "https://allowed.example/cover.png",
                    "artwork_filename": "../../escape.png",
                })

            if mode == "raw_plain_http":
                return Raw({
                    "artwork_url": "http://allowed.example/cover.png",
                })

            if mode == "raw_userinfo":
                return Raw({
                    "artwork_url":
                        "https://allowed.example@evil.example/cover.png",
                })

            if mode == "raw_unknown_field":
                return Raw({"provider_ids": {"fs_path": "/etc/passwd"}})

            if mode == "raw_not_a_mapping":
                return Raw(["not", "a", "patch"])

            if mode == "empty":
                return MetadataPatch()

            if mode == "inline_artwork":
                return MetadataPatch(
                    name="Doom", artwork_base64=b"\\x89PNG-not-really"
                )

            if mode == "echo":
                # Proves the host actually told the plugin which rom.
                return MetadataPatch(name=f"{rom.name} [{rom.rom_id}]")

            return MetadataPatch(
                name="Doom",
                provider_ids={"igdb_id": 7},
                artwork_url="https://allowed.example/cover.png",
            )
    '''
)


class NullFetcher:
    """The enrich path must never touch this. Records anything that does."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, params):
        self.calls.append(url)
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "meta_plugin.py").write_text(PLUGIN, encoding="utf-8")
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


REF = RomRef(rom_id=42, name="Doom", filename="doom.zip", platform="dos")


def test_enrich_returns_a_validated_patch(plugin_dir):
    with _proc(plugin_dir) as proc:
        patch = proc.enrich(REF)
    assert patch.name == "Doom"
    assert patch.provider_ids == {"igdb_id": 7}
    assert patch.artwork_url == "https://allowed.example/cover.png"


def test_the_plugin_is_told_which_rom(plugin_dir):
    with _proc(plugin_dir, {"mode": "echo"}) as proc:
        assert proc.enrich(REF).name == "Doom [42]"


def test_an_artwork_url_on_an_undeclared_host_is_rejected(plugin_dir):
    """The hole this gate exists for: artwork is a host-performed fetch."""
    with _proc(plugin_dir, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.enrich(REF)


def test_a_cleartext_artwork_url_is_rejected_even_for_an_allowed_host(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_plain_http"}) as proc:
        with pytest.raises(PluginCallError, match="allowed.example"):
            proc.enrich(REF)


def test_userinfo_cannot_disguise_the_real_artwork_host(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_userinfo"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.enrich(REF)


def test_a_traversal_artwork_filename_is_rejected_host_side(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_traversal"}) as proc:
        with pytest.raises(PluginCallError, match="invalid MetadataPatch"):
            proc.enrich(REF)


def test_an_undeclared_form_field_is_rejected_host_side(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_unknown_field"}) as proc:
        with pytest.raises(PluginCallError, match="invalid MetadataPatch"):
            proc.enrich(REF)


def test_a_non_mapping_patch_is_a_plugin_error_not_a_crash(plugin_dir):
    with _proc(plugin_dir, {"mode": "raw_not_a_mapping"}) as proc:
        with pytest.raises(
            PluginCallError,
            match="invalid MetadataPatch: expected an object, got list",
        ):
            proc.enrich(REF)


def test_an_empty_patch_is_legal(plugin_dir):
    """"I found nothing" is an answer, not an error."""
    with _proc(plugin_dir, {"mode": "empty"}) as proc:
        assert proc.enrich(REF).is_empty()


def test_inline_artwork_crosses_the_pipe_intact(plugin_dir):
    with _proc(plugin_dir, {"mode": "inline_artwork"}) as proc:
        patch = proc.enrich(REF)
    assert patch.artwork_data() == b"\x89PNG-not-really"


def test_enriching_never_fetches_anything_itself(plugin_dir):
    """enrich() describes work; the host performs it afterwards."""
    fetcher = NullFetcher()
    with _proc(plugin_dir, fetcher=fetcher) as proc:
        proc.enrich(REF)
    assert fetcher.calls == []
