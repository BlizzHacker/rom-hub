"""demozoo, replayed against captured Demozoo API documents.

`tests/fixtures/demozoo/` holds ten verbatim bodies from
`https://demozoo.org/api/v1/`, captured 2026-07-29 while honouring that
site's `Crawl-delay: 10`:

    platforms.json              all 93 platforms
    production_types.json       all 57 production types
    search_second_reality.json  ?title=second reality      -- 7 rows
    browse_c64_cracktro.json    ?platform=3&production_type=13 -- 6,487
    browse_windows_demo.json    ?platform=1&production_type=1  -- 6,450
    prod_282136_sceneorg.json   csdb.dk then files.scene.org
    prod_144105_fujiology.json  fujiology.org only
    prod_341803_csdb_only.json  csdb.dk only
    prod_309_amigascne.json     ftp.amigascne.org only, over http
    prod_62892_video.json       supertype `production`, type `Video`

The `second reality` capture is the fixture that earns its keep. Its
seven rows are seven different answers to "can a ROM library hold this?":
two carry no platform at all, one is a `Video`, one is `Music`, one is on
`Mobile`, and three -- the ZX Spectrum, C64 and MS-Dos demos, the last of
them Future Crew's -- are real importable productions. Any filter that is
wrong in any direction shows up as a different number here.

The `browse_windows_demo` capture is the other half: 6,450 rows of real
demos that are all `Windows`, which is not a library platform and never
will be. A hundred rows in, nothing out, and the request budget is what
stops it.

No test opens a socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "demozoo"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "demozoo"
sys.path.insert(0, str(PLUGIN_ROOT))

from demozoo.filenames import FALLBACK, MAX_CHARS, safe_filename  # noqa: E402
from demozoo.importer import ImportRefused, Importer  # noqa: E402
from demozoo.links import (  # noqa: E402
    DECLINED_HOSTS,
    SCENE_ORG_MIRROR,
    SUPPORTED_HOSTS,
    NoUsableDownload,
    resolve,
)
from demozoo.platforms import (  # noqa: E402
    AMBIGUOUS,
    NOT_A_LIBRARY_PLATFORM,
    PLATFORMS,
    NeedsMapping,
    demozoo_ids_for_slug,
    require_slug,
    slug_for,
)
from demozoo.productions import (  # noqa: E402
    EXCLUDED_TYPES,
    IMPORTABLE_TYPE_IDS,
    NotImportable,
    UnknownType,
    is_importable,
    parse_production,
    require_importable,
    type_id_for,
)
from demozoo.search import MAX_LIMIT, Search, SearchError  # noqa: E402

from rom_hub.manifest import parse_manifest  # noqa: E402
from rom_hub.netpolicy import url_allowed  # noqa: E402
from rom_hub.types import SearchResult, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


PLATFORMS_JSON = json.loads(fixture("platforms.json"))
TYPES_JSON = json.loads(fixture("production_types.json"))
SECOND_REALITY = fixture("search_second_reality.json")
C64_CRACKTRO = fixture("browse_c64_cracktro.json")
WINDOWS_DEMO = fixture("browse_windows_demo.json")
PROD_SCENEORG = fixture("prod_282136_sceneorg.json")
PROD_FUJIOLOGY = fixture("prod_144105_fujiology.json")
PROD_CSDB_ONLY = fixture("prod_341803_csdb_only.json")
PROD_AMIGASCNE = fixture("prod_309_amigascne.json")
PROD_VIDEO = fixture("prod_62892_video.json")

MANIFEST = (PLUGIN_ROOT / "manifest.toml").read_text(encoding="utf-8")
ALLOWLIST = parse_manifest(MANIFEST).network


class FakeHttp:
    """Serves one body per call, and records url + params verbatim."""

    def __init__(self, bodies, status=200):
        self.bodies = bodies if isinstance(bodies, list) else [bodies]
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        index = min(len(self.calls) - 1, len(self.bodies) - 1)
        return HttpResponse(status_code=self.status, text=self.bodies[index])


def make_search(bodies, config=None, status=200):
    http = FakeHttp(bodies, status)
    return Search(PluginContext(config=config or {}, http=http)), http


def make_importer(bodies, config=None, status=200):
    http = FakeHttp(bodies, status)
    return Importer(PluginContext(config=config or {}, http=http)), http


# ------------------------------------------------------------- platforms


def test_the_platform_table_uses_demozoos_own_spellings():
    """Every key must be a name Demozoo actually serves, and every id must
    be the id Demozoo gives that name. A tidied-up spelling here would
    simply never match, silently."""
    live = {row["name"]: row["id"] for row in PLATFORMS_JSON["results"]}
    assert PLATFORMS_JSON["count"] == 93
    for name, entry in PLATFORMS.items():
        assert name in live, name
        assert entry.id == live[name], name


def test_every_named_absence_is_also_a_real_demozoo_platform():
    live = {row["name"] for row in PLATFORMS_JSON["results"]}
    for name in list(NOT_A_LIBRARY_PLATFORM) + list(AMBIGUOUS):
        assert name in live, name


def test_the_three_absence_tables_do_not_overlap():
    assert set(PLATFORMS).isdisjoint(NOT_A_LIBRARY_PLATFORM)
    assert set(PLATFORMS).isdisjoint(AMBIGUOUS)
    assert set(NOT_A_LIBRARY_PLATFORM).isdisjoint(AMBIGUOUS)


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Commodore 64", "c64"),
        ("Amiga OCS/ECS", "amiga"),
        ("Amiga AGA", "amiga"),
        ("Amiga PPC/RTG", "amiga"),
        ("ZX Spectrum", "zxs"),
        ("ZX Spectrum Enhanced", "zxs"),
        ("MS-Dos", "dos"),
        ("Atari ST/E", "atari-st"),
        ("Amstrad CPC", "acpc"),
        ("Nintendo SNES/Super FamiCom", "snes"),
        ("Atari 2600 Video Computer System (VCS)", "atari2600"),
    ],
)
def test_platforms_map_to_the_slug_the_library_uses(name, slug):
    assert slug_for(name) == slug
    assert require_slug(name) == slug


def test_one_romm_slug_can_be_several_demozoo_platforms():
    """`amiga` is three of them, which is why a platform browse is three
    requests rather than one."""
    assert demozoo_ids_for_slug("amiga") == [5, 6, 26]
    assert demozoo_ids_for_slug("c64") == [3]
    assert demozoo_ids_for_slug("zxs") == [2, 69]
    assert demozoo_ids_for_slug("nonesuch") == []


def test_an_unmapped_platform_says_to_add_a_row():
    with pytest.raises(NeedsMapping) as exc:
        require_slug("Acorn Archimedes")
    assert "needs mapping" in str(exc.value)
    assert "demozoo/platforms.py" in str(exc.value)


def test_a_platform_that_is_not_a_library_platform_says_so_instead():
    """"Add a row" would be the wrong advice: there is no shelf in a ROM
    library for a Windows executable and there never will be."""
    with pytest.raises(NeedsMapping) as exc:
        require_slug("Windows")
    assert "not a library platform" in str(exc.value)
    assert "no row" in str(exc.value)


def test_an_ambiguous_platform_refuses_rather_than_picking_a_side():
    with pytest.raises(NeedsMapping) as exc:
        require_slug("Neo Geo")
    assert "ambiguous" in str(exc.value)
    assert "neogeoaes" in str(exc.value)


# ------------------------------------------------------- production types


def test_the_type_table_uses_demozoos_own_names_and_ids():
    live = {row["name"]: row["id"] for row in TYPES_JSON["results"]}
    assert TYPES_JSON["count"] == 57
    for name, type_id in IMPORTABLE_TYPE_IDS.items():
        assert name in live, name
        assert live[name] == type_id, name


def test_every_importable_type_is_a_production_not_graphics_or_music():
    """`Executable Graphics` and `Executable Music` really are executables,
    and they are still out of scope -- deliberately, in one place."""
    supertypes = {row["name"]: row["supertype"] for row in TYPES_JSON["results"]}
    for name in IMPORTABLE_TYPE_IDS:
        assert supertypes[name] == "production", name


def test_video_is_a_production_supertype_and_is_still_excluded():
    """The single most important line in the filter. A supertype-only rule
    would keep it, and its download links are usually YouTube."""
    supertypes = {row["name"]: row["supertype"] for row in TYPES_JSON["results"]}
    assert supertypes["Video"] == "production"
    assert "Video" not in IMPORTABLE_TYPE_IDS
    assert "youtube" in EXCLUDED_TYPES["video"].lower()


def test_a_configured_type_this_plugin_does_not_import_is_refused_by_name():
    assert type_id_for("Cracktro") == 13
    assert type_id_for("cracktro") == 13
    with pytest.raises(UnknownType) as exc:
        type_id_for("Video")
    assert "Video" in str(exc.value)


# ------------------------------------------------------------- filtering


def test_the_seven_second_reality_rows_reduce_to_the_three_that_can_be_held():
    """Seven real productions with the same title, and seven different
    answers. This is the filter in one assertion."""
    rows = json.loads(SECOND_REALITY)["results"]
    assert len(rows) == 7
    kept = [p for p in (parse_production(r) for r in rows) if p and is_importable(p)]
    assert [(p.id, p.mapped_platform) for p in kept] == [
        (13695, "ZX Spectrum"),
        (19822, "Commodore 64"),
        (108, "MS-Dos"),
    ]


@pytest.mark.parametrize(
    "production_id,why",
    [
        (188714, "no platform at all"),
        (145154, "a Video with no platform"),
        (56779, "Mobile, which has no RomM slug"),
        (126321, "typed Music"),
    ],
)
def test_each_dropped_second_reality_row_is_dropped_for_its_own_reason(
    production_id, why
):
    rows = {r["id"]: r for r in json.loads(SECOND_REALITY)["results"]}
    production = parse_production(rows[production_id])
    assert not is_importable(production), why
    with pytest.raises(NotImportable):
        require_importable(production)


def test_a_video_entry_refuses_with_the_youtube_reason_spelled_out():
    production = parse_production(json.loads(PROD_VIDEO))
    with pytest.raises(NotImportable) as exc:
        require_importable(production)
    # It has no platform either; the type check runs first, on purpose.
    assert "Video" in str(exc.value)
    assert "YouTube" in str(exc.value)


def test_a_production_with_several_types_is_importable_if_any_of_them_is():
    production = parse_production(json.loads(PROD_SCENEORG))
    assert production.type_names == ("Cracktro", "2K Intro")
    assert require_importable(production) == ("Commodore 64", "c64")


def test_a_malformed_row_is_skipped_rather_than_raising():
    assert parse_production(None) is None
    assert parse_production({"id": 1}) is None
    assert parse_production({"title": "x"}) is None
    assert parse_production({"id": True, "title": "x"}) is None


# ----------------------------------------------------------------- links


def test_a_scene_org_view_link_becomes_a_pinned_https_mirror_get():
    """The whole point of links.py. `/view/` is an HTML page, the default
    `/get/` 302s to plain http, and only the pinned mirror is https."""
    links = json.loads(PROD_SCENEORG)["download_links"]
    assert [entry["link_class"] for entry in links] == ["BaseUrl", "SceneOrgFile"]
    download = resolve(links)
    assert download.url == (
        f"https://files.scene.org/{SCENE_ORG_MIRROR}/parties/2020/asoa20/"
        f"c64_fast_intro/2.8k_nuance__onslaught.zip"
    )
    assert download.host == "files.scene.org"
    assert download.raw_name == "2.8k_nuance__onslaught.zip"


def test_resolve_skips_a_declined_host_to_reach_a_supported_one():
    """That production's first link is csdb.dk. Stopping at the first link
    would have refused an import that is perfectly possible."""
    links = json.loads(PROD_SCENEORG)["download_links"]
    assert "csdb.dk" in links[0]["url"]
    assert resolve(links).host == "files.scene.org"


def test_a_fujiology_link_is_used_as_it_stands():
    links = json.loads(PROD_FUJIOLOGY)["download_links"]
    download = resolve(links)
    assert download.url == "https://fujiology.org/8BIT/FRIDAY/ONE_YEAR.ZIP"
    assert download.raw_name == "ONE_YEAR.ZIP"


def test_a_production_whose_only_host_is_csdb_is_refused_by_name():
    with pytest.raises(NoUsableDownload) as exc:
        resolve(json.loads(PROD_CSDB_ONLY)["download_links"])
    message = str(exc.value)
    assert "csdb.dk" in message
    assert "ClaudeBot" in message
    assert "will not work around" in message


def test_a_production_whose_only_host_is_amigascne_is_refused_by_name():
    links = json.loads(PROD_AMIGASCNE)["download_links"]
    assert links[0]["url"].startswith("http://")
    with pytest.raises(NoUsableDownload) as exc:
        resolve(links)
    message = str(exc.value)
    assert "ftp.amigascne.org" in message
    assert "Disallow: /" in message


def test_a_production_with_no_links_at_all_says_so():
    with pytest.raises(NoUsableDownload) as exc:
        resolve([])
    assert "no download link" in str(exc.value)


def test_a_scene_org_link_is_percent_decoded_for_the_filename_only():
    download = resolve(
        [
            {
                "link_class": "SceneOrgFile",
                "url": "https://files.scene.org/view/parties/x/2.8K%20Nuance%21.zip",
            }
        ]
    )
    assert download.raw_name == "2.8K Nuance!.zip"
    assert safe_filename(download.raw_name) == "2.8K Nuance!.zip"


def test_a_percent_encoded_separator_cannot_escape_the_filename():
    """Decoding happens for display; the sanitiser is what holds."""
    download = resolve(
        [
            {
                "link_class": "SceneOrgFile",
                "url": "https://files.scene.org/view/x/%2E%2E%2Fetc%2Fpasswd",
            }
        ]
    )
    name = safe_filename(download.raw_name)
    assert bare_filename(name) == name
    assert "/" not in name


def test_supported_hosts_and_the_manifest_allowlist_agree():
    """Two lists that must not drift: a host this table resolves to but
    the manifest does not declare fails as an opaque policy violation."""
    for host in SUPPORTED_HOSTS:
        assert url_allowed(f"https://{host}/x", ALLOWLIST), host
    for host in DECLINED_HOSTS:
        assert not url_allowed(f"https://{host}/x", ALLOWLIST), host


def test_the_manifest_declares_the_scene_org_redirect_target():
    """files.scene.org/get:nl-https/<p> 302s to archive.scene.org, and the
    host re-checks every hop. Verified live 2026-07-29."""
    assert sorted(ALLOWLIST) == [
        "archive.scene.org",
        "demozoo.org",
        "files.scene.org",
        "fujiology.org",
    ]
    assert url_allowed("https://archive.scene.org/pub/parties/x.zip", ALLOWLIST)


def test_the_pinned_mirror_is_the_https_one():
    """`get:us-http` and the bare `get` both land on plain http, which
    `rom_hub.netpolicy` refuses outright."""
    assert SCENE_ORG_MIRROR == "get:nl-https"
    assert not url_allowed("http://http.us.scene.org/pub/x.zip", ALLOWLIST)


# ---------------------------------------------------------------- search


def test_search_returns_only_the_rows_an_import_would_accept():
    search, http = make_search(SECOND_REALITY)
    results = search.search("second reality", None, 20)
    assert [r.source_id for r in results] == ["13695", "19822", "108"]
    assert [r.platform for r in results] == ["zxs", "c64", "dos"]
    assert results[2].title == "Second Reality"
    assert results[2].extra["author"] == "Future Crew"
    assert results[2].extra["released"] == "1993-10-07"
    assert results[2].url == "https://demozoo.org/productions/108/"
    assert len(http.calls) == 1


def test_search_sends_title_and_never_a_parameter_the_api_ignores():
    """`?search=`, `?q=` and `?title__icontains=` are all silently ignored
    by this API and return the unfiltered 386,682-row listing. A plugin
    that sent one would look like it was searching."""
    search, http = make_search(SECOND_REALITY)
    search.search("second reality", None, 5)
    url, params = http.calls[0]
    assert url == "https://demozoo.org/api/v1/productions/"
    assert params["title"] == "second reality"
    assert params["format"] == "json"
    assert set(params) <= {"format", "title", "platform", "production_type", "page"}


def test_a_platform_search_issues_one_request_per_demozoo_platform_id():
    search, http = make_search([SECOND_REALITY, SECOND_REALITY, SECOND_REALITY])
    search.search("second reality", "amiga", 200)
    assert [params["platform"] for _, params in http.calls] == [5, 6, 26]


def test_a_platform_demozoo_does_not_index_returns_nothing_without_asking():
    search, http = make_search(SECOND_REALITY)
    assert search.search("x", "playdate", 10) == []
    assert http.calls == []


def test_the_same_production_is_never_returned_twice_across_streams():
    """The three Amiga streams are served the same body here; a production
    reached from two of them must still appear once."""
    search, _ = make_search([SECOND_REALITY, SECOND_REALITY, SECOND_REALITY])
    results = search.search("second reality", "amiga", 200)
    assert len(results) == len({r.source_id for r in results})


def test_a_browse_of_windows_demos_yields_almost_nothing_and_stops_at_the_budget():
    """6,450 real demos on a platform no ROM library has a shelf for.

    Exactly one row in the captured hundred survives, and it is worth
    knowing why: `3½ Inches Is Enough` lists `Linux`, `Mac OS (Classic)`
    and `Windows`, and the classic Mac build *is* a retro binary, so it
    maps to `mac`. Five other rows are equally multi-platform and are all
    Java/Linux/macOS, which map to nothing. Without the request budget
    this browse would page through sixty-four more screens of the same.
    """
    search, http = make_search(WINDOWS_DEMO, config={"max_requests": 3})
    results = search.search("", None, 50)
    assert [(r.source_id, r.platform) for r in results] == [("71663", "mac")]
    assert len(http.calls) == 3
    assert [params["page"] for _, params in http.calls] == [1, 2, 3]


def test_a_browse_of_c64_cracktros_returns_them():
    search, http = make_search(C64_CRACKTRO)
    results = search.search("", "c64", 5)
    assert len(results) == 5
    assert all(r.platform == "c64" for r in results)
    assert results[0].title == "[!+$] Tribute Intro"
    assert "Cracktro" in results[0].extra["types"]
    assert http.calls[0][1]["platform"] == 3
    assert "title" not in http.calls[0][1]


def test_the_production_type_config_is_sent_as_demozoos_own_id():
    search, http = make_search(C64_CRACKTRO, config={"production_type": "Cracktro"})
    search.search("", "c64", 3)
    assert http.calls[0][1]["production_type"] == 13


def test_a_production_type_config_this_plugin_cannot_import_is_refused():
    search, _ = make_search(C64_CRACKTRO, config={"production_type": "Video"})
    with pytest.raises(UnknownType):
        search.search("", "c64", 3)


def test_limit_is_honoured_and_bounded():
    search, _ = make_search(C64_CRACKTRO)
    assert len(search.search("", "c64", 3)) == 3
    assert len(search.search("", "c64", 10**9)) <= MAX_LIMIT


def test_a_non_200_is_an_error_rather_than_an_empty_result():
    search, _ = make_search(SECOND_REALITY, status=503)
    with pytest.raises(SearchError) as exc:
        search.search("x", None, 5)
    assert "503" in str(exc.value)


def test_a_200_that_is_not_json_is_an_error():
    search, _ = make_search("<html>maintenance</html>")
    with pytest.raises(SearchError) as exc:
        search.search("x", None, 5)
    assert "not JSON" in str(exc.value)


def test_every_search_result_validates_as_a_search_result():
    search, _ = make_search(C64_CRACKTRO)
    for result in search.search("", "c64", 20):
        SearchResult(**result.model_dump())


# -------------------------------------------------------------- importer


def test_an_import_plans_the_scene_org_download_for_the_right_platform():
    importer, http = make_importer(PROD_SCENEORG)
    plan = importer.plan(SearchResult(source_id="282136", title="2.8K Nuance!"))
    assert len(plan.files) == 1
    entry = plan.files[0]
    assert entry.url == (
        "https://files.scene.org/get:nl-https/parties/2020/asoa20/"
        "c64_fast_intro/2.8k_nuance__onslaught.zip"
    )
    assert entry.filename == "2.8k_nuance__onslaught.zip"
    assert plan.platform == "c64"
    assert plan.collection == "Demozoo"
    assert http.calls[0][0] == "https://demozoo.org/api/v1/productions/282136/"


def test_an_import_plans_a_fujiology_download():
    importer, _ = make_importer(PROD_FUJIOLOGY)
    plan = importer.plan(SearchResult(source_id="144105", title="1 Jahr Top-Magazin"))
    assert plan.files[0].url == "https://fujiology.org/8BIT/FRIDAY/ONE_YEAR.ZIP"
    assert plan.platform == "atari8bit"


def test_every_planned_url_is_permitted_by_the_manifests_own_allowlist():
    for body in (PROD_SCENEORG, PROD_FUJIOLOGY):
        importer, _ = make_importer(body)
        source_id = str(json.loads(body)["id"])
        plan = importer.plan(SearchResult(source_id=source_id, title="x"))
        assert url_allowed(plan.files[0].url, ALLOWLIST), plan.files[0].url


def test_the_collection_is_configurable():
    importer, _ = make_importer(PROD_SCENEORG, config={"collection": "Scene"})
    plan = importer.plan(SearchResult(source_id="282136", title="x"))
    assert plan.collection == "Scene"


def test_importing_a_video_is_refused_before_any_url_is_built():
    importer, _ = make_importer(PROD_VIDEO)
    with pytest.raises(NotImportable) as exc:
        importer.plan(SearchResult(source_id="62892", title="?"))
    assert "YouTube" in str(exc.value)


def test_importing_a_production_only_csdb_hosts_is_refused_with_the_reason():
    importer, _ = make_importer(PROD_CSDB_ONLY)
    with pytest.raises(NoUsableDownload) as exc:
        importer.plan(SearchResult(source_id="341803", title="x"))
    assert "csdb.dk" in str(exc.value)


def test_a_non_numeric_source_id_is_refused():
    importer, http = make_importer(PROD_SCENEORG)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(SearchResult(source_id="../../etc/passwd", title="x"))
    assert "not a Demozoo production id" in str(exc.value)
    assert http.calls == []


def test_a_deleted_production_says_so_rather_than_reporting_a_fault():
    importer, _ = make_importer('{"detail":"Not found."}', status=404)
    with pytest.raises(ImportRefused) as exc:
        importer.plan(SearchResult(source_id="1", title="x"))
    assert "merged or deleted" in str(exc.value)


def test_a_200_that_is_not_json_is_refused():
    importer, _ = make_importer("<html>rate limited</html>")
    with pytest.raises(ImportRefused) as exc:
        importer.plan(SearchResult(source_id="1", title="x"))
    assert "not JSON" in str(exc.value)


# ------------------------------------------------------------- filenames


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "C:evil.zip",
        "sub/dir/x.d64",
        "sub\\dir\\x.d64",
        "NUL.zip",
        "COM1.prg",
        "trailing. ",
        "...",
        "",
        "__booze_design.zip",
        "2.8K Nuance!.zip",
        "x" * 500 + ".zip",
    ],
)
def test_sanitised_names_are_always_names_the_host_accepts(raw):
    name = safe_filename(raw)
    assert bare_filename(name) == name
    assert len(name) <= MAX_CHARS


def test_sanitising_preserves_the_extension_when_truncating():
    assert safe_filename("y" * 400 + ".d64").endswith(".d64")


def test_sanitising_is_deterministic():
    assert safe_filename("a/b/c.zip") == safe_filename("a/b/c.zip") == "c.zip"


def test_a_name_that_sanitises_to_nothing_gets_the_fallback():
    assert safe_filename("   ...   ") == FALLBACK


def test_a_name_with_no_extension_survives_intact():
    """The archives this plugin uses always have one, but Demozoo links to
    archives that do not (`Skid_Row-Cr3DConstKit`)."""
    assert safe_filename("Skid_Row-Cr3DConstKit") == "Skid_Row-Cr3DConstKit"


# -------------------------------------------------------------- manifest


def test_the_manifest_declares_search_and_importer_and_no_romm_scopes():
    manifest = parse_manifest(MANIFEST)
    assert set(manifest.capabilities) == {"search", "importer"}
    assert manifest.romm_api == []
