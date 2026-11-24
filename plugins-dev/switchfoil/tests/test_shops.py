import unittest

from fakes import (
    BASE,
    DLC,
    PASSWORD,
    PASSWORD_IN_URL,
    UPDATE,
    USER,
    FakeHTTP,
    blocked,
    sections,
    too_large,
)

from switchfoil.shops import SourceError, join_url, read_source
from switchfoil.sources import parse_source

ROOT = "https://shop.your-server.example"
REMOTE = ROOT + "/api/remote/sections"
SHOP = ROOT + "/api/shop/sections"


def source(protocol="", credentials=True):
    auth = f"{USER}:{PASSWORD_IN_URL}@" if credentials else ""
    return parse_source(f"Home|https://{auth}shop.your-server.example|{protocol}", 1)


class CyberFoilSectionsTests(unittest.TestCase):
    def test_remote_sections_response(self):
        http = FakeHTTP(
            {
                REMOTE: (
                    200,
                    {
                        "sections": [
                            {
                                "id": "base",
                                "items": [
                                    {"url": "/api/download/t1#" + BASE, "size": 1000},
                                    {
                                        "url": "/api/download/t2",
                                        "name": "Fixture Quest.nsz",
                                        "title_id": "0100F1C7A0B10800",
                                        "app_version": "131072",
                                        "app_type": "UPDATE",
                                        "size": "2000",
                                    },
                                ],
                            },
                            {"id": "dlc", "items": [{"url": "/files/Extra.nsp"}]},
                            {"id": "cheats", "items": [{"url": "/cheats/x.nsp"}]},
                        ]
                    },
                )
            }
        )
        entries, skipped = read_source(http, source())
        self.assertEqual(http.urls(), [REMOTE])
        self.assertEqual(skipped, 0)
        first, second, third = entries
        self.assertEqual(first.url, ROOT + "/api/download/t1#" + BASE)
        self.assertEqual(
            (first.title_id, first.version, first.kind, first.size),
            ("0100F1C7A0B10000", 0, "base", 1000),
        )
        self.assertEqual(
            (second.title_id, second.version, second.kind, second.size),
            ("0100F1C7A0B10800", 131072, "update", 2000),
        )
        self.assertEqual((third.kind, third.title_id, third.fmt), ("dlc", None, "nsp"))

    def test_shop_sections_after_remote_404(self):
        http = FakeHTTP({SHOP: (200, sections({"url": "/d/1#" + DLC}))})
        entries, _ = read_source(http, source())
        self.assertEqual(http.urls(), [REMOTE, SHOP])
        self.assertEqual(entries[0].kind, "dlc")

    def test_ownfoil_style_files_at_sections_path_fall_through_to_the_index(self):
        # Ownfoil answers every path with its flat Tinfoil index, as CyberFoil
        # finds it. CyberFoil reads the base URL after both probes, and so do we.
        index = {"success": "motd", "files": [{"url": "/api/download/t1#" + BASE, "size": 5}]}
        http = FakeHTTP({REMOTE: (200, index), SHOP: (200, index), ROOT: (200, index)})
        entries, _ = read_source(http, source())
        self.assertEqual(http.urls(), [REMOTE, SHOP, ROOT])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].url, ROOT + "/api/download/t1#" + BASE)

    def test_forced_cyberfoil_does_not_read_the_index(self):
        http = FakeHTTP({ROOT: (200, {"files": [BASE]})})
        with self.assertRaises(SourceError):
            read_source(http, source("cyberfoil"))
        self.assertEqual(http.urls(), [REMOTE, SHOP])

    def test_forced_tinfoil_skips_the_probes(self):
        http = FakeHTTP({ROOT: (200, {"files": ["/f/" + BASE]})})
        entries, _ = read_source(http, source("tinfoil"))
        self.assertEqual(http.urls(), [ROOT])
        self.assertEqual(entries[0].url, ROOT + "/f/" + BASE)


class TinfoilIndexTests(unittest.TestCase):
    def test_nested_directories(self):
        http = FakeHTTP(
            {
                ROOT: (
                    200,
                    {
                        "success": "welcome",
                        "files": [{"url": "base/" + BASE, "size": 10}, "readme.txt"],
                        "directories": ["/updates", {"url": "https://cdn.your-server.example/dlc"}],
                    },
                ),
                ROOT + "/updates": (
                    200,
                    {
                        "files": [{"url": "https://cdn.your-server.example/u/" + UPDATE}],
                        "directories": ["deeper"],
                    },
                ),
                ROOT + "/updates/deeper": (
                    200,
                    {"files": {"Named.nsz": {"url": "x/y", "size": 3}}},
                ),
                "https://cdn.your-server.example/dlc": (
                    200,
                    {"files": [DLC], "directories": [ROOT]},
                ),
            }
        )
        entries, skipped = read_source(http, source("tinfoil"))
        self.assertEqual(skipped, 1)
        by_name = {e.filename: e for e in entries}
        self.assertEqual(by_name[BASE].url, ROOT + "/base/" + BASE)
        self.assertEqual(by_name[UPDATE].kind, "update")
        self.assertEqual(by_name["Named.nsz"].url, ROOT + "/updates/deeper/x/y")
        self.assertEqual(by_name[DLC].url, "https://cdn.your-server.example/dlc/" + DLC)
        # The base URL listed again as a directory is not read twice.
        self.assertEqual(http.urls().count(ROOT), 1)

    def test_credentials_only_go_to_the_source_origin(self):
        http = FakeHTTP(
            {
                ROOT: (200, {"directories": ["https://cdn.your-server.example/more"]}),
                "https://cdn.your-server.example/more": (200, {"files": [BASE]}),
            }
        )
        read_source(http, source("tinfoil"))
        self.assertIn("Authorization", http.headers_for(ROOT)[0])
        self.assertNotIn(
            "Authorization", http.headers_for("https://cdn.your-server.example/more")[0]
        )
        for url, _ in http.calls:
            self.assertNotIn("@", url)

    def test_userinfo_in_a_shop_index_is_dropped(self):
        http = FakeHTTP(
            {ROOT: (200, {"files": ["https://other:pw@cdn.your-server.example/" + BASE]})}
        )
        entries, _ = read_source(http, source("tinfoil"))
        self.assertEqual(entries[0].url, "https://cdn.your-server.example/" + BASE)

    def test_unsupported_urls_are_skipped(self):
        http = FakeHTTP(
            {ROOT: (200, {"files": ["gdrive:abc#" + BASE, "jbod:x#" + BASE, "ftp://h/" + BASE]})}
        )
        entries, skipped = read_source(http, source("tinfoil"))
        self.assertEqual((entries, skipped), ([], 3))

    def test_directory_failure_fails_the_source(self):
        http = FakeHTTP({ROOT: (200, {"directories": ["/missing"]})})
        with self.assertRaisesRegex(SourceError, "HTTP 404"):
            read_source(http, source("tinfoil"))

    def test_join_url(self):
        self.assertEqual(join_url("https://h.example/a", "/b"), "https://h.example/a/b")
        self.assertEqual(join_url("https://h.example/a", "b"), "https://h.example/a/b")
        self.assertEqual(
            join_url("https://h.example/a", "HTTPS://x.example/b"), "HTTPS://x.example/b"
        )


class FailureTests(unittest.TestCase):
    def assert_clean(self, message):
        for secret in (USER, PASSWORD, PASSWORD_IN_URL):
            self.assertNotIn(secret, message)

    def test_401(self):
        http = FakeHTTP({REMOTE: (401, ""), SHOP: (401, ""), ROOT: (401, '{"error": "denied"}')})
        with self.assertRaises(SourceError) as caught:
            read_source(http, source())
        self.assertIn("HTTP 401", str(caught.exception))
        self.assertIn("credentials", str(caught.exception))
        self.assert_clean(str(caught.exception))

    def test_ownfoil_error_body_is_redacted(self):
        body = {"error": f'User "{USER}" does not have access to the shop.'}
        http = FakeHTTP({ROOT: (200, body)})
        with self.assertRaises(SourceError) as caught:
            read_source(http, source("tinfoil"))
        self.assertIn("does not have access", str(caught.exception))
        self.assert_clean(str(caught.exception))

    def test_oversized_index(self):
        http = FakeHTTP({REMOTE: too_large(REMOTE)})
        with self.assertRaisesRegex(SourceError, "4 MiB"):
            read_source(http, source())

    def test_policy_error(self):
        http = FakeHTTP({REMOTE: blocked()})
        with self.assertRaisesRegex(SourceError, "ROM_HUB_NETWORK_SWITCHFOIL"):
            read_source(http, source())

    def test_other_transport_errors_are_redacted(self):
        http = FakeHTTP({ROOT: RuntimeError(f"ConnectError: {USER}:{PASSWORD} refused")})
        with self.assertRaises(SourceError) as caught:
            read_source(http, source("tinfoil"))
        self.assert_clean(str(caught.exception))

    def test_binary_tinfoil_indexes_are_named(self):
        header = "TINFOIL{}" + "\x00" * 0x108
        for flag, label in (
            ("\x0d", "zstd-compressed"),
            ("\ufffd", "encrypted"),
            ("\x0e", "zlib-compressed"),
        ):
            with self.subTest(label=label):
                http = FakeHTTP({ROOT: (200, "\ufeff" + header.format(flag) + "payload")})
                with self.assertRaisesRegex(SourceError, f"{label} TINFOIL index"):
                    read_source(http, source("tinfoil"))

    def test_html_page(self):
        http = FakeHTTP({ROOT: (200, "<!doctype html><title>Shop</title>")})
        with self.assertRaisesRegex(SourceError, "did not return JSON"):
            read_source(http, source("tinfoil"))

    def test_redirect_is_reported(self):
        http = FakeHTTP({ROOT: (302, "")})
        with self.assertRaisesRegex(SourceError, "redirect"):
            read_source(http, source("tinfoil"))

    def test_directory_loop_and_fan_out_are_bounded(self):
        routes = {ROOT: (200, {"directories": [f"/d{i}" for i in range(40)]})}
        for i in range(40):
            routes[f"{ROOT}/d{i}"] = (200, {"files": []})
        with self.assertRaisesRegex(SourceError, "more than 32 directories"):
            read_source(FakeHTTP(routes), source("tinfoil"))
        deep = {ROOT: (200, {"directories": ["a"]})}
        url = ROOT
        for _ in range(10):
            url += "/a"
            deep[url] = (200, {"directories": ["a"]})
        with self.assertRaisesRegex(SourceError, "deeper than"):
            read_source(FakeHTTP(deep), source("tinfoil"))


if __name__ == "__main__":
    unittest.main()
