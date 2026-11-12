"""Host authentication routing through CLI entry points."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock


from rom_hub import cli
from rom_hub.dispatcher import search_all


def plugin(slug):
    return SimpleNamespace(slug=slug, enabled=True,
        manifest=SimpleNamespace(capabilities=["search"], network=["archive.org"]))


def test_cached_search_does_not_construct_authenticated_client(monkeypatch):
    anonymous = object()
    factory = Mock(side_effect=AssertionError("network client opened"))
    monkeypatch.setattr(cli, "_plugin_fetcher", factory)
    with cli._search_fetchers(anonymous) as fetcher_for:
        outcome = search_all([plugin("archive-org")], anonymous, "game",
            catalogue_for=lambda *args: [], fetcher_for=fetcher_for)
    assert outcome.complete and outcome.from_catalogue == ["archive-org"]
    factory.assert_not_called()


def test_live_search_isolates_authentication_failure(monkeypatch):
    anonymous = object()
    def factory(p):
        raise cli.ArchiveAuthError("invalid credentials")
    monkeypatch.setattr(cli, "_plugin_fetcher", factory)
    process = MagicMock()
    process.return_value.__enter__.return_value.search.return_value = []
    with cli._search_fetchers(anonymous) as fetcher_for:
        outcome = search_all([plugin("archive-org"), plugin("other")], anonymous,
            "game", process_factory=process, fetcher_for=fetcher_for)
    assert outcome.responded == 1
    assert outcome.statuses[0].error == "ArchiveAuthError: invalid credentials"
    assert process.call_args.args[1] is anonymous


def test_fetcher_and_download_share_auth_but_only_for_archive_org(monkeypatch):
    transport = object()
    monkeypatch.setattr(cli, "archive_transport",
        lambda slug, root: transport if slug == "archive-org" else None)
    fetcher = Mock()
    downloader = Mock()
    monkeypatch.setattr(cli, "HttpxFetcher", fetcher)
    monkeypatch.setattr(cli, "HttpDownloader", downloader)
    cli._plugin_fetcher(plugin("archive-org"))
    cli._auth_downloader(plugin("archive-org"))
    assert fetcher.call_args.kwargs["transport"] is transport
    assert downloader.call_args.kwargs["transport"] is transport
    assert downloader.call_args.kwargs["allowlist"] == ["archive.org"]
    cli._plugin_fetcher(plugin("other"))
    assert fetcher.call_args.kwargs == {}
    with cli._auth_downloader(plugin("other")) as value:
        assert value is None


def test_cli_census_search_and_import_authenticate_host_requests(tmp_path, monkeypatch):
    import httpx
    from rom_hub.ia_auth import configure
    from rom_hub.ia_session import ArchiveOrgSession, ArchiveOrgTransport
    from rom_hub.types import CensusPage, CensusRecord, CensusUnit, FetchFile, FetchPlan, SearchResult
    from rom_hub.jobs import JobState

    for prefix in ("ROM_HUB_ARCHIVE_ORG_", "ROMARR_ARCHIVE_ORG_"):
        for suffix in ("EMAIL", "PASSWORD", "EMAIL_FILE", "PASSWORD_FILE"):
            monkeypatch.delenv(prefix + suffix, raising=False)
    monkeypatch.setenv("ROM_HUB_HOME", str(tmp_path))
    monkeypatch.setenv("ROM_HUB_SECRET_STORE", "file")
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "test-only-secret-key")
    assert configure(tmp_path, "account@example.com", "secret-password")["ok"]
    seen = []
    def login(request):
        return httpx.Response(200, json={"success": True, "values": {"cookies": {
            "logged-in-user": "account", "logged-in-sig": "session"}}})
    manager = ArchiveOrgSession(tmp_path, login_transport=httpx.MockTransport(login))
    def remote(request):
        assert request.headers["cookie"] == "logged-in-user=account; logged-in-sig=session"
        seen.append(request.url.path)
        return httpx.Response(200, content=b"ROM")
    monkeypatch.setattr(cli, "archive_transport", lambda slug, root:
        ArchiveOrgTransport(manager, httpx.MockTransport(remote)) if slug == "archive-org" else None)
    source = plugin("archive-org")
    source.manifest.capabilities = ["search", "census", "importer"]
    source.path, source.config = tmp_path, {}
    registry = Mock()
    registry.get.return_value = source
    registry.installed.return_value = [source]
    monkeypatch.setattr(cli, "Registry", lambda root: registry)
    monkeypatch.setattr(cli, "prepare_assets", lambda *args: None)
    monkeypatch.setattr(cli, "prepare_secrets", lambda *args: {})

    class Process:
        manifest = source.manifest
        def __init__(self, **kwargs):
            self.fetcher = kwargs["fetcher"]
        def start(self):
            pass
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def census_scope(self):
            assert self.fetcher.get("https://archive.org/metadata/set", {})[0] == 200
            return [CensusUnit(unit_id="set", label="Set", kind="roms", declared_total=1)]
        def census_page(self, unit, cursor):
            assert self.fetcher.get("https://archive.org/download/set/rom.zip/", {})[0] == 200
            return CensusPage(records=[CensusRecord(record_id="zip:set/rom.zip/game.chd",
                title="Game", platform="psx")], declared_total=1)
        def search(self, query, platform, limit):
            self.fetcher.get("https://archive.org/metadata/live", {})
            return [SearchResult(source_id="zip:set/rom.zip/game.chd", title="Game", platform="psx")]
        def plan(self, result):
            assert result.source_id == "zip:set/rom.zip/game.chd"
            self.fetcher.get("https://archive.org/metadata/plan", {})
            return FetchPlan(platform="psx", files=[FetchFile(
                url="https://archive.org/download/set/rom.zip/game.chd", filename="game.chd")])

    monkeypatch.setattr(cli, "PluginProcess", Process)
    monkeypatch.setattr("rom_hub.dispatcher._default_factory", lambda p, fetcher, timeout, **kw:
        Process(fetcher=fetcher))
    assert cli.main(["census", "build", "archive-org"]) == 0
    assert cli.main(["search", "Game", "--platform", "psx"]) == 0
    assert "/metadata/live" not in seen
    assert cli.main(["search", "Game", "--platform", "psx", "--live"]) == 0
    backend = Mock()
    monkeypatch.setattr(cli, "open_backend", lambda: backend)
    monkeypatch.setattr(cli, "require", lambda *args, **kwargs: None)
    def importer(proc, result, downloader, **kwargs):
        dest = tmp_path / "game.chd"
        downloader.download(proc.plan(result).files[0].url, dest)
        assert dest.read_bytes() == b"ROM"
        return SimpleNamespace(job_id=1, state=JobState.DONE, message="imported", warnings=[])
    monkeypatch.setattr(cli, "run_import", importer)
    assert cli.main(["import", "archive-org", "zip:set/rom.zip/game.chd", "--platform", "psx"]) == 0
    assert seen == ["/metadata/set", "/download/set/rom.zip/", "/metadata/live",
        "/metadata/plan", "/download/set/rom.zip/game.chd"]
