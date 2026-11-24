import unittest

import fakes  # noqa: F401 - puts the plugin on sys.path
from rom_hub.types import bare_filename

from switchfoil.names import (
    base_title_id,
    filename_from_url,
    kind_of_title_id,
    last_component,
    parse_filename,
    parse_title_id,
    safe_filename,
)


class FilenameTests(unittest.TestCase):
    def test_base_update_and_dlc(self):
        base = parse_filename("Fixture Quest [0100F1C7A0B10000][v0].nsp")
        self.assertEqual(
            (base.name, base.title_id, base.version, base.kind, base.fmt),
            ("Fixture Quest", "0100F1C7A0B10000", 0, "base", "nsp"),
        )
        update = parse_filename("Fixture Quest [0100F1C7A0B10800][v131072].NSZ")
        self.assertEqual((update.kind, update.version, update.fmt), ("update", 131072, "nsz"))
        dlc = parse_filename("Fixture Quest Extra [0100F1C7A0B11001][v0].xcz")
        self.assertEqual((dlc.kind, dlc.fmt), ("dlc", "xcz"))

    def test_tags_without_title_id(self):
        info = parse_filename("Fixture Quest [UPD][v65536].nsp")
        self.assertEqual((info.title_id, info.kind, info.version), (None, "update", 65536))
        info = parse_filename("Fixture Quest.xci")
        self.assertEqual(
            (info.name, info.title_id, info.kind, info.fmt), ("Fixture Quest", None, None, "xci")
        )

    def test_other_formats_are_not_switch_files(self):
        self.assertIsNone(parse_filename("readme.txt").fmt)
        self.assertIsNone(parse_filename("Fixture Quest [0100F1C7A0B10000].zip").fmt)

    def test_title_id_values(self):
        self.assertEqual(parse_title_id("0x0100F1C7A0B10000"), "0100F1C7A0B10000")
        self.assertEqual(parse_title_id(0x0100F1C7A0B10800), "0100F1C7A0B10800")
        self.assertIsNone(parse_title_id("not-an-id"))
        self.assertIsNone(parse_title_id(True))

    def test_kind_and_base_of_title_ids(self):
        self.assertEqual(kind_of_title_id("0100F1C7A0B10000"), "base")
        self.assertEqual(kind_of_title_id("0100F1C7A0B10800"), "update")
        self.assertEqual(kind_of_title_id("0100F1C7A0B11001"), "dlc")
        self.assertEqual(base_title_id("0100F1C7A0B10800"), "0100F1C7A0B10000")
        self.assertEqual(base_title_id("0100F1C7A0B11001"), "0100F1C7A0B10000")
        self.assertEqual(base_title_id("0100F1C7A0B1100A"), "0100F1C7A0B10000")
        self.assertEqual(base_title_id("0100F1C7A0B10000"), "0100F1C7A0B10000")

    def test_windows_paths_reduce_to_the_last_component(self):
        self.assertEqual(
            last_component("C:\\Games\\Switch\\Fixture Quest.nsp"), "Fixture Quest.nsp"
        )
        self.assertEqual(last_component("\\\\nas\\share\\Fixture Quest.nsp"), "Fixture Quest.nsp")
        self.assertEqual(last_component("games/switch/Fixture Quest.nsp"), "Fixture Quest.nsp")
        info = parse_filename("D:\\library\\Fixture Quest [0100F1C7A0B10000][v0].nsp")
        self.assertEqual(info.filename, "Fixture Quest [0100F1C7A0B10000][v0].nsp")

    def test_filename_from_url(self):
        self.assertEqual(
            filename_from_url("https://h.example/api/download/tok#Fixture%20Quest%20%5Bv0%5D.nsp"),
            "Fixture Quest [v0].nsp",
        )
        self.assertEqual(
            filename_from_url("https://h.example/files/Fixture%20Quest.nsz"), "Fixture Quest.nsz"
        )
        self.assertEqual(filename_from_url("https://h.example/x#..\\..\\evil.nsp"), "evil.nsp")

    def test_safe_filename_is_accepted_by_rom_hub(self):
        cases = {
            "Fixture: Quest™ [0100F1C7A0B10000][v0].nsp": (
                "Fixture - Quest_ [0100F1C7A0B10000][v0].nsp"
            ),
            "C:\\Games\\Fixture Quest.nsp": "Fixture Quest.nsp",
            "CON.nsp": "_CON.nsp",
            "Fixture Quest. .nsz": "Fixture Quest.nsz",
            "Fixture Quest*?<>|.xci": "Fixture Quest_____.xci",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(safe_filename(raw), expected)
                bare_filename(safe_filename(raw))
        long_name = "A" * 300 + ".nsp"
        self.assertEqual(len(safe_filename(long_name)), 200)
        self.assertTrue(safe_filename(long_name).endswith(".nsp"))
        bare_filename(safe_filename(long_name))


if __name__ == "__main__":
    unittest.main()
