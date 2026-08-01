"""The host half of the `torrent` capability.

**No test here opens a socket.** `rubik_202308.torrent` and
`pacman_nested.torrent` are real Archive.org torrents captured on
2026-08-01 and checked in; every download goes through a fake
`HttpDownloader` that serves bytes out of the fixture.

What is being tested is the boundary, not the arithmetic: every URL a
plugin or a torrent hands over is gated against the plugin's own
manifest allowlist, and the interesting cases are the ones where it
should say no.
"""

import hashlib
from pathlib import Path

import pytest

from rom_hub.netpolicy import PolicyViolation
from rom_hub.torrents import (
    MAGNET_PARAMS,
    TRACKER_SCHEMES,
    FetchedFile,
    Torrent,
    TorrentEntry,
    TorrentError,
    TorrentOutcome,
    check_magnet,
    check_trackers,
    check_web_seeds,
    fetch_entry,
    handoff_path,
    magnet_for,
    parse_torrent,
    web_seed_url,
    write_handoff,
)
from rom_hub.types import TorrentSource

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "archive_org_torrent"

ALLOWLIST = ["archive.org", "*.archive.org"]

RUBIK_BTIH = "6e56c747303e7bf35bf86b1956fb7ea06c99b805"


def rubik() -> Torrent:
    return parse_torrent((FIXTURES / "rubik_202308.torrent").read_bytes())


def pacman() -> Torrent:
    return parse_torrent((FIXTURES / "pacman_nested.torrent").read_bytes())


# --------------------------------------------------------- reading one


def test_a_real_torrent_reads_into_a_manifest():
    t = rubik()
    assert t.info_hash == RUBIK_BTIH
    assert t.name == "rubik_202308"
    assert t.piece_length == 524288
    assert t.piece_count == 1
    assert t.total_bytes == 58458
    assert [e.path for e in t.entries] == [
        "__ia_thumb.jpg",
        "rubik.zip",
        "rubik_002.png",
        "rubik_002_thumb.jpg",
        "rubik_202308_meta.sqlite",
        "rubik_202308_meta.xml",
    ]


def test_archive_org_puts_a_digest_on_every_file_and_that_is_the_point():
    """Per-file `sha1` is what makes a single-file verified fetch possible.

    It is not part of the BitTorrent spec -- pieces are -- but it lives
    in the `info` dictionary, so it is covered by the info-hash. Without
    it, verifying one 15 KB ROM would mean downloading the five other
    files that share its 512 KB piece.
    """
    t = rubik()
    rom = t.entry("rubik.zip")
    assert rom.length == 15420
    assert rom.sha1 == "4f7396a71145a83f477e2dae84cf0235b7fee444"
    assert rom.md5 == "065e1fbe7899ffa2df962a6f9a7aba06"
    assert rom.verified_by == "sha1"
    # One piece for the whole item: the reason the fallback is not pieces.
    assert t.piece_count == 1
    assert t.piece_length > t.total_bytes


def ben(value) -> bytes:
    """Bencode, for building test input only.

    `rom_hub.bencode` deliberately ships no encoder -- the info-hash comes
    from the raw byte span precisely so nothing is ever re-encoded -- so
    the one needed to *write* a fixture lives here, in the tests, where it
    cannot become a second way to compute an info-hash.
    """
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(ben(v) for v in value) + b"e"
    if isinstance(value, dict):
        return (
            b"d"
            + b"".join(
                ben(k) + ben(v) for k, v in sorted(value.items())
            )
            + b"e"
        )
    raise TypeError(type(value))


#: A torrent with a genuinely nested entry. Built rather than captured,
#: because -- see the test below -- Archive.org does not publish one, and
#: the host's rule has to hold for the sources that do.
NESTED_TORRENT = ben(
    {
        "announce": "http://bt1.archive.org:6969/announce",
        "info": {
            "name": "item",
            "piece length": 16384,
            "pieces": b"",
            "files": [
                {"length": 4, "path": ["NES", "rom.nes"]},
                {"length": 4, "path": ["PSP", "rom.nes"]},
                {"length": 4, "path": ["flat.zip"]},
            ],
        },
    }
)


def test_archive_orgs_own_torrents_are_flat_even_when_the_item_is_not():
    """A real finding, recorded because it is the opposite of the assumption.

    `pac-man-championship-edition-1` keeps its files in `NES/`, `PSP/`,
    `Android/` and `iOS/` subdirectories -- the /metadata/ listing says so
    -- and `ia_make_torrent` writes every one of them into the torrent as
    a **single-component** path. So on this corpus the host's nested-entry
    rule is defensive rather than routinely exercised, and a plugin that
    selected files by their /metadata/ path would name entries the torrent
    does not contain.
    """
    t = pacman()
    assert t.piece_count > 1000, "a multi-piece torrent, unlike the rubik one"
    assert all("/" not in e.path for e in t.entries)
    assert all(e.selectable for e in t.entries)
    assert "PAC-MAN Championship Edition.nes" in {e.path for e in t.entries}


def test_a_nested_entry_is_listed_and_refused_rather_than_flattened():
    """Flattening `NES/rom.nes` and `PSP/rom.nes` makes them one request.

    Picking one of them is a guess about which ROM somebody wanted, so a
    nested entry is listed with a reason and cannot be selected.
    """
    t = parse_torrent(NESTED_TORRENT)
    nested = [e for e in t.entries if "/" in e.path]
    assert [e.path for e in nested] == ["NES/rom.nes", "PSP/rom.nes"]
    assert all(not e.selectable for e in nested)
    assert "more than one component" in nested[0].refusal
    with pytest.raises(TorrentError, match="cannot be fetched"):
        t.entry("NES/rom.nes")
    # The flat sibling is unaffected: one bad entry does not poison a torrent.
    assert t.entry("flat.zip").selectable


def test_a_missing_file_names_what_exists():
    with pytest.raises(TorrentError, match="has no file 'nope.zip'"):
        rubik().entry("nope.zip")


@pytest.mark.parametrize(
    "data, fragment",
    [
        (b"i1e", "must be a bencoded dictionary"),
        (b"d1:ai1ee", "no `info` dictionary"),
        (b"d4:infod4:name0:12:piece lengthi1e6:pieces0:ee", "no `name`"),
        (
            b"d4:infod4:name1:x12:piece lengthi0e6:pieces0:ee",
            "not a positive integer",
        ),
        (
            b"d4:infod4:name1:x12:piece lengthi1e6:pieces3:abcee",
            "20-byte SHA-1 hashes",
        ),
        (
            b"d4:infod4:name1:x12:piece lengthi1e6:pieces0:ee",
            "neither `files` nor a valid `length`",
        ),
        (b"not bencode at all", "not a readable .torrent"),
    ],
)
def test_a_document_that_is_not_a_torrent_is_refused(data, fragment):
    with pytest.raises(TorrentError, match=fragment):
        parse_torrent(data)


def test_an_oversized_torrent_is_refused_before_it_is_parsed():
    with pytest.raises(TorrentError, match="over the"):
        parse_torrent(b"d" + b"0" * (4 * 1024 * 1024 + 1))


def test_a_malformed_per_file_digest_becomes_no_digest_not_a_bad_one():
    """An unusable digest must never reach a comparison.

    Empty means "no digest", and `_verify` then reports the fetch as
    unverified. A half-parsed one would be worse than none: it would
    either pass wrongly or fail a good file.
    """
    raw = ben(
        {
            "info": {
                "name": "x",
                "piece length": 16384,
                "pieces": b"",
                "files": [{"length": 1, "path": ["a"], "sha1": "xxxxx"}],
            }
        }
    )
    entry = parse_torrent(raw).entries[0]
    assert entry.sha1 == ""
    assert entry.verified_by == ""


# ------------------------------------------------ gating what came back


def test_the_real_torrents_trackers_are_inside_the_allowlist():
    verdict = check_trackers(rubik().trackers, ALLOWLIST)
    assert verdict.ok
    assert "http://bt1.archive.org:6969/announce" in verdict.permitted


def test_a_tracker_on_an_undeclared_host_is_refused():
    """The case the whole gate exists for.

    A torrent whose tracker list a plugin's allowlist never mentioned is
    a way to cause traffic to an undeclared host with the plugin's
    fingerprints nowhere near it.
    """
    verdict = check_trackers(("udp://tracker.evil.example:6969/announce",), ALLOWLIST)
    assert not verdict.ok
    assert "not permitted by the plugin's network allowlist" in verdict.reasons()


def test_a_tracker_is_gated_by_host_because_check_url_cannot_be_used():
    """`http://bt1.archive.org:6969/announce` must pass, and `check_url` refuses it.

    This is the one place the ordinary gate cannot be reused verbatim.
    Asserting both halves so that "just widen ALLOWED_SCHEMES" is visibly
    the wrong fix: it would weaken the check for the five capabilities
    whose URLs the host actually fetches.
    """
    from rom_hub.netpolicy import check_url

    tracker = "http://bt1.archive.org:6969/announce"
    with pytest.raises(PolicyViolation):
        check_url(tracker, ALLOWLIST)
    assert check_trackers((tracker,), ALLOWLIST).ok


@pytest.mark.parametrize(
    "url, fragment",
    [
        ("gopher://bt1.archive.org/x", "not a tracker scheme"),
        ("http:///announce", "no host"),
        ("udp://bt1.evil.example/announce", "not permitted"),
        # The wildcard covers a subdomain, never a domain that merely
        # contains the suffix -- netpolicy's own rule, reused.
        ("http://evil-archive.org:6969/announce", "not permitted"),
        ("http://archive.org.evil.example/announce", "not permitted"),
    ],
)
def test_tracker_refusals(url, fragment):
    assert fragment in check_trackers((url,), ALLOWLIST).reasons()


def test_a_web_seed_gets_check_url_proper_so_http_mirrors_are_dropped():
    """Archive.org lists one https seed and two plain-http mirrors.

    Refusing the http ones is the correct outcome and costs nothing: the
    https entry is the one that works.
    """
    t = rubik()
    assert len(t.web_seeds) == 3
    verdict = check_web_seeds(t.web_seeds, ALLOWLIST)
    assert verdict.permitted == ("https://archive.org/download/",)
    assert len(verdict.refused) == 2
    assert all(u.startswith("http://") for u, _ in verdict.refused)


def test_a_web_seed_on_an_undeclared_host_is_refused():
    verdict = check_web_seeds(("https://evil.example/download/",), ALLOWLIST)
    assert not verdict.ok
    assert "not permitted by manifest network allowlist" in verdict.reasons()


# ---------------------------------------------------------------- magnets


def test_a_magnet_built_from_a_verified_torrent_round_trips():
    t = rubik()
    uri = magnet_for(t, ALLOWLIST)
    link = check_magnet(uri, ALLOWLIST)
    assert link.info_hash == RUBIK_BTIH
    assert link.display_name == "rubik_202308"
    assert "http://bt1.archive.org:6969/announce" in link.trackers
    # Only the https seed survives; the two http mirrors do not.
    assert link.web_seeds == ("https://archive.org/download/",)


def test_the_base32_spelling_of_an_info_hash_is_accepted():
    """Equally standard, equally common, and it must normalise to hex."""
    import base64

    b32 = base64.b32encode(bytes.fromhex(RUBIK_BTIH)).decode()
    assert check_magnet(f"magnet:?xt=urn:btih:{b32}", ALLOWLIST).info_hash == RUBIK_BTIH


@pytest.mark.parametrize(
    "uri, fragment",
    [
        # The parameter that would turn a magnet into an unrestricted
        # outbound connection: a raw IP:port with no hostname for an
        # allowlist to match.
        (
            f"magnet:?xt=urn:btih:{RUBIK_BTIH}&x.pe=203.0.113.5:6881",
            "will not accept",
        ),
        # A URL parameter in a scheme this reasoning has not been done for.
        (
            f"magnet:?xt=urn:btih:{RUBIK_BTIH}&xs=https://evil.example/t",
            "will not accept",
        ),
        (f"magnet:?xt=urn:btih:{RUBIK_BTIH}&mt=https://evil.example/l", "will not accept"),
        # Trackers and web seeds outside the allowlist.
        (
            f"magnet:?xt=urn:btih:{RUBIK_BTIH}&tr=udp://tracker.evil.example:6969",
            "outside the plugin's network allowlist",
        ),
        (
            f"magnet:?xt=urn:btih:{RUBIK_BTIH}&ws=https://evil.example/download/",
            "web seed",
        ),
        # A plain-http web seed is refused by check_url, unmodified.
        (
            f"magnet:?xt=urn:btih:{RUBIK_BTIH}&ws=http://archive.org/download/",
            "web seed",
        ),
        # The info-hash itself.
        ("magnet:?dn=x", "exactly one `xt`"),
        (f"magnet:?xt=urn:btih:{RUBIK_BTIH}&xt=urn:btih:{RUBIK_BTIH}", "exactly one"),
        ("magnet:?xt=urn:btmh:1220abcd", "v2. is not accepted"),
        ("magnet:?xt=urn:btih:zzzz", "40 hex or 32 base32"),
        # "Z" IS a base32 character; "0" and "1" are not, which is what
        # makes this the case that reaches the decoder and fails there.
        ("magnet:?xt=urn:btih:" + "0" * 32, "not valid base32"),
        ("magnet:?", "carries no parameters"),
        ("https://archive.org/x.torrent", "not a magnet URI"),
    ],
)
def test_magnet_refusals(uri, fragment):
    with pytest.raises(TorrentError, match=fragment):
        check_magnet(uri, ALLOWLIST)


def test_the_accepted_magnet_parameters_are_an_allowlist():
    """Default-deny, like manifest keys and like hosts. The third instance."""
    assert MAGNET_PARAMS == {"xt", "dn", "tr", "ws", "xl", "kt", "so"}
    assert "x.pe" not in MAGNET_PARAMS
    assert "xs" not in MAGNET_PARAMS
    assert TRACKER_SCHEMES == {"http", "https", "udp", "ws", "wss"}


def test_a_magnet_cannot_carry_a_location_the_plugin_never_declared():
    """The property, stated directly rather than through a parameter list.

    Whatever a magnet contains, everything left after `check_magnet` is
    either not a location or is on a declared host.
    """
    link = check_magnet(magnet_for(rubik(), ALLOWLIST), ALLOWLIST)
    from urllib.parse import urlsplit

    for url in link.trackers + link.web_seeds:
        host = urlsplit(url).hostname
        assert host == "archive.org" or host.endswith(".archive.org")


def test_magnet_for_refuses_to_emit_what_check_magnet_would_reject():
    """The round trip inside `magnet_for` is the point, not decoration.

    A builder that could emit something the validator refuses is a bug
    found here rather than a magnet in somebody's client.
    """
    hostile = Torrent(
        info_hash=RUBIK_BTIH,
        name="x",
        piece_length=1,
        piece_count=0,
        entries=(),
        trackers=("udp://tracker.evil.example:6969",),
        web_seeds=("https://evil.example/d/",),
    )
    # Both are dropped rather than emitted, so what comes out still validates.
    uri = magnet_for(hostile, ALLOWLIST)
    assert "evil.example" not in uri
    assert check_magnet(uri, ALLOWLIST).info_hash == RUBIK_BTIH


# ----------------------------------------------------------- the handoff


def test_a_handoff_is_named_by_its_info_hash_not_by_anything_supplied(tmp_path):
    """No plugin- or source-controlled string reaches the filesystem.

    An info-hash is 40 hex characters and content-addressed, so the same
    torrent handed over twice replaces itself.
    """
    t = rubik()
    dest = write_handoff(t, tmp_path)
    assert dest.name == f"{RUBIK_BTIH}.torrent"
    assert dest.parent == tmp_path
    # Byte for byte as received: a re-encoding is only guaranteed to
    # preserve the info-hash for input that was already canonical, and the
    # info-hash is the one property the receiving client depends on.
    assert dest.read_bytes() == (FIXTURES / "rubik_202308.torrent").read_bytes()
    assert parse_torrent(dest.read_bytes()).info_hash == RUBIK_BTIH


def test_a_handoff_stays_inside_the_directory_it_was_given(tmp_path):
    """Routed through `dest_in_job_dir` even though the name is generated.

    A containment check skipped whenever the caller believes the name is
    safe is a check that stops running exactly when somebody's belief
    turns out to be wrong.
    """
    assert handoff_path(rubik(), tmp_path).parent == tmp_path.resolve()


def test_handing_the_same_torrent_over_twice_replaces_it(tmp_path):
    write_handoff(rubik(), tmp_path)
    write_handoff(rubik(), tmp_path)
    assert len(list(tmp_path.iterdir())) == 1


# ------------------------------------------- fetching from the web seed


class FakeDownloader:
    """Serves fixture bytes for an allowlisted URL; refuses anything else.

    Deliberately re-runs the allowlist check itself rather than trusting
    the caller, so a test cannot pass by accident on a URL the real
    downloader would have refused.
    """

    def __init__(self, files: dict[str, bytes], allowlist=ALLOWLIST):
        self.files = files
        self.allowlist = allowlist
        self.requested: list[str] = []

    def download(self, url, dest, expected_size=None):
        from rom_hub.netpolicy import check_url

        check_url(url, self.allowlist)
        self.requested.append(url)
        if url not in self.files:
            raise RuntimeError(f"HTTP 404 for {url}")
        Path(dest).write_bytes(self.files[url])
        return dest

    def close(self):
        pass


RUBIK_ZIP = b"PK\x03\x04" + b"rubik payload " * 40


def seeded(entry_path="rubik.zip", body=RUBIK_ZIP):
    """A torrent whose one entry's digests describe `body`."""
    return Torrent(
        info_hash=RUBIK_BTIH,
        name="rubik_202308",
        piece_length=524288,
        piece_count=1,
        entries=(
            TorrentEntry(
                path=entry_path,
                length=len(body),
                sha1=hashlib.sha1(body).hexdigest(),
                md5=hashlib.md5(body).hexdigest(),
            ),
        ),
        trackers=("http://bt1.archive.org:6969/announce",),
        web_seeds=(
            "https://archive.org/download/",
            "http://ia902705.us.archive.org/26/items/",
        ),
    )


def test_a_web_seed_url_is_the_items_ordinary_download_url():
    """BEP 19: base + `<name>/<path>` for a multi-file torrent.

    Archive.org's is `https://archive.org/download/` + the identifier +
    the filename -- which is exactly the URL the ordinary importer would
    use. That is the whole reason no peer stack is needed.
    """
    t = rubik()
    assert web_seed_url("https://archive.org/download/", t, t.entry("rubik.zip")) == (
        "https://archive.org/download/rubik_202308/rubik.zip"
    )
    # A base without a trailing slash still joins correctly.
    assert web_seed_url("https://archive.org/download", t, t.entry("rubik.zip")) == (
        "https://archive.org/download/rubik_202308/rubik.zip"
    )


def test_fetching_pulls_from_the_https_seed_and_verifies_the_bytes(tmp_path):
    t = seeded()
    url = "https://archive.org/download/rubik_202308/rubik.zip"
    downloader = FakeDownloader({url: RUBIK_ZIP})
    got = fetch_entry(t, t.entries[0], tmp_path, ALLOWLIST, downloader=downloader)

    assert downloader.requested == [url]
    assert got.verified
    assert got.verified_by == "sha1"
    assert got.digest == hashlib.sha1(RUBIK_ZIP).hexdigest()
    assert got.size_bytes == len(RUBIK_ZIP)
    assert (tmp_path / "rubik.zip").read_bytes() == RUBIK_ZIP


def test_the_plain_http_mirror_is_never_the_one_chosen(tmp_path):
    """The seed list is filtered before one is picked, not after."""
    t = seeded()
    downloader = FakeDownloader(
        {"https://archive.org/download/rubik_202308/rubik.zip": RUBIK_ZIP}
    )
    fetch_entry(t, t.entries[0], tmp_path, ALLOWLIST, downloader=downloader)
    assert all(u.startswith("https://") for u in downloader.requested)


def test_bytes_that_do_not_match_the_torrents_digest_are_refused(tmp_path):
    t = seeded()
    url = "https://archive.org/download/rubik_202308/rubik.zip"
    wrong = b"X" * len(RUBIK_ZIP)
    with pytest.raises(TorrentError, match="failed its sha1 check"):
        fetch_entry(
            t,
            t.entries[0],
            tmp_path,
            ALLOWLIST,
            downloader=FakeDownloader({url: wrong}),
        )


def test_a_wrong_length_is_refused_before_the_digest_is_even_computed(tmp_path):
    t = seeded()
    url = "https://archive.org/download/rubik_202308/rubik.zip"
    with pytest.raises(TorrentError, match="the torrent declares"):
        fetch_entry(
            t,
            t.entries[0],
            tmp_path,
            ALLOWLIST,
            downloader=FakeDownloader({url: RUBIK_ZIP + b"!"}),
        )


def test_a_torrent_with_no_digest_reports_unverified_rather_than_passing(tmp_path):
    """It never reports a check it did not perform.

    A `.torrent` from a source that writes no per-file digest is still
    useful as a manifest and as a handoff. Claiming a verification would
    make the one guarantee this capability offers worthless.
    """
    body = b"no digest here"
    t = Torrent(
        info_hash=RUBIK_BTIH,
        name="item",
        piece_length=1024,
        piece_count=1,
        entries=(TorrentEntry(path="rom.bin", length=len(body)),),
        trackers=(),
        web_seeds=("https://archive.org/download/",),
    )
    url = "https://archive.org/download/item/rom.bin"
    got = fetch_entry(
        t, t.entries[0], tmp_path, ALLOWLIST, downloader=FakeDownloader({url: body})
    )
    assert not got.verified
    assert got.verified_by == ""
    assert "no per-file digest" in got.note


def test_md5_is_the_fallback_when_there_is_no_sha1(tmp_path):
    body = b"md5 only"
    t = Torrent(
        info_hash=RUBIK_BTIH,
        name="item",
        piece_length=1024,
        piece_count=1,
        entries=(
            TorrentEntry(
                path="rom.bin", length=len(body), md5=hashlib.md5(body).hexdigest()
            ),
        ),
        trackers=(),
        web_seeds=("https://archive.org/download/",),
    )
    url = "https://archive.org/download/item/rom.bin"
    got = fetch_entry(
        t, t.entries[0], tmp_path, ALLOWLIST, downloader=FakeDownloader({url: body})
    )
    assert got.verified_by == "md5"


def test_a_torrent_with_no_permitted_seed_refuses_and_says_to_hand_it_over(tmp_path):
    t = Torrent(
        info_hash=RUBIK_BTIH,
        name="item",
        piece_length=1024,
        piece_count=1,
        entries=(TorrentEntry(path="rom.bin", length=4),),
        trackers=(),
        web_seeds=("https://evil.example/d/", "http://archive.org/download/"),
    )
    with pytest.raises(TorrentError) as excinfo:
        fetch_entry(t, t.entries[0], tmp_path, ALLOWLIST, downloader=FakeDownloader({}))
    message = str(excinfo.value)
    assert "names no web seed the plugin's network allowlist permits" in message
    assert "torrent handoff" in message


def test_a_torrent_with_no_seeds_at_all_refuses(tmp_path):
    t = Torrent(
        info_hash=RUBIK_BTIH,
        name="item",
        piece_length=1024,
        piece_count=1,
        entries=(TorrentEntry(path="rom.bin", length=4),),
        trackers=(),
        web_seeds=(),
    )
    with pytest.raises(TorrentError, match="names no web seed"):
        fetch_entry(t, t.entries[0], tmp_path, ALLOWLIST, downloader=FakeDownloader({}))


def test_an_unselectable_entry_cannot_be_fetched(tmp_path):
    t = parse_torrent(NESTED_TORRENT)
    nested = next(e for e in t.entries if not e.selectable)
    with pytest.raises(TorrentError, match="cannot be fetched"):
        fetch_entry(t, nested, tmp_path, ALLOWLIST, downloader=FakeDownloader({}))


def test_a_hostile_entry_name_is_a_refusal_not_a_write(tmp_path):
    """The torrent is the untrusted document, so its names get the filename rules.

    `bare_filename` refuses this at parse time, so the entry arrives
    already marked unselectable and never reaches `dest_in_job_dir`. Both
    layers are kept -- this asserts the first one holds.
    """
    raw = ben(
        {
            "info": {
                "name": "x",
                "piece length": 16384,
                "pieces": b"",
                "files": [{"length": 4, "path": ["../../evil.zip"]}],
            }
        }
    )
    entry = parse_torrent(raw).entries[0]
    assert not entry.selectable
    assert "not one this host will write" in entry.refusal


# ------------------------------------------------------------ the wire type


def test_a_torrent_url_may_not_be_a_magnet_and_a_magnet_may_not_be_a_url():
    """`StreamTarget`'s rule, applied here for the same reason.

    Without it the discriminator is the hole, because picking the kind
    would pick the check.
    """
    with pytest.raises(ValueError, match="must be a magnet: URI"):
        TorrentSource(kind="magnet", source="https://archive.org/x.torrent")
    with pytest.raises(ValueError, match="must be an http\\(s\\) URL"):
        TorrentSource(kind="torrent_url", source=f"magnet:?xt=urn:btih:{RUBIK_BTIH}")
    with pytest.raises(ValueError, match="must be an http\\(s\\) URL"):
        TorrentSource(kind="torrent_url", source="file:///etc/passwd")


def test_a_wanted_file_selector_gets_the_filename_rules():
    """The same `bare_filename` a FetchPlan filename goes through."""
    for bad in ("../evil.zip", "a/b.zip", "C:evil.zip", "NUL.zip", "rom.zip "):
        with pytest.raises(ValueError):
            TorrentSource(
                kind="torrent_url",
                source="https://archive.org/x.torrent",
                files=[bad],
            )
    ok = TorrentSource(
        kind="torrent_url", source="https://archive.org/x.torrent", files=["rom.zip"]
    )
    assert ok.files == ["rom.zip"]


def test_two_selectors_naming_one_file_are_refused():
    with pytest.raises(ValueError, match="repeated"):
        TorrentSource(
            kind="torrent_url",
            source="https://archive.org/x.torrent",
            files=["rom.zip", "ROM.ZIP"],
        )


def test_a_v2_info_hash_is_refused_because_nothing_here_computes_one():
    with pytest.raises(ValueError, match="v1 \\(SHA-1\\) info-hash"):
        TorrentSource(
            kind="torrent_url",
            source="https://archive.org/x.torrent",
            info_hash="a" * 64,
        )
    source = TorrentSource(
        kind="torrent_url",
        source="https://archive.org/x.torrent",
        info_hash=RUBIK_BTIH.upper(),
    )
    assert source.info_hash == RUBIK_BTIH


def test_control_characters_are_refused_in_a_source():
    with pytest.raises(ValueError, match="control characters"):
        TorrentSource(
            kind="torrent_url", source="https://archive.org/x\r\nHost: evil"
        )


# --------------------------------------------------------------- the outcome


def test_the_json_form_carries_the_refusals_as_well_as_the_answer():
    """A consumer must be able to see what was declined and why.

    A refusal that appears only on stderr is one a launcher, a TV app or
    another Hub command cannot act on.
    """
    t = rubik()
    outcome = TorrentOutcome(
        source=TorrentSource(
            kind="torrent_url",
            source="https://archive.org/download/rubik_202308/rubik_202308_archive.torrent",
            files=["rubik.zip"],
        ),
        torrent=t,
        plugin="archive-org-torrent",
        trackers=check_trackers(t.trackers, ALLOWLIST),
        seeds=check_web_seeds(t.web_seeds, ALLOWLIST),
        magnet=magnet_for(t, ALLOWLIST),
        fetched=[
            FetchedFile(
                entry=t.entry("rubik.zip"),
                path=Path("x/rubik.zip"),
                url="https://archive.org/download/rubik_202308/rubik.zip",
                size_bytes=15420,
                digest="4f7396a71145a83f477e2dae84cf0235b7fee444",
                verified_by="sha1",
            )
        ],
    )
    data = outcome.as_dict()
    assert data["info_hash"] == RUBIK_BTIH
    assert data["wanted"] == ["rubik.zip"]
    assert len(data["files"]) == 6
    assert data["web_seeds"]["permitted"] == ["https://archive.org/download/"]
    assert len(data["web_seeds"]["refused"]) == 2
    assert data["web_seeds"]["refused"][0]["why"]
    assert data["fetched"][0]["verified"] is True
    import json

    json.dumps(data)  # it has to actually serialise
