"""The plugin catalog: a directory of known sources, in the qBittorrent mould.

The catalog is a convenience for *finding* plugins. It is deliberately not a
source of authority about what a plugin may do — see
test_catalog_cannot_widen_permissions, which is the property that keeps a
compromised or malicious catalog from being a privilege-escalation route.
"""

import json
from pathlib import Path

import pytest

from romm_hub.catalog import CatalogError, CatalogEntry, load_catalog, render_markdown

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "plugins.json"


def test_shipped_catalog_parses():
    entries = load_catalog(CATALOG_PATH)
    assert entries, "the shipped catalog should not be empty"
    assert any(e.slug == "archive-org" for e in entries)


def test_archive_org_entry_points_at_a_real_repo_and_a_pinned_download():
    entry = next(e for e in load_catalog(CATALOG_PATH) if e.slug == "archive-org")
    assert entry.repository.startswith("https://github.com/")
    # The download must be pinned to a tag, never a moving branch: "latest"
    # is how a directory silently ships someone new code.
    assert entry.ref.startswith("v")
    assert entry.ref in entry.download
    assert entry.download.endswith(".tar.gz")


def test_entries_must_declare_a_known_status():
    with pytest.raises(CatalogError, match="status"):
        load_catalog_from_text('{"catalog_version":"1","plugins":[{"slug":"x",'
                               '"name":"X","author":"a","repository":"https://e/r",'
                               '"install":"https://e/r","download":"https://e/r/v1.tar.gz",'
                               '"version":"1","ref":"v1","updated":"2026-01-01",'
                               '"rpp_version":"1","capabilities":["search"],'
                               '"network":[],"status":"vibes","comments":""}]}')


def test_non_https_urls_rejected():
    with pytest.raises(CatalogError, match="https"):
        load_catalog_from_text('{"catalog_version":"1","plugins":[{"slug":"x",'
                               '"name":"X","author":"a","repository":"http://e/r",'
                               '"install":"http://e/r","download":"http://e/r/v1.tar.gz",'
                               '"version":"1","ref":"v1","updated":"2026-01-01",'
                               '"rpp_version":"1","capabilities":["search"],'
                               '"network":[],"status":"ok","comments":""}]}')


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
    from romm_hub.broker.host import PluginProcess
    import inspect

    source = inspect.getsource(PluginProcess._serve_plugin_call)
    assert "self.manifest.network" in source
    assert "catalog" not in source.lower()


def test_render_markdown_has_the_qbittorrent_columns():
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
    from romm_hub.catalog import symbol_for

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
    from romm_hub.catalog import render_markdown

    page = (Path(__file__).resolve().parents[1] / "docs" / "PLUGINS.md").read_text(
        encoding="utf-8"
    )
    assert render_markdown(load_catalog(CATALOG_PATH)) in page, (
        "docs/PLUGINS.md is out of date -- run python scripts/render_directory.py"
    )
