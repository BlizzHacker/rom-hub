"""The Archive.org `metadata` capability.

The fixtures are the same live captures the importer tests use, so the
field names, the string-typed `size` and the `format` spellings are
Archive.org's shapes rather than ours.

No test here opens a socket: the plugin's only network path is `ctx.http`,
and `FakeHttp` stands in for it. The artwork URL this plugin returns is
never fetched by the plugin at all -- the host does that, after checking
it against the manifest allowlist (see tests/test_broker_enrich.py).
"""

import copy
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.metadata import EnrichRefused, Metadata  # noqa: E402

from rom_hub.types import RomRef  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

# https://archive.org/metadata/rubik_202308, as captured for the importer
# tests. Note what it does NOT have: a `00_coverscreenshot.jpg`. Most
# softwarelibrary items do not, which is why the cover rule falls back to
# `format` rather than requiring that filename.
RUBIK = {
    "metadata": {
        "identifier": "rubik_202308",
        "mediatype": "software",
        "collection": ["softwarelibrary_msdos_games", "emulation"],
        "title": "Rubik DOS game",
        "emulator": "dosbox",
        "emulator_ext": "zip",
    },
    "files": [
        {"name": "__ia_thumb.jpg", "format": "Item Tile", "size": "9759"},
        {"name": "rubik.zip", "format": "ZIP", "size": "15420"},
        {"name": "rubik_002.png", "format": "Emulator Screenshot", "size": "3511"},
        {"name": "rubik_002_thumb.jpg", "format": "JPEG Thumb", "size": "8223"},
        {"name": "rubik_202308_files.xml", "format": "Metadata"},
    ],
}


class FakeHttp:
    """Stands in for ctx.http. Records every URL the plugin asked for."""

    def __init__(self, payload=None, status_code=200, text=None):
        self.payload = RUBIK if payload is None else payload
        self.status_code = status_code
        self.text = text
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        body = self.text if self.text is not None else json.dumps(self.payload)
        return HttpResponse(status_code=self.status_code, text=body)


def _provider(http=None, config=None):
    http = http or FakeHttp()
    return Metadata(PluginContext(config=config or {}, http=http)), http


def _ref(**kwargs):
    base = {
        "rom_id": 1,
        "name": "rubik.zip",
        "filename": "rubik.zip",
        "platform": "dos",
        "extra": {"source_id": "rubik_202308"},
    }
    base.update(kwargs)
    return RomRef(**base)


def test_the_title_becomes_the_name():
    provider, http = _provider()
    patch = provider.enrich(_ref())
    assert patch.name == "Rubik DOS game"
    assert http.calls == ["https://archive.org/metadata/rubik_202308"]


def test_the_cover_is_chosen_by_format_and_handed_over_as_a_url():
    """The plugin names a URL. It does not fetch it -- the host does."""
    provider, http = _provider()
    patch = provider.enrich(_ref())
    assert patch.artwork_url == (
        "https://archive.org/download/rubik_202308/rubik_002.png"
    )
    # And the plugin made exactly one request, to the metadata endpoint.
    assert len(http.calls) == 1


def test_the_cover_filename_is_ours_not_archive_orgs():
    """The host writes this name to disk, and Archive.org filenames are
    user-supplied. Only the extension is taken from the item."""
    item = copy.deepcopy(RUBIK)
    item["files"] = [
        {"name": "../../#weird name%.png", "format": "Emulator Screenshot", "size": "9"}
    ]
    provider, _ = _provider(FakeHttp(item))
    patch = provider.enrich(_ref())
    assert patch.artwork_filename == "cover.png"
    # The escaping name still went into the URL, where it is harmless and
    # correctly percent-encoded, and nowhere near the filesystem.
    assert "%23weird%20name%25.png" in patch.artwork_url


def test_real_box_art_outranks_a_bigger_screenshot():
    item = copy.deepcopy(RUBIK)
    item["files"].append(
        {"name": "00_coverscreenshot.jpg", "format": "JPEG", "size": "10"}
    )
    provider, _ = _provider(FakeHttp(item))
    patch = provider.enrich(_ref())
    assert patch.artwork_url.endswith("00_coverscreenshot.jpg")
    assert patch.artwork_filename == "cover.jpg"


def test_an_item_tile_is_used_when_there_is_no_screenshot():
    item = copy.deepcopy(RUBIK)
    item["files"] = [
        {"name": "__ia_thumb.jpg", "format": "Item Tile", "size": "9759"},
        {"name": "rubik.zip", "format": "ZIP", "size": "15420"},
    ]
    provider, _ = _provider(FakeHttp(item))
    assert provider.enrich(_ref()).artwork_url.endswith("__ia_thumb.jpg")


def test_an_item_with_no_image_still_yields_the_name():
    item = copy.deepcopy(RUBIK)
    item["files"] = [{"name": "rubik.zip", "format": "ZIP", "size": "15420"}]
    provider, _ = _provider(FakeHttp(item))
    patch = provider.enrich(_ref())
    assert patch.name == "Rubik DOS game"
    assert patch.artwork_url is None


def test_an_item_with_neither_is_an_empty_patch_not_an_error():
    """The host reads an empty patch as "leave RomM alone", which is right."""
    provider, _ = _provider(FakeHttp({"metadata": {"identifier": "x"}, "files": []}))
    assert provider.enrich(_ref()).is_empty()


def test_a_rom_with_no_identifier_is_refused_not_guessed():
    """Searching for the rom's name and taking the top hit would write
    another game's title and cover into the user's library."""
    provider, http = _provider()
    with pytest.raises(EnrichRefused, match="--source-id"):
        provider.enrich(_ref(extra={}))
    assert http.calls == [], "a refusal must not cost a request either"


def test_an_unknown_identifier_is_refused():
    provider, _ = _provider(FakeHttp({}))
    with pytest.raises(EnrichRefused, match="no item"):
        provider.enrich(_ref())


def test_a_non_json_response_is_refused():
    """Rate limiting and maintenance pages both arrive as 200 + HTML."""
    provider, _ = _provider(FakeHttp(text="<html>slow down</html>"))
    with pytest.raises(EnrichRefused, match="not JSON"):
        provider.enrich(_ref())


def test_an_http_error_is_refused():
    provider, _ = _provider(FakeHttp(status_code=503))
    with pytest.raises(EnrichRefused, match="503"):
        provider.enrich(_ref())


def test_every_url_it_produces_is_inside_its_declared_allowlist():
    """The manifest declares archive.org; a patch that named anything else
    would be refused by the host, so it must not produce one."""
    from rom_hub.netpolicy import check_url

    provider, _ = _provider()
    patch = provider.enrich(_ref())
    check_url(patch.artwork_url, ["archive.org", "*.archive.org"])
