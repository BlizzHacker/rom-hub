"""The Archive.org `importer` capability.

Every fixture below was captured from the live
`GET https://archive.org/metadata/<identifier>` endpoint during this task,
so the field names, the string-typed `size`, and the mixed-case
`emulator_ext` are Archive.org's shapes rather than ours. Where a case has
no natural specimen -- two files sharing the payload extension -- the real
capture is copied and extended, and the test says so.

No test here opens a socket: the plugin's only network path is `ctx.http`,
and `FakeHttp` stands in for it.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.importer import ImportRefused, Importer  # noqa: E402

from romm_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from romm_hub.types import SearchResult  # noqa: E402

# --- fixtures captured from the real API -------------------------------

# https://archive.org/metadata/rubik_202308 -- CC Public Domain Mark, 15 KB,
# and NOT in `stream_only`, which is what makes it importable.
RUBIK = {
    "metadata": {
        "identifier": "rubik_202308",
        "mediatype": "software",
        "collection": [
            "softwarelibrary_msdos_games",
            "softwarelibrary_msdos",
            "softwarelibrary",
            "emulation",
        ],
        "title": "Rubik DOS game",
        "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
        "emulator": "dosbox",
        "emulator_ext": "zip",
        "emulator_start": "RUBIK.EXE",
    },
    "files": [
        {"name": "__ia_thumb.jpg", "format": "Item Tile", "size": "9759"},
        {"name": "rubik.zip", "format": "ZIP", "size": "15420"},
        {"name": "rubik_002.png", "format": "Emulator Screenshot", "size": "3511"},
        {"name": "rubik_002_thumb.jpg", "format": "JPEG Thumb", "size": "8223"},
        {
            "name": "rubik_202308_archive.torrent",
            "format": "Archive BitTorrent",
            "size": "2147",
        },
        # Real items carry a sizeless file. A payload selector that assumes
        # `size` is always present crashes on almost every item there is.
        {"name": "rubik_202308_files.xml", "format": "Metadata"},
        {"name": "rubik_202308_meta.sqlite", "format": "Metadata", "size": "20480"},
        {"name": "rubik_202308_meta.xml", "format": "Metadata", "size": "1065"},
    ],
}

# https://archive.org/metadata/msdos_Oregon_Trail_The_1990 -- the same
# emulator and extension as Rubik, and a perfectly good .zip sitting in
# files[]. The only thing that makes it un-importable is `stream_only`.
OREGON_TRAIL = {
    "metadata": {
        "identifier": "msdos_Oregon_Trail_The_1990",
        "collection": [
            "softwarelibrary_msdos_games",
            "stream_only",
            "softwarelibrary_msdos",
            "softwarelibrary",
            "softwarelibrary_kids",
            "emulation",
        ],
        "title": "Oregon Trail, The",
        "emulator": "dosbox",
        "emulator_ext": "zip",
    },
    "files": [
        {"name": "00_coverscreenshot.jpg", "format": "JPEG", "size": "89012"},
        {"name": "Oregon_Trail_The_1990.zip", "format": "ZIP", "size": "359527"},
    ],
}

# https://archive.org/metadata/msdos_floppy_thexder -- the same shape, but
# Archive.org spells the extension "ZIP" here and "zip" on Rubik. Matching
# case-sensitively would lose this item and thousands like it.
THEXDER = {
    "metadata": {
        "identifier": "msdos_floppy_thexder",
        "collection": ["softwarelibrary_msdos_games", "softwarelibrary", "emulation"],
        "title": "msdos_floppy_thexder",
        "emulator": "dosbox",
        "emulator_ext": "ZIP",
    },
    "files": [
        {"name": "THEXDER.ZIP", "format": "ZIP", "size": "10240"},
        {
            "name": "msdos_floppy_thexder_archive.torrent",
            "format": "Archive BitTorrent",
            "size": "1685",
        },
        {"name": "msdos_floppy_thexder_meta.xml", "format": "Metadata", "size": "765"},
    ],
}


class FakeHttp:
    """Stands in for `ctx.http` -- the plugin's only route to the network."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return HttpResponse(
            status_code=self.status_code, text=json.dumps(self.payload)
        )


def make_importer(payload=RUBIK, config=None, status_code=200):
    http = FakeHttp(payload, status_code=status_code)
    return Importer(PluginContext(config=config or {}, http=http)), http


def result_for(payload):
    identifier = payload["metadata"]["identifier"]
    return SearchResult(
        source_id=identifier, title=payload["metadata"].get("title", identifier)
    )


# --- payload selection ---------------------------------------------------


def test_emulator_ext_selects_the_payload_out_of_the_file_list():
    """Eight files, one of them the ROM. `emulator_ext` is which."""
    importer, _ = make_importer()
    plan = importer.plan(result_for(RUBIK))
    assert [f.filename for f in plan.files] == ["rubik.zip"]
    assert plan.files[0].url == "https://archive.org/download/rubik_202308/rubik.zip"
    assert plan.files[0].size_bytes == 15420


def test_the_metadata_endpoint_is_asked_for_the_result_identifier():
    importer, http = make_importer()
    importer.plan(result_for(RUBIK))
    assert http.calls == [("https://archive.org/metadata/rubik_202308", None)]


def test_extension_matching_is_case_insensitive():
    """Archive.org writes "ZIP" on some items and "zip" on others."""
    importer, _ = make_importer(THEXDER)
    plan = importer.plan(result_for(THEXDER))
    assert [f.filename for f in plan.files] == ["THEXDER.ZIP"]


def test_largest_matching_file_wins():
    """Derived from the real Rubik capture by adding a second .zip.

    Multi-payload items exist (multi-disk sets, alternate builds); no
    specimen turned up in the sample swept for this task, so the tie is
    constructed on top of a real item rather than invented wholesale.
    """
    payload = copy.deepcopy(RUBIK)
    payload["files"].append({"name": "rubik_hires.zip", "format": "ZIP", "size": "99999"})
    payload["files"].append({"name": "rubik_tiny.zip", "format": "ZIP", "size": "12"})
    importer, _ = make_importer(payload)
    plan = importer.plan(result_for(payload))
    assert [f.filename for f in plan.files] == ["rubik_hires.zip"]
    assert plan.files[0].size_bytes == 99999


def test_a_sizeless_file_never_wins_over_a_sized_one():
    """`size` is absent on _files.xml for every item Archive.org serves.

    Treating "no size" as anything but "smallest" would let a metadata
    stub beat the actual ROM whenever the two share an extension.
    """
    payload = copy.deepcopy(RUBIK)
    payload["files"].append({"name": "rubik_unsized.zip", "format": "ZIP"})
    importer, _ = make_importer(payload)
    plan = importer.plan(result_for(payload))
    assert [f.filename for f in plan.files] == ["rubik.zip"]


def test_platform_comes_from_the_emulator_mapping():
    importer, _ = make_importer()
    assert importer.plan(result_for(RUBIK)).platform == "dos"


def test_imports_are_grouped_into_a_collection():
    importer, _ = make_importer()
    assert importer.plan(result_for(RUBIK)).collection == "Archive.org"


def test_the_collection_name_is_configurable():
    importer, _ = make_importer(config={"collection": "IA imports"})
    assert importer.plan(result_for(RUBIK)).collection == "IA imports"


# --- the refusals --------------------------------------------------------


def test_a_stream_only_item_is_refused():
    """The whole reason routing reads Archive.org's own signal.

    Oregon Trail has a downloadable-looking .zip in files[]; fetching it
    is exactly the mistake `stream_only` exists to prevent.
    """
    importer, _ = make_importer(OREGON_TRAIL)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(OREGON_TRAIL))
    message = str(exc.value)
    assert "stream" in message.lower()
    assert "msdos_Oregon_Trail_The_1990" in message


def test_a_stream_only_item_is_refused_before_any_file_is_chosen():
    """A refusal that still named a URL would invite someone to fetch it."""
    importer, _ = make_importer(OREGON_TRAIL)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(OREGON_TRAIL))
    assert "Oregon_Trail_The_1990.zip" not in str(exc.value)


def test_collection_given_as_a_bare_string_is_still_read():
    """Archive.org returns a string, not a list, for single-collection items."""
    payload = copy.deepcopy(OREGON_TRAIL)
    payload["metadata"]["collection"] = "stream_only"
    importer, _ = make_importer(payload)
    with pytest.raises(ImportRefused):
        importer.plan(result_for(payload))


def test_an_unmapped_emulator_raises_needs_mapping_and_names_it():
    """Never guess. A ROM filed under the wrong system is worse than a gap."""
    payload = copy.deepcopy(RUBIK)
    payload["metadata"]["emulator"] = "some-new-emulator"
    importer, _ = make_importer(payload)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(payload))
    message = str(exc.value)
    assert "needs mapping" in message.lower()
    assert "some-new-emulator" in message


def test_an_item_with_no_emulator_at_all_is_refused():
    payload = copy.deepcopy(RUBIK)
    del payload["metadata"]["emulator"]
    importer, _ = make_importer(payload)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(payload))
    assert "emulator" in str(exc.value).lower()


def test_no_file_matching_the_extension_names_the_extension_it_wanted():
    payload = copy.deepcopy(RUBIK)
    payload["metadata"]["emulator_ext"] = "d64"
    importer, _ = make_importer(payload)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(payload))
    message = str(exc.value)
    assert "d64" in message
    assert "rubik_202308" in message


def test_an_item_with_no_emulator_ext_is_refused():
    payload = copy.deepcopy(RUBIK)
    del payload["metadata"]["emulator_ext"]
    importer, _ = make_importer(payload)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(payload))
    assert "emulator_ext" in str(exc.value)


def test_an_unknown_identifier_is_refused():
    """Archive.org answers 200 with `{}` for an identifier that is not there."""
    importer, _ = make_importer({})
    with pytest.raises(ImportRefused) as exc:
        importer.plan(SearchResult(source_id="no_such_item", title="nope"))
    assert "no_such_item" in str(exc.value)


def test_a_non_200_from_the_metadata_endpoint_is_refused():
    importer, _ = make_importer(RUBIK, status_code=503)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(result_for(RUBIK))
    assert "503" in str(exc.value)


def test_an_unparseable_body_is_refused_rather_than_raising_json_errors():
    class BadHttp:
        def get(self, url, params=None):
            return HttpResponse(status_code=200, text="<html>rate limited</html>")

    importer = Importer(PluginContext(config={}, http=BadHttp()))
    with pytest.raises(ImportRefused):
        importer.plan(result_for(RUBIK))


# --- URLs the host is going to fetch -------------------------------------


def test_a_filename_needing_escaping_produces_a_usable_url():
    payload = copy.deepcopy(RUBIK)
    payload["files"].append(
        {"name": "rubik demo (v2).zip", "format": "ZIP", "size": "99999"}
    )
    importer, _ = make_importer(payload)
    plan = importer.plan(result_for(payload))
    assert plan.files[0].filename == "rubik demo (v2).zip"
    assert plan.files[0].url == (
        "https://archive.org/download/rubik_202308/rubik%20demo%20%28v2%29.zip"
    )


def test_a_file_in_a_subdirectory_keeps_its_path_in_the_url_only():
    """`FetchFile.filename` must be a bare name -- the host writes it to disk.

    Archive.org does serve files under a subdirectory, and the path
    belongs in the URL, never in the name the host opens for writing.
    """
    payload = copy.deepcopy(RUBIK)
    payload["files"].append({"name": "disks/rubik_b.zip", "format": "ZIP", "size": "99999"})
    importer, _ = make_importer(payload)
    plan = importer.plan(result_for(payload))
    assert plan.files[0].filename == "rubik_b.zip"
    assert plan.files[0].url.endswith("/rubik_202308/disks/rubik_b.zip")


def test_every_planned_url_is_inside_the_manifest_allowlist():
    """The host re-checks this, but a plugin that routinely planned URLs
    its own manifest forbids would be broken, not merely refused."""
    from romm_hub.manifest import load_manifest
    from romm_hub.netpolicy import check_url

    manifest = load_manifest(PLUGIN_ROOT / "manifest.toml")
    importer, _ = make_importer()
    for entry in importer.plan(result_for(RUBIK)).files:
        check_url(entry.url, manifest.network)


# --- the mapping table ---------------------------------------------------


def test_the_mapping_covers_the_emulators_archive_org_actually_uses():
    """Sampled live from `collection:(softwarelibrary) AND emulator:[* TO *]`."""
    from archive_org.platforms import platform_for

    observed = {
        "dosbox": "dos",
        "dosbox-sync": "dos",
        "vice-resid": "c64",
        "vice": "c64",
        "vice-pet": "cpet",
        "apple2e": "appleii",
        "apple2ee": "appleii",
        "apple2woz": "appleii",
        "apple2gs": "apple-iigs",
        "apple3": "appleiii",
        "a800": "atari8bit",
        "a800xl": "atari8bit",
        "sae-a500p": "amiga",
        "cpc6128": "acpc",
        "zx81": "zx81",
        "spectrum": "zxs",
        "megadriv": "genesis",
        "genesis": "genesis",
        "gamegear": "gamegear",
        "sms": "sms",
        "nes": "nes",
        "pce-macplus": "mac",
        "pce-atarist-color": "atari-st",
        "mc10": "trs-80-mc-10",
        "aquarius": "aquarius",
    }
    assert {k: platform_for(k) for k in observed} == observed


def test_the_manifest_declares_the_importer_capability():
    from romm_hub.manifest import load_manifest

    manifest = load_manifest(PLUGIN_ROOT / "manifest.toml")
    assert manifest.capabilities.get("importer") == "archive_org.importer:Importer"
