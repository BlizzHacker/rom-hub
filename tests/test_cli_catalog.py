"""`rom-hub catalog`, and what `browse` and `install` say once there is more
than one directory.

**Offline.** Local-path sources are real files in `tmp_path`; the one test
that exercises the https path replaces `catalog_sources._fetch_text`, which
is the seam directly above the socket. Nothing here resolves a hostname.

The failing-source tests use a local file that is deleted rather than a host
that is down, on purpose: it reaches the same degradation code by the same
route and cannot be flaky.
"""

import json
from pathlib import Path

import pytest

from rom_hub.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalogs"
THIRD_PARTY = FIXTURES / "third_party.json"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(root))
    return root


def test_catalog_list_shows_the_bundled_directory_on_a_fresh_install(home, capsys):
    assert main(["catalog", "list"]) == 0
    out = capsys.readouterr().out
    assert "bundled" in out
    assert "1 of 1 catalog(s) reachable" in out
    # The claim that carries the whole trust model belongs where somebody
    # managing directories will read it.
    assert "grants nothing" in out


def test_catalog_add_then_list_then_remove(home, capsys):
    assert main(["catalog", "add", "neighbour", str(THIRD_PARTY)]) == 0
    added = capsys.readouterr().out
    assert "neighbour" in added
    # Precedence is stated when it is decided, not discovered at a collision.
    assert "cannot replace" in added
    assert "2 plugin(s) listed" in added

    assert main(["catalog", "list"]) == 0
    listing = capsys.readouterr().out
    assert "neighbour" in listing
    assert "2 of 2 catalog(s) reachable" in listing
    # The collision the fixture creates is named in the listing rather than
    # left for the operator to discover at install time.
    assert "archive-org" in listing
    assert "collision" in listing

    assert main(["catalog", "remove", "neighbour"]) == 0
    assert "removed" in capsys.readouterr().out
    assert main(["catalog", "list"]) == 0
    assert "neighbour" not in capsys.readouterr().out


def test_catalog_add_refuses_http_and_says_why(home, capsys):
    assert main(["catalog", "add", "insecure", "http://example.test/c.json"]) == 1
    assert "rewritten in flight" in capsys.readouterr().err


def test_catalog_remove_refuses_the_bundled_directory(home, capsys):
    assert main(["catalog", "remove", "bundled"]) == 1
    assert "cannot be removed" in capsys.readouterr().err


def test_browse_says_which_directory_each_plugin_came_from(home, capsys):
    main(["catalog", "add", "neighbour", str(THIRD_PARTY)])
    capsys.readouterr()

    assert main(["plugin", "browse"]) == 0
    out = capsys.readouterr().out
    assert "SOURCE" in out
    assert "neighbourhood-roms" in out
    assert "third-party catalog 'neighbour'" in out
    assert "not vouched for by this project" in out
    assert "from a third-party catalog" in out


def test_browse_shows_the_bundled_entry_for_a_slug_two_directories_claim(
    home, capsys
):
    main(["catalog", "add", "impostor", str(THIRD_PARTY)])
    capsys.readouterr()

    assert main(["plugin", "browse"]) == 0
    captured = capsys.readouterr()
    # The bundled archive-org, not the fixture's impostor copy.
    assert "not-really-archive-org" not in captured.out
    assert "github.com/BlizzHacker/rom-hub-archive-org" in captured.out
    # And the collision is reported rather than silently resolved.
    assert "slug collision" in captured.err
    assert "archive-org" in captured.err


def test_browse_reports_an_unreachable_directory_rather_than_a_short_list(
    home, tmp_path, capsys
):
    """A partial directory must never be presented as a complete one.

    `search` already reports "N of M sources responded". A missing plugin
    is worse than a missing search result, because it reads as a plugin
    that does not exist rather than as a source that failed.
    """
    catalog = tmp_path / "gone.json"
    catalog.write_text(THIRD_PARTY.read_text(encoding="utf-8"), encoding="utf-8")
    main(["catalog", "add", "gone", str(catalog)])
    catalog.unlink()
    capsys.readouterr()

    assert main(["plugin", "browse"]) == 0
    captured = capsys.readouterr()
    assert "1 of 2 catalog(s) reachable" in captured.err
    assert "this listing is incomplete" in captured.err
    # The reachable directory still prints. Degradation, not collapse.
    assert "archive-org" in captured.out
    assert "neighbourhood-roms" not in captured.out


def test_catalog_list_reports_the_unreachable_one_and_exits_non_zero_on_refresh(
    home, tmp_path, capsys
):
    catalog = tmp_path / "gone.json"
    catalog.write_text(THIRD_PARTY.read_text(encoding="utf-8"), encoding="utf-8")
    main(["catalog", "add", "gone", str(catalog)])
    catalog.unlink()
    capsys.readouterr()

    assert main(["catalog", "list"]) == 0
    assert "1 of 2 catalog(s) reachable" in capsys.readouterr().out
    # `refresh` is the command somebody runs to find out whether the
    # directories are healthy, so its exit code has to answer that.
    assert main(["catalog", "refresh"]) == 1
    assert "unreachable" in capsys.readouterr().out


def test_platforms_reports_an_unreachable_directory_too(home, tmp_path, capsys):
    catalog = tmp_path / "gone.json"
    catalog.write_text(THIRD_PARTY.read_text(encoding="utf-8"), encoding="utf-8")
    main(["catalog", "add", "gone", str(catalog)])
    catalog.unlink()
    capsys.readouterr()

    assert main(["platforms"]) == 0
    assert "1 of 2 catalog(s) reachable" in capsys.readouterr().err


def test_installing_from_a_third_party_directory_says_so_before_it_installs(
    home, capsys, monkeypatch
):
    """The operator must know they are installing something unvouched for.

    A notice rather than a prompt: they added the source deliberately, in
    a separate command. What they cannot be expected to remember is which
    of a dozen slugs came from where.
    """
    main(["catalog", "add", "neighbour", str(THIRD_PARTY)])
    capsys.readouterr()

    # The install itself must not run: the fixture points at a host that
    # does not exist, and this test is about what is printed first.
    seen = {}

    def refuse(self, source, ref=None):
        seen["source"], seen["ref"] = source, ref
        raise SystemExit(0)

    monkeypatch.setattr("rom_hub.registry.Registry.install", refuse)
    with pytest.raises(SystemExit):
        main(["plugin", "install", "neighbourhood-roms"])

    out = capsys.readouterr().out
    assert "third-party catalog 'neighbour'" in out
    assert "does not vouch" in out
    assert "manifest.toml" in out
    # And the notice comes before the line that says what will be cloned.
    assert out.index("does not vouch") < out.index("resolved")
    assert seen["source"].startswith("https://git.example.test/")
    assert seen["ref"] == "v0.1.0"


def test_installing_a_bundled_slug_is_unchanged_and_carries_no_notice(
    home, capsys, monkeypatch
):
    main(["catalog", "add", "impostor", str(THIRD_PARTY)])
    capsys.readouterr()

    seen = {}

    def refuse(self, source, ref=None):
        seen["source"] = source
        raise SystemExit(0)

    monkeypatch.setattr("rom_hub.registry.Registry.install", refuse)
    with pytest.raises(SystemExit):
        main(["plugin", "install", "archive-org"])

    captured = capsys.readouterr()
    assert "does not vouch" not in captured.out
    assert "from the bundled catalog" in captured.out
    # The impostor entry claimed this slug and lost; the operator is told.
    assert "impostor" in captured.err
    assert seen["source"] == "https://github.com/BlizzHacker/rom-hub-archive-org"


def test_a_remote_directory_reaches_the_cli_without_a_socket(
    home, capsys, monkeypatch
):
    """The https path end to end, with the fetch replaced one layer down.

    `_fetch_text` is the seam immediately above `HttpDownloader`, so
    everything the CLI does with a remote directory -- adding it, caching
    it, merging it, labelling it third-party -- runs here for real.
    """
    fetched: list[str] = []

    def fake_fetch(source, root, *, transport=None):
        fetched.append(source.location)
        return THIRD_PARTY.read_text(encoding="utf-8")

    monkeypatch.setattr("rom_hub.catalog_sources._fetch_text", fake_fetch)

    url = "https://git.moveweight.com/wade/rom-hub-catalog/raw/branch/main/plugins.json"
    assert main(["catalog", "add", "mine", url]) == 0
    added = capsys.readouterr().out
    assert "https only" in added or "redirect hop" in added
    assert "2 plugin(s) listed" in added

    assert main(["plugin", "browse"]) == 0
    out = capsys.readouterr().out
    assert "neighbourhood-roms" in out
    assert "third-party catalog 'mine'" in out

    # Cached: `browse` did not refetch after `add` did.
    assert fetched == [url]
    cached = list((home / "var" / "catalogs").glob("*.json"))
    assert len(cached) == 2  # the body and its fetched-at stamp
    body = next(p for p in cached if not p.name.endswith(".meta.json"))
    assert json.loads(body.read_text(encoding="utf-8"))["catalog_version"] == "1"
