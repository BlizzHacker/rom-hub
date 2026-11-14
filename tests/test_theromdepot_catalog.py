"""Catalogue robustness and non-game resource regression cases."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins-dev/theromdepot"))
from theromdepot import catalog


def row(path="Nintendo 64/Game.z64", size=1):
    """Construct a minimal normalized game without any downloaded content."""
    return {"id": path, "name": path.rsplit("/", 1)[-1], "size": size, "platform": "n64"}


def test_invalid_unicode_size_does_not_remove_valid_games():
    """Unicode digit predicates must not allow one bad size to abort parsing."""
    assert len(catalog.entries_from({"items": [row(size="²"), row("Nintendo 64/Good.z64")]})) == 1


def test_directory_items_descend_to_files():
    """A directory path is not evidence that the node is a normalized file."""
    payload = {"items": [{"type": "directory", "name": "Nintendo 64", "path": "Nintendo 64", "children": [{"type": "file", "name": "Good.z64", "size": 1}]}]}
    assert len(catalog.entries_from(payload)) == 1


def test_normalized_export_enforces_unique_entry_bound(monkeypatch):
    """Both export formats enforce the same memory/collection bound."""
    monkeypatch.setattr(catalog, "MAX_ENTRIES", 1)
    with pytest.raises(catalog.CatalogUnavailable):
        catalog.entries_from({"items": [row(), row("Nintendo 64/Second.z64")]})
    assert len(catalog.entries_from({"items": [row(), row()]})) == 1


def test_overdepth_subtree_does_not_hide_valid_siblings(monkeypatch):
    """Skip malformed deep branches while preserving usable sibling entries."""
    monkeypatch.setattr(catalog, "MAX_TREE_DEPTH", 8)
    deep = row()
    for _ in range(20): deep = {"children": [deep]}
    good = {"type": "file", "path": "Nintendo 64/Good.z64", "name": "Good.z64", "size": 1}
    assert len(catalog.entries_from({"children": [deep, good]})) == 1


def test_manuals_bios_and_documents_do_not_become_games():
    """Live catalogue PDFs and resource ZIPs must not be acquisition results."""
    items = [row("Nintendo 64/Manual.pdf"), row("[Game Manuals]/Nintendo 64/Manual.zip"), row("[BIOS]/Nintendo 64/firmware.zip"), row()]
    entries = catalog.entries_from({"items": items})
    assert [x.path for x in entries] == ["Nintendo 64/Game.z64"]
