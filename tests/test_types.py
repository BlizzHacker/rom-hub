import pytest
from pydantic import ValidationError

from rom_hub.types import SearchResult


def test_minimal_result_gets_defaults():
    r = SearchResult(source_id="msdos_Oregon_Trail_The_1990", title="The Oregon Trail")
    assert r.source_id == "msdos_Oregon_Trail_The_1990"
    assert r.title == "The Oregon Trail"
    assert r.platform is None
    assert r.size_bytes is None
    assert r.url is None
    assert r.extra == {}
    assert r.plugin == ""


def test_empty_source_id_rejected():
    with pytest.raises(ValidationError):
        SearchResult(source_id="", title="The Oregon Trail")


def test_empty_title_rejected():
    with pytest.raises(ValidationError):
        SearchResult(source_id="abc", title="")


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        SearchResult(source_id="abc", title="x", size_bytes=-1)


def test_extra_survives_roundtrip():
    r = SearchResult(source_id="abc", title="x", extra={"stream_only": "true"})
    assert SearchResult(**r.model_dump()).extra["stream_only"] == "true"
