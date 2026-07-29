"""MetadataPatch: what a plugin may propose, and what the host will send.

The single most destructive thing this capability could do is not a
security escape -- it is a faithful PUT of a partial patch. RomM's update
endpoint writes the record it is given, so forwarding an unset field as an
empty form part erases whatever the user had curated there. "Absent means
leave it alone" is therefore a tested invariant, not a convention.
"""

import base64
import json

import pytest
from pydantic import ValidationError

from rom_hub.types import (
    MAX_ARTWORK_BYTES,
    MAX_RAW_METADATA_CHARS,
    MetadataPatch,
    RomRef,
)


def test_an_unset_field_is_never_sent():
    """The failure that would silently destroy a curated library.

    A plugin that only knows the name must not blank out the igdb_id, the
    raw metadata, or anything else RomM already holds.
    """
    fields = MetadataPatch(name="Oregon Trail").form_fields()
    assert fields == {"name": "Oregon Trail"}
    # Explicit, because the whole point is what is *absent*.
    assert "igdb_id" not in fields
    assert "raw_igdb_metadata" not in fields
    assert not any(value == "" for value in fields.values())


def test_a_patch_that_sets_nothing_sends_nothing():
    patch = MetadataPatch()
    assert patch.form_fields() == {}
    assert patch.is_empty()


def test_provider_ids_and_raw_blobs_become_form_fields():
    patch = MetadataPatch(
        name="Doom",
        provider_ids={"igdb_id": 7, "moby_id": "1234"},
        raw_metadata={"raw_igdb_metadata": {"summary": "shooter"}},
    )
    fields = patch.form_fields()
    assert fields["name"] == "Doom"
    assert fields["igdb_id"] == "7"
    assert fields["moby_id"] == "1234"
    assert json.loads(fields["raw_igdb_metadata"]) == {"summary": "shooter"}


def test_an_unknown_provider_id_field_is_refused():
    """The request is built by iterating the plugin's own keys, so the key
    set is an allowlist like everything else here."""
    with pytest.raises(ValidationError, match="unknown provider id field"):
        MetadataPatch(provider_ids={"fs_path": "/etc/passwd"})


def test_an_unknown_raw_metadata_field_is_refused():
    with pytest.raises(ValidationError, match="unknown raw metadata field"):
        MetadataPatch(raw_metadata={"raw_evil_metadata": {}})


def test_a_boolean_provider_id_is_refused():
    """bool is an int in Python; `igdb_id=True` would post the string "True"."""
    with pytest.raises(ValidationError, match="not a bool"):
        MetadataPatch(provider_ids={"igdb_id": True})


def test_a_provider_id_cannot_carry_arbitrary_text():
    with pytest.raises(ValidationError, match="not permitted in an identifier"):
        MetadataPatch(provider_ids={"igdb_id": "7; DROP TABLE roms"})


def test_an_oversized_raw_blob_is_refused():
    with pytest.raises(ValidationError, match="over the"):
        MetadataPatch(
            raw_metadata={"raw_igdb_metadata": {"x": "y" * MAX_RAW_METADATA_CHARS}}
        )


def test_artwork_bytes_survive_the_round_trip():
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    patch = MetadataPatch(artwork_base64=png)
    assert patch.artwork_data() == png
    # And the same patch reconstructed from its wire form, which is what
    # the host actually does with a plugin's reply.
    assert MetadataPatch(**patch.model_dump()).artwork_data() == png


def test_artwork_cannot_be_supplied_twice():
    with pytest.raises(ValidationError, match="not\\s+both"):
        MetadataPatch(
            artwork_url="https://allowed.example/a.png", artwork_base64=b"data"
        )


def test_oversized_artwork_is_refused_before_it_reaches_memory_twice():
    oversized = base64.b64encode(b"\0" * (MAX_ARTWORK_BYTES + 1)).decode()
    with pytest.raises(ValidationError, match="over the"):
        MetadataPatch(artwork_base64=oversized)


def test_artwork_that_is_not_base64_is_refused():
    with pytest.raises(ValidationError, match="not valid base64"):
        MetadataPatch(artwork_base64="not base64 at all!!")


@pytest.mark.parametrize(
    "evil", ["../../cover.png", "C:cover.png", "/etc/passwd", "..", "NUL.png"]
)
def test_the_artwork_filename_is_validated_like_a_rom_filename(evil):
    """Same rule as FetchFile.filename, because the host writes both."""
    with pytest.raises(ValidationError):
        MetadataPatch(artwork_url="https://a.example/x.png", artwork_filename=evil)


def test_romref_needs_a_real_rom_id():
    with pytest.raises(ValidationError):
        RomRef(rom_id=0)
    assert RomRef(rom_id=12, name="Doom").rom_id == 12
