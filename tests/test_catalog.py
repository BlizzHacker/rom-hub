"""The plugin catalog: a community-kept directory of known sources.

The catalog is a convenience for *finding* plugins. It is deliberately not a
source of authority about what a plugin may do — see
test_catalog_cannot_widen_permissions, which is the property that keeps a
compromised or malicious catalog from being a privilege-escalation route.
"""

import json
from pathlib import Path

import pytest

from rom_hub.catalog import CatalogError, CatalogEntry, load_catalog, render_markdown

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "plugins.json"


def test_shipped_catalog_parses():
    entries = load_catalog(CATALOG_PATH)
    assert entries, "the shipped catalog should not be empty"
    assert any(e.slug == "archive-org" for e in entries)


def test_the_catalog_lists_every_plugin_that_ships_in_this_repo():
    """A directory that omits half the plugins is a directory nobody trusts.

    Reads plugins-dev/ rather than a hardcoded list, so adding a plugin
    without cataloguing it fails here instead of shipping unlisted.
    """
    plugins_dev = Path(__file__).resolve().parents[1] / "plugins-dev"
    on_disk = {p.name for p in plugins_dev.iterdir() if (p / "manifest.toml").exists()}
    catalogued = {e.slug for e in load_catalog(CATALOG_PATH)}
    assert on_disk == catalogued, (
        f"in plugins-dev but not the catalog: {sorted(on_disk - catalogued)}; "
        f"in the catalog but not on disk: {sorted(catalogued - on_disk)}"
    )


def test_catalog_entries_agree_with_the_manifests_they_describe():
    """Slug, version and capabilities are facts, and the manifest owns them.

    The catalog is a copy for reading. A copy that drifts is how a reader
    ends up judging a plugin by capabilities it does not have.
    """
    import tomllib

    plugins_dev = Path(__file__).resolve().parents[1] / "plugins-dev"
    for entry in load_catalog(CATALOG_PATH):
        raw = tomllib.loads(
            (plugins_dev / entry.slug / "manifest.toml").read_text(encoding="utf-8")
        )
        assert raw["plugin"]["slug"] == entry.slug
        assert raw["plugin"]["name"] == entry.name
        assert str(raw["plugin"]["version"]) == entry.version
        assert sorted(raw["capabilities"]) == sorted(entry.capabilities)
        # The network list shown to a reader must be the one the broker will
        # actually enforce, or the directory is worse than silent.
        assert raw["permissions"]["network"] == entry.network


def entry_json(**overrides) -> str:
    """A minimal valid entry, so a test can vary the one field it is about.

    Built as a dict rather than a string literal: every new required field
    used to mean editing three separate JSON literals, and a test that fails
    because it is stale tells you nothing about the rule it was written for.
    """
    entry = {
        "slug": "x", "name": "X", "author": "a",
        "repository": "https://e/r", "install": "https://e/r",
        "download": "https://e/r/v1.tar.gz", "version": "1", "ref": "v1",
        "updated": "2026-01-01", "rpp_version": "1", "capabilities": ["search"],
        "network": [], "status": "ok", "description": "d", "terms": "t",
        "search_only": False, "key_required": False, "in_tree": False,
        "comments": "",
    }
    entry.update(overrides)
    return json.dumps({"catalog_version": "1", "plugins": [entry]})


def test_archive_org_entry_is_pinned_to_a_tag():
    entry = next(e for e in load_catalog(CATALOG_PATH) if e.slug == "archive-org")
    # The download must be pinned to a tag, never a moving branch: "latest"
    # is how a directory silently ships someone new code.
    assert entry.ref.startswith("v")
    assert entry.ref in entry.download
    assert entry.download.endswith(".tar.gz")


def test_in_tree_plugins_do_not_advertise_repo_urls_that_resolve():
    """These six have no public repos yet, so their URLs must not pretend to.

    A directory printing a URL that 404s is worse than one printing none: a
    reader cannot tell a dead link from a supply-chain swap. `.invalid` is
    reserved by RFC 2606 and is guaranteed never to resolve, so it reads as
    a placeholder and can never be registered by somebody else.
    """
    for entry in load_catalog(CATALOG_PATH):
        if not entry.in_tree:
            continue
        for url in (entry.repository, entry.install, entry.download):
            host = url.split("/")[2]
            assert host.endswith(".invalid"), (
                f"{entry.slug}: {url} looks like a real URL, but this plugin "
                f"ships in-tree and has no public repo"
            )


def test_entries_must_declare_a_known_status():
    with pytest.raises(CatalogError, match="status"):
        load_catalog_from_text(entry_json(status="vibes"))


def test_non_https_urls_rejected():
    with pytest.raises(CatalogError, match="https"):
        load_catalog_from_text(
            entry_json(repository="http://e/r", install="http://e/r",
                       download="http://e/r/v1.tar.gz")
        )


def test_the_reader_facing_prose_cannot_be_left_blank():
    """A blank cell reads as "nothing to declare", not "nobody filled it in".

    `terms` is the licensing position of the plugin's *source*. It is the
    field most likely to be skipped and the one a reader most needs.
    """
    for field in ("description", "terms"):
        with pytest.raises(CatalogError, match=field):
            load_catalog_from_text(entry_json(**{field: "   "}))


def test_the_flags_a_reader_needs_before_installing_must_be_booleans():
    for field in ("search_only", "key_required", "in_tree"):
        with pytest.raises(CatalogError, match=field):
            load_catalog_from_text(entry_json(**{field: "yes"}))


def test_search_only_and_key_required_are_surfaced_in_the_page():
    """Both have a failure mode that looks like a bug to whoever hits it.

    itch-io's importer always refuses; retroachievements returns nothing
    without a key. Saying so in the directory is cheaper than the bug report.
    """
    entries = load_catalog(CATALOG_PATH)
    itch = next(e for e in entries if e.slug == "itch-io")
    ra = next(e for e in entries if e.slug == "retroachievements")
    assert itch.search_only
    assert ra.key_required

    md = render_markdown(entries)
    # Rendered as the behaviour, not the category: itch-io implements
    # `metadata` and still cannot import anything, so "search-only" would
    # be wrong where "cannot import" is exactly right.
    assert "cannot import" in md
    assert "search-only" not in md
    # The clear-text storage is the part that must not be buried.
    assert "clear text" in md.lower()


def test_the_api_key_really_is_stored_in_clear_text():
    """The directory says so; this checks the host has not quietly improved.

    RPP v1 reserves a `secret` config type and this host rejects it, so
    `api_key` has to be a plain `str` on disk. If that ever changes, the
    catalog's wording becomes a lie and this test is where it surfaces.
    """
    from rom_hub.manifest import RESERVED_CONFIG_TYPES, ManifestError, parse_manifest

    assert "secret" in RESERVED_CONFIG_TYPES
    with pytest.raises(ManifestError, match="reserved in RPP v1"):
        parse_manifest(
            '[plugin]\nslug="x"\nname="X"\nversion="1"\nrpp_version="1"\n'
            '[capabilities]\nmetadata="x.m:M"\n'
            '[permissions]\nnetwork=[]\nromm_api=[]\n'
            '[config]\napi_key={type="secret",default=""}\n'
        )


def test_unsupported_catalog_version_rejected():
    with pytest.raises(CatalogError, match="catalog_version"):
        load_catalog_from_text('{"catalog_version":"2","plugins":[]}')


def test_catalog_cannot_widen_permissions():
    """The catalog's `network` list is advisory only.

    An installed plugin's real allowlist comes from its own manifest, read at
    install time. If the catalog could grant network access, then whoever hosts
    the catalog could silently widen every plugin's reach — which is exactly
    the authority a directory must not have.
    """
    from rom_hub.broker.host import PluginProcess
    import inspect

    source = inspect.getsource(PluginProcess._serve_plugin_call)
    assert "self.manifest.network" in source
    assert "catalog" not in source.lower()


def test_render_markdown_carries_every_column_a_reader_compares_on():
    md = render_markdown(load_catalog(CATALOG_PATH))
    for column in ("Source", "Author (Repository)", "Version", "Last update",
                   "Install", "Comments"):
        assert column in md
    assert "archive-org" in md or "Archive.org" in md


def load_catalog_from_text(text: str):
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(text)
        path = Path(fh.name)
    try:
        return load_catalog(path)
    finally:
        path.unlink(missing_ok=True)


def test_status_symbol_degrades_on_a_terminal_that_cannot_encode_it():
    """A Windows console is cp1252 and cannot encode these at all.

    Printing one raises UnicodeEncodeError and takes the command down, so the
    status column would break `browse` on the platform this was built on.
    """
    from rom_hub.catalog import symbol_for

    assert symbol_for("ok", "utf-8") == "✔"
    assert symbol_for("ok", "cp1252") == "ok"
    assert symbol_for("broken", "cp1252") == "x"
    # An unknown or absent encoding must degrade, not raise.
    assert symbol_for("caveat", None) == "!"
    assert symbol_for("caveat", "definitely-not-a-codec") == "!"


def test_directory_is_in_sync_with_the_catalog():
    """docs/PLUGINS.md is generated; a stale page contradicts what the CLI does.

    A published directory that disagrees with the catalog the CLI installs from
    is worse than no directory, so drift fails here rather than misleading
    somebody choosing what to trust.
    """
    from rom_hub.catalog import render_markdown

    page = (Path(__file__).resolve().parents[1] / "docs" / "PLUGINS.md").read_text(
        encoding="utf-8"
    )
    assert render_markdown(load_catalog(CATALOG_PATH)) in page, (
        "docs/PLUGINS.md is out of date -- run python scripts/render_directory.py"
    )
