"""The hand-written protobuf codec, checked against bytes Retrom really sent.

The encoders and decoders in `rom_hub.backends.retrom.proto` are not
generated, so a test that only round-trips them through each other would
prove they agree with themselves and nothing else. The decoding tests here
therefore start from **captured wire bytes** -- copied verbatim out of a
gRPC-Web response from a real Retrom 0.8.4 -- so a codec that drifts from
the actual encoding fails, not just one that is internally inconsistent.

No test here requires a live Retrom.
"""

from __future__ import annotations

import pytest

from rom_hub.backends.retrom import proto

# -- captured from Retrom 0.8.4 --------------------------------------------
#
# `retrom.PlatformService/GetPlatforms`, one platform. The two 12-byte
# fields at 3 and 4 are `google.protobuf.Timestamp` messages this codec
# has no reader for, which is exactly why they are kept: skipping a field
# it does not understand is the property under test.
GET_PLATFORMS_RESPONSE = bytes.fromhex(
    "0a38080212182f6170702f646174612f6c6962726172792f646f73626f78"
    "1a0c089090a9d30610b8ec9ea903220c089090a9d30610b8ec9ea903"
)

# `retrom.GameService/GetGames` with with_metadata and with_files set:
# one Game (field 1) and one GameFile (field 3), no metadata.
GET_GAMES_RESPONSE = bytes.fromhex(
    "0a4408011a222f6170702f646174612f6c6962726172792f646f73626f782f"
    "70726f62652e74787420022a0c089090a9d30610e0e3d3ae03320c089090a9"
    "d30610e0e3d3ae031a460801180c22222f6170702f646174612f6c69627261"
    "72792f646f73626f782f70726f62652e74787430013a0c089090a9d3061098"
    "d8acb103420c089090a9d3061098d8acb103"
)

# `retrom.LibraryService/UpdateLibrary`: one job id.
UPDATE_LIBRARY_RESPONSE = bytes.fromhex(
    "0a2435363762363638302d373561622d343864342d616631382d3334663061"
    "39306534613765"
)


# -- varints ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value, encoded",
    [
        (0, b"\x00"),
        (1, b"\x01"),
        (127, b"\x7f"),
        (128, b"\x80\x01"),
        (300, b"\xac\x02"),
        (1234, b"\xd2\x09"),
    ],
)
def test_varints_match_the_specified_encoding(value, encoded):
    assert proto.encode_varint(value) == encoded
    assert proto.decode(proto.varint_field(1, value)) == {1: [value]}


def test_a_negative_int_is_sign_extended_to_ten_bytes():
    """What protobuf does with a negative int32/int64, not an error."""
    assert len(proto.encode_varint(-1)) == 10


def test_an_oversized_varint_is_refused_rather_than_wrapped():
    with pytest.raises(proto.ProtoError):
        proto.decode(b"\x08" + b"\xff" * 11)


# -- decoding real responses ----------------------------------------------


def test_a_captured_platform_listing_decodes():
    fields = proto.decode(GET_PLATFORMS_RESPONSE)
    platforms = proto.as_messages(fields, 1)
    assert len(platforms) == 1
    assert proto.as_int(platforms[0], 1) == 2
    assert proto.as_str(platforms[0], 2) == "/app/data/library/dosbox"


def test_timestamps_the_codec_cannot_read_are_skipped_not_fatal():
    """A Retrom release that adds a field must not break an import."""
    platform = proto.as_messages(proto.decode(GET_PLATFORMS_RESPONSE), 1)[0]
    # created_at / updated_at are present on the wire...
    assert 3 in platform and 4 in platform
    # ...and reading past them still finds the fields that matter.
    assert proto.as_str(platform, 2) == "/app/data/library/dosbox"


def test_a_captured_game_listing_decodes_games_and_their_files():
    fields = proto.decode(GET_GAMES_RESPONSE)

    games = proto.as_messages(fields, 1)
    assert len(games) == 1
    assert proto.as_int(games[0], 1) == 1
    assert proto.as_str(games[0], 3) == "/app/data/library/dosbox/probe.txt"
    assert proto.as_int(games[0], 4) == 2

    files = proto.as_messages(fields, 3)
    assert len(files) == 1
    assert proto.as_int(files[0], 3) == 12  # byte_size
    assert proto.as_int(files[0], 6) == 1  # game_id


def test_a_captured_job_id_list_decodes_as_repeated_string():
    fields = proto.decode(UPDATE_LIBRARY_RESPONSE)
    assert proto.as_strs(fields, 1) == ["567b6680-75ab-48d4-af18-34f0a90e4a7e"]


# -- encoding --------------------------------------------------------------


def test_repeated_scalars_encode_packed_and_an_empty_list_encodes_to_nothing():
    assert proto.packed_varints(1, [1, 2, 300]) == b"\x0a\x04\x01\x02\xac\x02"
    # proto3 cannot distinguish empty from absent, and Retrom reads an
    # empty `ids` as "no filter".
    assert proto.packed_varints(1, []) == b""


def test_packed_and_unpacked_repeated_scalars_both_decode():
    """Conformant decoders must accept either encoding, so this one does."""
    packed = proto.packed_varints(1, [7, 8])
    unpacked = proto.varint_field(1, 7) + proto.varint_field(1, 8)
    assert proto.as_packed_ints(proto.decode(packed), 1) == [7, 8]
    assert proto.as_packed_ints(proto.decode(unpacked), 1) == [7, 8]


def test_strings_and_nested_messages_round_trip():
    inner = proto.varint_field(1, 42) + proto.string_field(2, "Doom")
    outer = proto.bytes_field(1, inner) + proto.bool_field(2, True)
    fields = proto.decode(outer)
    assert proto.as_bool(fields, 2) is True
    nested = proto.as_message(fields, 1)
    assert proto.as_int(nested, 1) == 42
    assert proto.as_str(nested, 2) == "Doom"


def test_repeated_strings_keep_their_order():
    payload = b"".join(proto.string_field(13, url) for url in ("a", "b", "c"))
    assert proto.as_strs(proto.decode(payload), 13) == ["a", "b", "c"]


# -- malformed input -------------------------------------------------------


def test_a_length_that_runs_past_the_end_is_an_error_not_a_short_read():
    with pytest.raises(proto.ProtoError):
        proto.decode(b"\x0a\x10ab")


def test_field_number_zero_is_refused():
    with pytest.raises(proto.ProtoError):
        proto.decode(b"\x00\x01")


def test_an_unknown_wire_type_stops_the_walk_rather_than_guessing():
    """Groups were removed in proto3 and their length is not derivable."""
    with pytest.raises(proto.ProtoError):
        proto.decode(b"\x0b\x01")


def test_an_undecodable_nested_message_is_dropped_not_raised():
    """One malformed row in a listing should cost that row, not the import."""
    payload = proto.bytes_field(1, b"\x0a\xff") + proto.bytes_field(
        1, proto.varint_field(1, 5)
    )
    messages = proto.as_messages(proto.decode(payload), 1)
    assert len(messages) == 1
    assert proto.as_int(messages[0], 1) == 5


# -- readers ---------------------------------------------------------------


def test_a_missing_field_is_the_default_not_an_error():
    fields = proto.decode(b"")
    assert proto.as_int(fields, 1) is None
    assert proto.as_str(fields, 2) is None
    assert proto.as_bool(fields, 3) is False
    assert proto.as_strs(fields, 4) == []
    assert proto.as_messages(fields, 5) == []
    assert proto.as_message(fields, 5) is None


def test_a_field_of_the_wrong_shape_reads_as_missing_not_as_garbage():
    """A string read out of a varint field would be nonsense either way;
    reporting it as absent keeps one wrong field from failing a listing."""
    fields = proto.decode(proto.varint_field(2, 9))
    assert proto.as_str(fields, 2) is None
    assert proto.as_int(fields, 2) == 9


def test_invalid_utf8_reads_as_missing_rather_than_raising():
    fields = proto.decode(proto.bytes_field(2, b"\xff\xfe"))
    assert proto.as_str(fields, 2) is None
