"""The `archive-org-torrent` plugin: one endpoint, three outcomes.

Every fixture here was captured from the live service on 2026-08-01,
including the refusals -- `metadata_dark.json` is what Archive.org
actually answers for an item it has taken down, and
`dark_torrent_status.txt` records that the same item's `/download/` path
gives **403**.

**No test here opens a socket.** The plugin's only network path is
`ctx.http`, and `FakeHttp` stands in for it -- which is not a
convenience: a plugin subprocess is seccomp-confined and could not open
one anyway.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org-torrent"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "archive_org_torrent"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org_torrent.torrent import (  # noqa: E402
    Torrent,
    TorrentRefused,
)

from rom_hub.netpolicy import check_url  # noqa: E402
from rom_hub.torrents import parse_torrent  # noqa: E402
from rom_hub.types import SearchResult  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

ALLOWLIST = ["archive.org", "*.archive.org"]

RUBIK_BTIH = "6e56c747303e7bf35bf86b1956fb7ea06c99b805"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeHttp:
    """One canned reply, and a record of what was asked for."""

    def __init__(self, body: str, status_code: int = 200):
        self.body = body
        self.status_code = status_code
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        return HttpResponse(status_code=self.status_code, text=self.body)


def provider(fixture_name: str, config: dict | None = None, **kwargs) -> Torrent:
    body = fixture(fixture_name) if fixture_name else kwargs.pop("body", "")
    ctx = PluginContext(config=config or {}, http=FakeHttp(body, **kwargs))
    return Torrent(ctx)


def resolve(fixture_name, identifier, config=None, **kwargs):
    return provider(fixture_name, config, **kwargs).resolve(
        SearchResult(source_id=identifier, title=identifier)
    )


# ------------------------------------------------------- the good outcome


def test_an_item_with_a_torrent_resolves_to_its_download_url():
    source = resolve("metadata_rubik.json", "rubik_202308")
    assert source.kind == "torrent_url"
    assert source.source == (
        "https://archive.org/download/rubik_202308/rubik_202308_archive.torrent"
    )
    # And it is inside the manifest allowlist the host will enforce.
    check_url(source.source, ALLOWLIST)


def test_only_one_request_is_made_and_it_is_the_metadata_endpoint():
    """The plugin never fetches the torrent, and could not if it tried.

    `ctx.http` caps a response at 4 MiB of *text*; a `.torrent` is bytes.
    The host fetches it, which is what makes the allowlist gate and the
    redirect re-check apply to it.
    """
    http = FakeHttp(fixture("metadata_rubik.json"))
    Torrent(PluginContext(config={}, http=http)).resolve(
        SearchResult(source_id="rubik_202308", title="x")
    )
    assert http.calls == ["https://archive.org/metadata/rubik_202308"]


def test_the_published_info_hash_travels_as_a_claim_for_the_host_to_check():
    """Archive.org publishes `btih` beside the torrent in /metadata/.

    Sending it is only worth doing because the host computes its own from
    the bytes that actually arrive and refuses when the two disagree.
    """
    source = resolve("metadata_rubik.json", "rubik_202308")
    assert source.info_hash == RUBIK_BTIH
    # The claim is true, checked against the real torrent's own bytes.
    real = parse_torrent((FIXTURES / "rubik_202308.torrent").read_bytes())
    assert real.info_hash == source.info_hash


def test_the_payload_is_named_and_the_archives_bookkeeping_is_not():
    """Six files in the item, one of which is the game.

    `emulator_ext` is Archive.org's own statement of which extension holds
    it, and it is the same signal the `archive-org` importer keys off.
    """
    source = resolve("metadata_rubik.json", "rubik_202308")
    assert source.files == ["rubik.zip"]
    # Every name it offered is really in the torrent the host will read.
    real = parse_torrent((FIXTURES / "rubik_202308.torrent").read_bytes())
    inside = {e.path for e in real.entries}
    assert set(source.files) <= inside
    # And the five it did not name are the thumbnail, screenshot, sqlite
    # and XML that share the item.
    assert len(inside) == 6


def test_payload_only_false_names_nothing_which_the_host_reads_as_everything():
    """The right answer for a handoff: the client fetches the lot anyway."""
    source = resolve("metadata_rubik.json", "rubik_202308", {"payload_only": False})
    assert source.files == []


def test_context_travels_for_an_operator_to_read():
    source = resolve("metadata_rubik.json", "rubik_202308")
    assert source.extra["identifier"] == "rubik_202308"
    assert source.extra["emulator"] == "dosbox"
    assert source.extra["stream_only"] == "false"
    assert source.name


# ------------------------------------- the flattening, and what it means


def test_the_selector_is_the_basename_because_the_torrent_flattens():
    """The bug this test exists for was real and silent.

    /metadata/ reports this item's ROM at `NES/PAC-MAN Championship
    Edition.nes`; `ia_make_torrent` writes it into the torrent as a
    single-component name. Selecting by the metadata path would have
    named entries the torrent does not contain, and the host would have
    refused every one of them.
    """
    source = resolve(
        "metadata_pacman.json", "pac-man-championship-edition-1"
    )
    assert "PAC-MAN Championship Edition.nes" in source.files
    assert not any("/" in name for name in source.files)

    real = parse_torrent((FIXTURES / "pacman_nested.torrent").read_bytes())
    inside = {e.path for e in real.entries}
    missing = sorted(set(source.files) - inside)
    assert not missing, f"named files that are not in the torrent: {missing}"


def test_every_name_offered_survives_the_wire_types_own_validator():
    """`TorrentSource` re-runs `bare_filename` on each selector.

    Constructing the model at all is the assertion: a name the host would
    refuse never leaves the plugin, because the type refuses it first.
    """
    for name, ident in (
        ("metadata_rubik.json", "rubik_202308"),
        ("metadata_pacman.json", "pac-man-championship-edition-1"),
    ):
        source = resolve(name, ident)
        assert source.files == [f for f in source.files if "/" not in f]


# --------------------------------------------------------- the refusals


def test_an_item_that_publishes_no_torrent_is_refused_by_name():
    """`msdos_Oregon_Trail_The_1990` exists and has no torrent.

    Live: its /metadata/ lists eleven files and not one of them has
    format `Archive BitTorrent`. It is `stream_only` and
    `access-restricted-item`, and the refusal says so -- "no torrent" and
    "no torrent because the Archive will not distribute this" are
    different facts to an operator.
    """
    with pytest.raises(TorrentRefused) as excinfo:
        resolve("metadata_oregon_no_torrent.json", "msdos_Oregon_Trail_The_1990")
    message = str(excinfo.value)
    assert "publishes no 'Archive BitTorrent' file" in message
    assert "stream_only" in message


def test_a_darkened_item_is_refused_before_the_request_that_would_403():
    """`nointro.gb` is the live 403 case, and it is caught one step earlier.

    Its /metadata/ answers 200 with a stub carrying `is_dark: true`, no
    `metadata` and no `files`. Its `/download/.../..._archive.torrent`
    answers **403** -- recorded in `dark_torrent_status.txt`. The plugin
    refuses on the stub, so the host never makes the request that would
    fail.
    """
    assert fixture("dark_torrent_status.txt").strip() == "403"
    stub = json.loads(fixture("metadata_dark.json"))
    assert stub["is_dark"] is True
    assert not stub.get("files")

    with pytest.raises(TorrentRefused) as excinfo:
        resolve("metadata_dark.json", "nointro.gb")
    message = str(excinfo.value)
    assert "darkened" in message
    assert "403" in message


def test_a_403_on_the_metadata_endpoint_itself_is_still_a_clean_refusal():
    """Belt and braces: the status is reported rather than crashed on."""
    with pytest.raises(TorrentRefused, match="HTTP 403"):
        resolve("metadata_rubik.json", "rubik_202308", status_code=403)


@pytest.mark.parametrize(
    "body, fragment",
    [
        ("<html>rate limited</html>", "was not JSON"),
        ("[]", "was not an object"),
        ("{}", "returned nothing"),
    ],
)
def test_a_reply_that_is_not_an_item_is_refused(body, fragment):
    with pytest.raises(TorrentRefused, match=fragment):
        resolve(None, "x", body=body)


def test_an_empty_identifier_is_refused_without_a_request():
    http = FakeHttp("{}")
    with pytest.raises(TorrentRefused, match="no Archive.org identifier"):
        Torrent(PluginContext(config={}, http=http)).resolve(
            SearchResult(source_id="   ", title="x")
        )
    assert http.calls == []


def test_a_garbled_btih_costs_the_cross_check_and_not_the_resolve():
    """A malformed claim is dropped rather than passed on.

    The plugin cannot establish the info-hash -- it has not seen the
    bytes. Sending a bad one would turn the host's cross-check into a
    refusal of a perfectly good torrent.
    """
    body = json.loads(fixture("metadata_rubik.json"))
    for entry in body["files"]:
        if entry.get("format") == "Archive BitTorrent":
            entry["btih"] = "not-a-hash"
    source = resolve(None, "rubik_202308", body=json.dumps(body))
    assert source.info_hash is None
    assert source.source.endswith("_archive.torrent")


def test_a_torrent_entry_with_no_name_is_not_mistaken_for_one():
    body = json.loads(fixture("metadata_rubik.json"))
    for entry in body["files"]:
        if entry.get("format") == "Archive BitTorrent":
            entry["name"] = ""
    with pytest.raises(TorrentRefused, match="publishes no"):
        resolve(None, "rubik_202308", body=json.dumps(body))
