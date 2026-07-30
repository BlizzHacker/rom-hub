"""Universal-DB plugin, replayed against a captured slice of the real database.

`tests/fixtures/universal_db/full_subset.json` is eighteen entries copied
verbatim out of a live `https://db.universal-team.net/data/full.json`
(2026-07-29, 400 entries, 1.66 MB). They were chosen because between them
they contain every awkward shape the real data has: an entry published for
both DS and 3DS, entries with no `license` key at all, one that spells its
author key `Author`, one whose only download is served over plain `http`,
one on a host nobody else uses, archives the database describes and
archives it does not, a Luma plugin and a FIRM that are not titles, and two
entries with no downloads whatsoever. None of it is invented.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "universal-db"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "universal_db"
sys.path.insert(0, str(PLUGIN_ROOT))

from universal_db.db import (  # noqa: E402
    FULL_JSON,
    UNSTATED,
    DatabaseError,
    parse_entry,
    parse_full,
)
from universal_db.filenames import MAX_CHARS, safe_filename  # noqa: E402
from universal_db.importer import ImportRefused, Importer  # noqa: E402
from universal_db.payload import (  # noqa: E402
    DECLARED_HOSTS,
    AmbiguousPayload,
    NoPayload,
    Unreachable,
    choose,
)
from universal_db.platforms import (  # noqa: E402
    format_rank,
    platform_for,
    system_for,
)
from universal_db.search import Search  # noqa: E402

from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402
from rom_hub.netpolicy import url_allowed  # noqa: E402
from rom_hub.types import SearchResult, bare_filename  # noqa: E402

SUBSET = json.loads((FIXTURES / "full_subset.json").read_text(encoding="utf-8"))
MANIFEST = PLUGIN_ROOT / "manifest.toml"


class FakeHttp:
    """Answers `full.json` from the fixture and records every call.

    Recording matters as much as answering: this plugin reads one document,
    and a change that made it read one per entry would still pass every
    assertion about the results.
    """

    def __init__(self, payload=None, status=200):
        self.payload = SUBSET if payload is None else payload
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params or {}))
        body = (
            self.payload
            if isinstance(self.payload, str)
            else json.dumps(self.payload)
        )
        return HttpResponse(status_code=self.status, text=body)


def make_search(config=None, payload=None, status=200):
    http = FakeHttp(payload, status)
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(config=None, payload=None, status=200):
    http = FakeHttp(payload, status)
    return Importer(PluginContext(config=config or {}, http=http)), http


def entry(slug):
    return next(e for e in parse_full(SUBSET) if e.slug == slug)


def plan_for(slug, platform=None, config=None):
    importer, _ = make_importer(config=config)
    return importer.plan(
        SearchResult(source_id=slug, title=slug, platform=platform)
    )


# ------------------------------------------------------------------ parsing


def test_every_fixture_record_parses():
    assert len(parse_full(SUBSET)) == len(SUBSET)


def test_a_record_with_no_systems_is_unusable():
    """`systems` is what decides the platform, so a record without one has
    nowhere to be filed and is dropped rather than defaulted to 3DS."""
    assert parse_entry({"slug": "x", "title": "X", "systems": []}) is None
    assert parse_entry({"slug": "x", "title": "X"}) is None


def test_a_record_with_no_slug_or_title_is_unusable():
    assert parse_entry({"title": "X", "systems": ["3DS"]}) is None
    assert parse_entry({"slug": "x", "systems": ["3DS"]}) is None


def test_full_json_must_be_a_list():
    with pytest.raises(DatabaseError):
        parse_full({"entries": []})


def test_a_capital_author_key_still_credits_the_author():
    """One live record spells it `Author`. Reading only `author` would drop
    the credit silently, which for homebrew is the wrong thing to lose."""
    assert entry("better-nfcreader").author == "cylin577"


def test_downloads_are_sorted_so_a_plan_is_reproducible():
    names = [d.name for d in entry("ftpd").downloads]
    assert names == sorted(names)


# ------------------------------------------------------------------ licence


def test_a_stated_licence_shows_the_databases_own_human_name():
    assert entry("universal-updater").license_label == "GNU General Public License v3.0"
    assert entry("universal-updater").license_id == "gpl-3.0"


def test_an_unstated_licence_says_unstated_and_is_not_invented():
    """130 of the 400 live entries carry no `license` key. That is a fact
    about the database, and turning it into a licence -- or dropping the
    field so the row looks like every other -- would misrepresent the one
    thing this source's usability rests on."""
    tasman = entry("tasmanquest")
    assert tasman.license_id is None
    assert tasman.license_name is None
    assert tasman.license_label == UNSTATED == "unstated"
    assert tasman.license_stated is False


def test_an_explicit_null_licence_reads_the_same_as_an_absent_one():
    """The live snapshot happens to omit the key rather than null it. Both
    mean "the database does not say", so both must land on `unstated`."""
    nulled = parse_entry(
        {"slug": "x", "title": "X", "systems": ["3DS"], "license": None,
         "license_name": None}
    )
    assert nulled.license_label == UNSTATED


def test_an_id_with_no_human_name_still_shows_the_id():
    only_id = parse_entry(
        {"slug": "x", "title": "X", "systems": ["3DS"], "license": "zlib"}
    )
    assert only_id.license_label == "zlib"


def test_every_search_row_carries_a_licence():
    search, _ = make_search()
    rows = search.search("", None, 100)
    assert rows
    assert all(r.extra["license"] for r in rows)
    labels = {r.source_id: r.extra["license"] for r in rows}
    assert labels["apotris"] == "GNU Affero General Public License v3.0 only"
    assert labels["tasmanquest"] == "unstated"


def test_require_license_hides_the_unstated_ones_and_only_those():
    search, _ = make_search(config={"require_license": True})
    rows = search.search("", None, 100)
    assert rows
    assert all(r.extra["license"] != "unstated" for r in rows)
    assert not any(r.source_id == "tasmanquest" for r in rows)
    assert any(r.source_id == "universal-updater" for r in rows)


def test_require_license_refuses_an_unstated_entry_at_import():
    with pytest.raises(ImportRefused) as excinfo:
        plan_for("better-nfcreader", config={"require_license": True})
    assert "states no licence" in str(excinfo.value)


def test_an_unstated_licence_is_importable_by_default():
    """Off by default, deliberately: not knowing the terms is a reason to
    show them, not a reason to pretend the entry does not exist."""
    assert plan_for("better-nfcreader").files[0].filename == "Better-NFCReader.cia"


# ------------------------------------------------------------------ platforms


def test_ds_and_3ds_are_separate_platforms():
    assert platform_for("3DS") == "3ds"
    assert platform_for("DS") == "nds"
    assert system_for("3ds") == "3DS"
    assert system_for("nds") == "DS"


def test_an_unknown_system_maps_to_nothing_rather_than_a_default():
    assert platform_for("Wii U") is None
    assert platform_for("") is None
    assert platform_for(None) is None


def test_a_3ds_format_is_not_a_ds_format():
    """The formats are what keep the two consoles apart on the eight
    entries published for both, so this is load-bearing, not trivia."""
    assert format_rank("3ds", "cia") == 0
    assert format_rank("3ds", "3dsx") == 1
    assert format_rank("3ds", "nds") is None
    assert format_rank("nds", "nds") == 0
    assert format_rank("nds", "dsi") == 1
    assert format_rank("nds", "cia") is None
    assert format_rank("gb", "cia") is None


# -------------------------------------------------------------------- search


def test_one_request_answers_a_whole_query():
    search, http = make_search()
    search.search("", None, 100)
    assert len(http.calls) == 1
    assert http.calls[0][0] == FULL_JSON


def test_a_dual_system_entry_produces_one_row_per_console():
    search, _ = make_search()
    rows = [r for r in search.search("pkcount", None, 50) if r.source_id == "pkcount"]
    assert sorted(r.platform for r in rows) == ["3ds", "nds"]
    assert {r.extra["systems"] for r in rows} == {"DS,3DS"}


def test_the_platform_filter_narrows_to_one_console():
    search, _ = make_search()
    rows = search.search("", "nds", 100)
    assert rows
    assert {r.platform for r in rows} == {"nds"}
    assert any(r.source_id == "wordle-ds" for r in rows)
    # ...and a 3DS-only entry is gone entirely.
    assert not any(r.source_id == "super-haxagon" for r in rows)


def test_a_platform_this_source_does_not_carry_costs_no_request():
    search, http = make_search()
    assert search.search("mario", "dc", 10) == []
    assert http.calls == []


def test_the_query_matches_the_title():
    search, _ = make_search()
    rows = search.search("haxagon", None, 50)
    assert [r.source_id for r in rows] == ["super-haxagon"]


def test_the_query_matches_the_author_too():
    """Homebrew is found by who wrote it at least as often as by its name."""
    search, _ = make_search()
    rows = search.search("Universal-Team", None, 50)
    assert any(r.source_id == "universal-updater" for r in rows)


def test_every_word_of_the_query_must_appear():
    search, _ = make_search()
    assert search.search("wordle ds", None, 50)
    assert search.search("wordle gameboy", None, 50) == []


def test_things_that_are_not_titles_never_appear():
    """A Luma plugin is injected into another game, a FIRM is boot-chain
    firmware. Neither is a thing a ROM library holds."""
    search, _ = make_search()
    slugs = {r.source_id for r in search.search("", None, 100)}
    assert "ctgp-7-downloader" not in slugs  # categories: game, plugin
    assert "nexus3ds" not in slugs  # categories: utility, firm
    assert "super-haxagon" in slugs


def test_the_category_filter_is_applied():
    search, _ = make_search(config={"category": "game"})
    rows = search.search("", None, 100)
    assert rows
    assert all("game" in r.extra["category"] for r in rows)
    assert not any(r.source_id == "universal-updater" for r in rows)


def test_limit_is_respected_across_the_per_platform_expansion():
    search, _ = make_search()
    assert len(search.search("", None, 3)) == 3


def test_results_link_to_the_entrys_own_page():
    search, _ = make_search()
    row = next(r for r in search.search("haxagon", None, 10))
    assert row.url == "https://db.universal-team.net/3ds/super-haxagon"


def test_a_non_200_is_reported_as_a_failure_not_as_no_results():
    """The site answers a missing path with 404 and an HTML body, so a
    parser that only tried json.loads would say "not JSON"."""
    search, _ = make_search(status=404)
    with pytest.raises(DatabaseError) as excinfo:
        search.search("", None, 10)
    assert "404" in str(excinfo.value)


def test_a_body_that_is_not_json_is_reported_as_such():
    search, _ = make_search(payload="<!DOCTYPE html>")
    with pytest.raises(DatabaseError) as excinfo:
        search.search("", None, 10)
    assert "not JSON" in str(excinfo.value)


# ------------------------------------------------------------------- payload


def test_a_cia_outranks_a_3dsx():
    """A CIA installs as a HOME-menu title; a 3DSX runs only from the
    Homebrew Launcher."""
    assert choose(entry("universal-updater"), "3ds").download.name == (
        "Universal-Updater.cia"
    )


def test_the_larger_build_wins_within_one_format():
    """`ftpd` ships `ftpd.cia` and `ftpd-classic.cia`; name order would
    take the classic build, size takes the current one."""
    assert choose(entry("ftpd"), "3ds").download.name == "ftpd.cia"


def test_one_entry_gives_a_different_file_to_each_console():
    ftpd = entry("ftpd")
    assert choose(ftpd, "3ds").download.name == "ftpd.cia"
    assert choose(ftpd, "nds").download.name == "ftpd.nds"


def test_an_archive_is_taken_only_on_the_databases_own_evidence():
    """CrossCraft ships four archives; the entry's `archive` manifest names
    exactly one of them as holding a 3DS title."""
    choice = choose(entry("crosscraft-classic"), "3ds")
    assert choice.download.name == "CrossCraft-3DS.zip"
    assert choice.from_archive is True


def test_the_archive_map_keys_are_regexes_not_literals():
    """`Apotris-(.*)?3(ds|DS)(-.*)?\\.zip` matches `Apotris-v4.1.03DS.zip`;
    a literal comparison would have found nothing."""
    choice = choose(entry("apotris"), "3ds")
    assert choice.download.name == "Apotris-v4.1.03DS.zip"
    assert choice.from_archive is True


def test_the_format_preference_also_decides_between_archives():
    """Super Haxagon ships a 3dsx zip and a cia zip; the map says what is
    inside each, so the same `.cia` first rule applies one level down."""
    assert choose(entry("super-haxagon"), "3ds").download.name == (
        "SuperHaxagon-3DS-armhf.cia.zip"
    )


def test_archives_the_database_describes_identically_are_refused():
    """TheXTech's one pattern matches its program archive and both of its
    asset packs. Largest would take 48 MB of level data; smallest would be
    a rule invented here. The refusal names all three."""
    with pytest.raises(AmbiguousPayload) as excinfo:
        choose(entry("thextech"), "3ds")
    message = str(excinfo.value)
    assert "thextech-3ds-v1.3.7.3.zip" in message
    assert "thextech-3ds-assets-smbx13-v1.3.7.3.zip" in message


def test_an_archive_with_no_map_is_not_guessed_at():
    """SuDokuL ships 3DS, GameCube, PSP and two Windows builds as bare
    zips with no manifest. Choosing by size takes the x64 Windows one."""
    with pytest.raises(NoPayload) as excinfo:
        choose(entry("sudokul"), "3ds")
    assert "SuDokuL-v1.5-gamecube.zip" in str(excinfo.value)


def test_an_entry_with_no_downloads_says_so():
    with pytest.raises(NoPayload) as excinfo:
        choose(entry("angband"), "3ds")
    assert "lists no downloads at all" in str(excinfo.value)


def test_a_broken_regex_in_the_data_refuses_rather_than_raising():
    broken = parse_entry(
        {
            "slug": "x",
            "title": "X",
            "systems": ["3DS"],
            "downloads": {"x.zip": {"url": "https://github.com/a/b/x.zip"}},
            "archive": {"[unclosed": {"x.cia": ["x.cia"]}},
        }
    )
    with pytest.raises(NoPayload):
        choose(broken, "3ds")


# --------------------------------------------------------- reachable hosts


def test_a_plain_http_download_is_refused_with_the_reason():
    """Two live entries publish over http. `rom_hub.netpolicy` permits
    https only; this explains that rather than working around it."""
    with pytest.raises(Unreachable) as excinfo:
        plan_for("tasmanquest")
    assert "https only" in str(excinfo.value)

    with pytest.raises(Unreachable):
        plan_for("lolsnes", platform="nds")


def test_an_undeclared_host_is_refused_by_name():
    off_allowlist = parse_entry(
        {
            "slug": "x",
            "title": "X",
            "systems": ["3DS"],
            "downloads": {"x.cia": {"url": "https://elsewhere.example/x.cia"}},
        }
    )
    from universal_db.payload import check_reachable

    with pytest.raises(Unreachable) as excinfo:
        check_reachable(off_allowlist, off_allowlist.downloads[0])
    assert "elsewhere.example" in str(excinfo.value)


def test_the_declared_hosts_and_the_manifest_agree():
    """Two copies of one list is a bug waiting to happen, so it is pinned.
    The manifest is what the broker enforces; `DECLARED_HOSTS` only exists
    so a refusal can name the host instead of surfacing as a policy error
    from inside the Hub."""
    import tomllib

    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    assert list(DECLARED_HOSTS) == manifest["permissions"]["network"]


def test_every_host_the_fixture_actually_needs_is_declared():
    """Computed from the data rather than asserted from memory: if a future
    capture pulls in an entry on a new host, this fails and says so."""
    import tomllib

    allowed = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))[
        "permissions"
    ]["network"]
    assert url_allowed(FULL_JSON, allowed), "the database itself must be reachable"

    for record in parse_full(SUBSET):
        if not record.is_title():
            continue
        for platform in record.platforms():
            try:
                choice = choose(record, platform)
            except (NoPayload, AmbiguousPayload):
                continue
            url = choice.download.url
            if not url.startswith("https://"):
                continue  # refused by netpolicy; see the http test above
            assert url_allowed(url, allowed), f"{record.slug}: {url}"


def test_the_allowlist_covers_githubs_release_redirect_target():
    """A GitHub release URL is a 302 to release-assets.githubusercontent.com
    and the broker re-checks every hop, so an undeclared target is a
    download that fails after the plan looked fine."""
    import tomllib

    allowed = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))[
        "permissions"
    ]["network"]
    assert url_allowed(
        "https://release-assets.githubusercontent.com/github-production-"
        "release-asset/1/2?sig=x",
        allowed,
    )
    assert url_allowed("https://raw.githubusercontent.com/a/b/c.3dsx", allowed)


def test_nothing_in_the_data_points_at_hshop_or_myrient():
    """Neither appears anywhere in the live database, and this plugin would
    have no way to reach them if they did. Pinned because the reason this
    source was chosen is that it is authors distributing their own work."""
    blob = json.dumps(SUBSET).lower()
    assert "hshop" not in blob
    assert "myrient" not in blob
    assert not any("hshop" in h or "myrient" in h for h in DECLARED_HOSTS)


# ------------------------------------------------------------------ importer


def test_a_plan_names_the_file_the_database_names():
    plan = plan_for("wordle-ds")
    assert len(plan.files) == 1
    assert plan.files[0].filename == "WordleDS.nds"
    assert plan.files[0].url.startswith("https://github.com/")
    assert plan.files[0].size_bytes == 978880
    assert plan.platform == "nds"
    assert plan.collection == "Homebrew"


def test_the_collection_is_configurable():
    assert plan_for("wordle-ds", config={"collection": "3DS homebrew"}).collection == (
        "3DS homebrew"
    )


def test_a_dual_system_entry_refuses_without_a_platform():
    """It ships a different file per console. Choosing would merge two
    platforms in a way nothing afterwards records as a guess."""
    with pytest.raises(ImportRefused) as excinfo:
        plan_for("pkcount")
    assert "--platform" in str(excinfo.value)


def test_a_dual_system_entry_imports_the_right_file_per_platform():
    assert plan_for("pkcount", platform="3ds").files[0].filename == "PKCount.cia"
    assert plan_for("pkcount", platform="nds").files[0].filename == "PKCount.nds"


def test_a_platform_this_source_cannot_file_under_is_refused():
    with pytest.raises(ImportRefused) as excinfo:
        plan_for("wordle-ds", platform="gba")
    assert "'gba'" in str(excinfo.value)


def test_an_unmapped_system_raises_needs_mapping_and_names_itself():
    """The live database has only `3DS` and `DS`, so this record is built
    rather than captured -- but the branch it exercises is the one that
    stops a future system being filed under a guess."""
    payload = [
        {"slug": "future", "title": "Future", "systems": ["Switch"],
         "categories": ["game"],
         "downloads": {"f.nsp": {"url": "https://github.com/a/b/f.nsp"}}}
    ]
    importer, _ = make_importer(payload=payload)
    with pytest.raises(ImportRefused) as excinfo:
        importer.plan(SearchResult(source_id="future", title="Future"))
    assert "need mapping" in str(excinfo.value)
    assert "'Switch'" in str(excinfo.value)


def test_an_unknown_slug_is_refused_rather_than_approximated():
    with pytest.raises(ImportRefused) as excinfo:
        plan_for("wordle")
    assert "no Universal-DB entry has the slug 'wordle'" in str(excinfo.value)


def test_something_that_is_not_a_title_is_refused_at_import_too():
    """Hidden from search is not enough: `--source-id` reaches the importer
    without going through search at all."""
    with pytest.raises(ImportRefused) as excinfo:
        plan_for("ctgp-7-downloader")
    assert "installable title" in str(excinfo.value)


def test_an_empty_source_id_is_refused():
    importer, http = make_importer()
    with pytest.raises(ImportRefused):
        importer.plan(SearchResult(source_id=" ", title="x"))
    assert http.calls == []


def test_an_import_reads_the_database_once():
    importer, http = make_importer()
    importer.plan(SearchResult(source_id="wordle-ds", title="x"))
    assert len(http.calls) == 1


def test_nightly_and_prerelease_builds_are_never_planned():
    """They move under the same URL, so a library row importing one stops
    describing the bytes on disk. They are also where three more download
    hosts live, so ignoring them keeps the allowlist honest."""
    universal_updater = next(
        r for r in SUBSET if r["slug"] == "universal-updater"
    )
    assert universal_updater.get("nightly"), "fixture must still exercise this"
    nightly_urls = {
        meta["url"]
        for meta in universal_updater["nightly"]["downloads"].values()
    }
    assert plan_for("universal-updater").files[0].url not in nightly_urls


# ------------------------------------------------------------------ filenames


def test_every_real_download_name_survives_untouched():
    """The point of the sanitiser is that a legitimate name never reaches
    the host's validator, not that it gets rewritten on the way. An
    over-strict version of this once dropped every GoodTools `[!]` name."""
    for record in SUBSET:
        for name in (record.get("downloads") or {}):
            assert safe_filename(name) == name, name


def test_names_the_host_would_refuse_are_made_acceptable():
    for raw in [
        "3ds/Apotris/Apotris.cia",
        "..\\..\\evil.cia",
        "C:evil.zip",
        "NUL.cia",
        "trailing. ",
        "",
    ]:
        assert bare_filename(safe_filename(raw))


def test_goodtools_style_punctuation_is_kept():
    assert safe_filename("Game (USA) [!].nds") == "Game (USA) [!].nds"


def test_sanitising_is_deterministic_including_when_truncated():
    long_name = "x" * 400 + ".3dsx"
    assert safe_filename(long_name) == safe_filename(long_name)
    assert len(safe_filename(long_name)) <= MAX_CHARS


def test_truncation_keeps_the_extension():
    """RomM routes on it, and a `.3dsx` cut down to `.3ds` would be filed as
    a cartridge dump of something that is not one."""
    assert safe_filename("y" * 400 + ".3dsx").endswith(".3dsx")


def test_a_name_that_sanitises_to_nothing_falls_back():
    assert safe_filename("...") == "download.bin"
    assert safe_filename(None) == "download.bin"


def test_every_planned_filename_passes_the_hosts_own_validator():
    for record in parse_full(SUBSET):
        if not record.is_title():
            continue
        for platform in record.platforms():
            try:
                choice = choose(record, platform)
            except (NoPayload, AmbiguousPayload):
                continue
            assert bare_filename(safe_filename(choice.download.name))
