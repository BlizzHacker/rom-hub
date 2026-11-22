"""The library's hashes reach metadata plugins."""
from rom_hub.metadata import rom_ref_from


def test_romm_hashes_are_passed_to_plugins():
    rom = {"name": "Chrono Trigger", "fs_name": "Chrono Trigger (USA) (En,Fr).nds",
           "platform_slug": "nds", "fs_size_bytes": 134217728,
           "md5_hash": "CB379392A8000000000000000000AAAA", "sha1_hash": "11f9e8204e480000000000000000000000000000",
           "crc_hash": "b3836946", "ra_hash": "465696f8a3c60000000000000000bbbb"}
    ref = rom_ref_from(rom, 38890)
    assert ref.extra == {"sha1": "11f9e8204e480000000000000000000000000000",
                         "md5": "cb379392a8000000000000000000aaaa", "crc": "b3836946",
                         "ra_hash": "465696f8a3c60000000000000000bbbb"}


def test_the_operator_wins_and_missing_hashes_are_left_out():
    ref = rom_ref_from({"name": "X", "md5_hash": "aa" * 16, "sha1_hash": None}, 1,
                       {"source_id": "md5:" + "bb" * 16, "md5": "cc" * 16})
    assert ref.extra == {"md5": "cc" * 16, "source_id": "md5:" + "bb" * 16}


def test_a_malformed_library_hash_is_not_forwarded():
    """RetroAchievements reads ra_hash before md5: a bad ra_hash must not hide a good md5."""
    ref = rom_ref_from({"name": "X", "ra_hash": "not-a-hash", "md5_hash": "AB" * 16,
                        "crc_hash": "123", "sha1_hash": "zz" * 20}, 1)
    assert ref.extra == {"md5": "ab" * 16}


def test_an_operator_source_id_suppresses_every_library_hash():
    """--source-id must still decide which item a rom is, as it did before hashes were passed."""
    ref = rom_ref_from({"name": "X", "ra_hash": "aa" * 16, "md5_hash": "aa" * 16}, 1,
                       {"source_id": "bb" * 16})
    assert ref.extra == {"source_id": "bb" * 16}
