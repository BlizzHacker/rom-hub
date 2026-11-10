"""Tests for the MiNERVA Archive plugin, against the real SDK types.

Run inside the ROMarr venv on the box, where `rom_hub_sdk` is installed --
these import the plugin's modules directly (no subprocess) and exercise the
two capabilities with a fake `ctx` that records the HTTP calls.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rom_hub_sdk import SearchResult, TorrentSource  # noqa: E402

from minerva_archive.search import Search, _info_hash, _title_of  # noqa: E402
from minerva_archive.torrent import Torrent, TorrentRefused  # noqa: E402


# -- a fake plugin context ------------------------------------------------------

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Http:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.answers(url, dict(params or {}))


class _Ctx:
    def __init__(self, config, http):
        self.config = config
        self.http = http


def _search(config, answers):
    http = _Http(answers)
    return Search(_Ctx(config, http)), http


ROW = {"id": 823208, "so_id": 29812,
       "full_path": "./TOSEC/Nintendo/Super Famicom/Games/"
                    "Chrono Trigger (1995)(Square)(US).zip",
       "magnet": "magnet:?xt=urn:btih:"
                 "c5c78ab0432dcc14fec568ba5daad2d9cc98f4dc&dn=Minerva_Myrient"}
ALT = dict(ROW, id=823244, so_id=29813,   # same magnet -> same torrent
           full_path="./TOSEC/Nintendo/Super Famicom/Games/"
                     "Chrono Trigger (1995)(Square)(US)[a8].zip")
OTHER = {"id": 900001, "so_id": 30001,
         "full_path": "./No-Intro/Nintendo 64/Games/Super Mario 64 (USA).z64",
         "magnet": "magnet:?xt=urn:btih:"
                   "1111111111111111111111111111111111111111&dn=Minerva_Myrient"}


# -- helpers --------------------------------------------------------------------

def test_title_is_the_last_path_component():
    assert _title_of(ROW["full_path"]) == "Chrono Trigger (1995)(Square)(US).zip"
    assert _title_of("./a/b/c/") == "c"
    assert _title_of("") == ""


def test_info_hash_is_read_from_the_magnet():
    assert _info_hash(ROW["magnet"]) == \
        "c5c78ab0432dcc14fec568ba5daad2d9cc98f4dc"
    assert _info_hash("magnet:?dn=x") == ""
    assert _info_hash("https://not-a-magnet") == ""


# -- search ---------------------------------------------------------------------

def test_search_lowercases_the_query_and_calls_the_api():
    s, http = _search({}, lambda u, p: _Resp(200, [ROW]))
    out = s.search("Chrono Trigger", None, 100)
    assert http.calls[0][1]["query"] == "chrono trigger"
    assert len(out) == 1
    assert out[0].title == "Chrono Trigger (1995)(Square)(US).zip"
    assert out[0].source_id.startswith("minerva-row:823208:")
    assert out[0].extra["id"] == "823208"
    assert out[0].extra["so_id"] == "29812"
    assert out[0].extra["torrent_magnet"] == ROW["magnet"]
    assert out[0].size_bytes is None, "unknown size must be None, never 0"
    assert out[0].url.endswith("/rom?id=823208")
    assert out[0].extra["path"] == ROW["full_path"]


def test_matching_file_rows_sharing_a_magnet_stay_distinct():
    s, http = _search({}, lambda u, p: _Resp(200, [ROW, ALT, ROW, OTHER]))
    out = s.search("chrono trigger", None, 100)
    chrono = [r for r in out if "Chrono Trigger" in r.title]
    assert len(chrono) == 2, "different matching file rows must not collapse by torrent"
    assert {r.extra["id"] for r in chrono} == {"823208", "823244"}
    assert {r.extra["path"] for r in chrono} == {
        ROW["full_path"], ALT["full_path"]
    }
    assert all(r.extra["torrent_magnet"] == ROW["magnet"] for r in chrono)
    assert chrono[0].source_id != chrono[1].source_id
    assert all(url == "https://minerva-archive.org/v1/api/rom/search"
               for url, _ in http.calls)
    assert all("/rom?id=" not in url and "/browse/" not in url
               for url, _ in http.calls)
    assert any(r.title == "Super Mario 64 (USA).z64" for r in out)


def test_zero_limit_returns_no_rows():
    s, _ = _search({}, lambda u, p: _Resp(200, [ROW]))
    assert s.search("chrono", None, 0) == []


def test_empty_filters_are_not_sent():
    s, http = _search({}, lambda u, p: _Resp(200, []))
    s.search("mario", None, 100)
    params = http.calls[0][1]
    assert "group" not in params and "console" not in params
    assert "region" not in params and "tokenised" not in params


def test_filters_are_passed_through():
    s, http = _search({"group": "TOSEC", "console": "Nintendo 64",
                       "region": "USA", "tokenised": True},
                      lambda u, p: _Resp(200, []))
    s.search("mario", None, 100)
    params = http.calls[0][1]
    assert params["group"] == "TOSEC"
    assert params["console"] == "Nintendo 64"
    assert params["region"] == "USA"
    assert params["tokenised"] == "true"


def test_an_empty_query_never_hits_the_network():
    s, http = _search({}, lambda u, p: _Resp(200, [ROW]))
    assert s.search("", None, 100) == []
    assert http.calls == []


def test_pagination_advances_offset_on_a_full_page():
    def rows(start, count):
        # distinct info-hashes per row, and per page, so nothing dedupes
        return [{"id": start + i, "so_id": start + i,
                 "full_path": f"./x/g{start + i}.zip",
                 "magnet": f"magnet:?xt=urn:btih:{(start + i):040x}"}
                for i in range(count)]

    state = {"n": 0}

    def answers(url, params):
        state["n"] += 1
        return _Resp(200, rows(0, 100) if state["n"] == 1 else rows(1000, 5))

    s, http = _search({"max_pages": 3}, answers)
    out = s.search("anything", None, 500)
    assert [c[1]["offset"] for c in http.calls] == [0, 100]
    assert all(c[0] == "https://minerva-archive.org/v1/api/rom/search"
               for c in http.calls)
    assert len(out) == 105


def test_max_pages_caps_the_walk():
    full = [{"id": i, "so_id": i, "full_path": f"./x/g{i}.zip",
             "magnet": f"magnet:?xt=urn:btih:{i:040x}"} for i in range(100)]
    s, http = _search({}, lambda u, p: _Resp(200, full))
    s.search("anything", None, 500)
    assert len(http.calls) == 1


def test_junk_rows_are_skipped():
    s, _ = _search({}, lambda u, p: _Resp(200, [
        {"id": 1, "full_path": "", "magnet": ROW["magnet"]},
        {"id": 2, "full_path": "./x/g.zip", "magnet": ""},
        {"id": 3, "full_path": "./x/g.zip", "magnet": "http://x"},
        "not-a-dict",
        OTHER,
    ]))
    out = s.search("mario", None, 100)
    assert [r.title for r in out] == ["Super Mario 64 (USA).z64"]


def test_a_non_200_stops_with_what_it_has():
    state = {"n": 0}

    def answers(url, params):
        state["n"] += 1
        return _Resp(200, [ROW]) if state["n"] == 1 else _Resp(500, None)

    s, http = _search({"max_pages": 5}, answers)
    out = s.search("chrono", None, 100)
    assert len(out) == 1, "page one's results survive page two's failure"


# -- torrent --------------------------------------------------------------------

def test_resolve_adds_trackers_and_keeps_the_info_hash():
    t = Torrent(_Ctx({}, None))
    src = t.resolve(SearchResult(source_id=ROW["magnet"], title="x"))
    assert isinstance(src, TorrentSource)
    assert src.kind == "magnet"
    assert src.info_hash == "c5c78ab0432dcc14fec568ba5daad2d9cc98f4dc"
    assert src.source.count("tr=") == 12
    assert "xt=urn:btih:c5c78ab0" in src.source


def test_resolve_uses_the_collection_magnet_for_a_file_row_source_id():
    t = Torrent(_Ctx({}, None))
    result = SearchResult(
        source_id="minerva-row:823208:abc123",
        title="Chrono Trigger.zip",
        extra={"torrent_magnet": ROW["magnet"], "path": ROW["full_path"]},
    )
    src = t.resolve(result)
    assert src.info_hash == "c5c78ab0432dcc14fec568ba5daad2d9cc98f4dc"
    assert src.kind == "magnet"


def test_resolve_refuses_a_non_magnet():
    t = Torrent(_Ctx({}, None))
    try:
        t.resolve(SearchResult(source_id="https://x/y", title="x"))
        assert False, "should have raised"
    except TorrentRefused:
        pass


def test_resolve_refuses_a_magnet_with_no_v1_hash():
    t = Torrent(_Ctx({}, None))
    try:
        t.resolve(SearchResult(source_id="magnet:?dn=nope", title="x"))
        assert False, "should have raised"
    except TorrentRefused as e:
        assert "info-hash" in str(e)


def test_a_base32_hash_is_normalised_to_hex():
    import base64
    t = Torrent(_Ctx({}, None))
    raw = bytes(range(20))
    b32 = base64.b32encode(raw).decode()          # 32 chars, padded
    src = t.resolve(SearchResult(
        source_id=f"magnet:?xt=urn:btih:{b32}", title="x"))
    assert src.info_hash == raw.hex()


def test_the_host_accepts_the_resolved_magnet():
    """The real gate: rom_hub.torrents.check_magnet must accept what resolve
    returns, using this plugin's own declared allowlist."""
    from rom_hub.torrents import check_magnet
    import tomllib
    manifest = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "manifest.toml").read_text())
    allowlist = list(manifest["permissions"]["network"])

    t = Torrent(_Ctx({}, None))
    src = t.resolve(SearchResult(source_id=ROW["magnet"], title="x"))
    link = check_magnet(src.source, allowlist)
    assert link.info_hash == src.info_hash
    assert len(link.trackers) == 12, "every declared tracker must survive"


# -- platform mapping (issue #2) ------------------------------------------------

def test_a_platform_argument_becomes_a_console_filter():
    """The whole point of the fix: `search(query, platform, limit)` with a
    platform the plugin maps must send MiNERVA the console filter, not
    search every system and hope."""
    s, http = _search({}, lambda u, p: _Resp(200, [ROW]))
    s.search("chrono trigger", "snes", 100)
    consoles = sorted(c[1].get("console") for c in http.calls)
    assert consoles == ["Nintendo Super Famicom",
                        "Super Nintendo Entertainment System"]


def test_an_explicit_console_config_wins_over_the_platform_argument():
    """The operator typed the filter-bar value; nothing may silently AND a
    second console onto it."""
    s, http = _search({"console": "Nintendo 64"}, lambda u, p: _Resp(200, []))
    s.search("mario", "ps2", 100)
    assert len(http.calls) == 1
    assert http.calls[0][1]["console"] == "Nintendo 64"


def test_an_unmapped_platform_searches_unfiltered_not_empty():
    s, http = _search({}, lambda u, p: _Resp(200, [ROW]))
    out = s.search("game", "calculon-9000", 100)
    assert len(http.calls) == 1
    assert "console" not in http.calls[0][1]
    assert len(out) == 1, "an unmapped slug must not mean 'no rows exist'"


def test_no_platform_argument_searches_unfiltered():
    s, http = _search({}, lambda u, p: _Resp(200, [ROW]))
    s.search("game", None, 100)
    assert "console" not in http.calls[0][1]


def test_results_are_tagged_with_the_platform_of_the_filter_that_found_them():
    """The API rows carry no console field, so the filter that produced the
    row is the platform evidence. A row found under "Sony PlayStation 2"
    comes back with platform="ps2"."""
    def answers(url, params):
        return _Resp(200, [ROW] if params.get("console") == "Sony PlayStation 2"
                     else [])

    s, http = _search({}, answers)
    out = s.search("chrono trigger", "ps2", 100)
    assert len(out) == 1
    assert out[0].platform == "ps2"
    # every mapped console was asked about exactly once
    asked = sorted(c[1].get("console") for c in http.calls)
    assert asked == ["Sony PlayStation 2"]


def test_unfiltered_results_keep_platform_none():
    s, _ = _search({}, lambda u, p: _Resp(200, [ROW]))
    out = s.search("chrono trigger", None, 100)
    assert out[0].platform is None


def test_each_console_filter_pages_independently():
    """The first-100-rows failure was pagination across every system; a
    platform search must page *within* each console it asks about."""
    full = [{"id": i, "so_id": i, "full_path": f"./x/g{i}.zip",
             "magnet": f"magnet:?xt=urn:btih:{i:040x}"} for i in range(100)]
    calls = []

    def answers(url, params):
        calls.append(dict(params))
        if params.get("console") == "Nintendo Super Famicom":
            # two full pages under one spelling, nothing under the other
            return _Resp(200, full if params["offset"] < 100 else [])
        return _Resp(200, [])

    s, http = _search({"max_pages": 2}, answers)
    out = s.search("chrono trigger", "snes", 500)
    snes_offsets = [c["offset"] for c in calls
                    if c.get("console") == "Nintendo Super Famicom"]
    assert snes_offsets == [0, 100]
    assert len(out) == 100


def test_the_mapping_names_only_consoles_the_live_api_lists():
    """Every console in the forward map was verified against
    /v1/api/rom/filters on 2026-11-09; this pins the list so a typo or an
    upstream rename shows up as a failing test against the recorded set
    rather than as silently-empty searches."""
    from minerva_archive.platforms import PLATFORM_CONSOLES
    recorded = (Path(__file__).resolve().parent / "fixtures_consoles.json")
    if recorded.exists():
        import json
        known = set(json.loads(recorded.read_text()))
        unknown = {c for cs in PLATFORM_CONSOLES.values() for c in cs} - known
        assert not unknown, f"consoles MiNERVA no longer lists: {sorted(unknown)}"
