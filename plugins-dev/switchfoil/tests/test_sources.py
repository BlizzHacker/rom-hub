import base64
import hashlib
import unittest

from fakes import PASSWORD, PASSWORD_IN_URL, USER

from switchfoil.sources import SourceLineError, parse_source, parse_sources


class SourceLineTests(unittest.TestCase):
    def test_minimal_line(self):
        source = parse_source("Home|https://shop.your-server.example/", 1)
        self.assertEqual(source.name, "Home")
        self.assertEqual(source.url, "https://shop.your-server.example")
        self.assertEqual(source.protocol, "auto")
        self.assertEqual(source.hauth, "")
        self.assertIsNone(source.authorization())

    def test_credentials_are_split_off_and_decoded(self):
        line = f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example:8443/shop/"
        source = parse_source(line, 1)
        self.assertEqual(source.url, "https://shop.your-server.example:8443/shop")
        self.assertEqual((source.username, source.password), (USER, PASSWORD))
        token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        self.assertEqual(source.authorization(), f"Basic {token}")
        self.assertNotIn(PASSWORD, repr(source))

    def test_protocol_and_hauth_fields(self):
        source = parse_source("Home|https://shop.your-server.example|CyberFoil|ABC123", 1)
        self.assertEqual((source.protocol, source.hauth), ("cyberfoil", "ABC123"))
        source = parse_source("Home|https://shop.your-server.example||ABC123", 1)
        self.assertEqual((source.protocol, source.hauth), ("auto", "ABC123"))

    def test_lan_http_and_ipv6(self):
        source = parse_source("LAN|http://192.168.1.12:8465", 1)
        self.assertEqual(source.url, "http://192.168.1.12:8465")
        source = parse_source(f"V6|http://{USER}:x@[fd00::12]:8465/", 1)
        self.assertEqual(source.url, "http://[fd00::12]:8465")

    def test_bad_lines_never_echo_credentials(self):
        bad = [
            "only-a-name",
            f"|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example",
            f"Home|ftp://{USER}:{PASSWORD_IN_URL}@shop.your-server.example",
            f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example|gopher",
            f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example:99999",
            "a|b|c|d|e",
        ]
        for line in bad:
            with self.subTest(line=line):
                with self.assertRaises(SourceLineError) as caught:
                    parse_source(line, 3)
                message = str(caught.exception)
                for secret in (USER, PASSWORD, PASSWORD_IN_URL):
                    self.assertNotIn(secret, message)

    def test_parse_sources_skips_comments_blank_and_duplicates(self):
        sources, errors = parse_sources(
            [
                "# a comment",
                "",
                "A|https://a.your-server.example",
                "a|https://b.your-server.example",
                "broken",
                "B|https://b.your-server.example",
            ]
        )
        self.assertEqual([s.name for s in sources], ["A", "B"])
        self.assertEqual(len(errors), 2)
        self.assertIn("named twice", errors[0])

    def test_uid_is_uppercase_sha256_of_name_and_clean_url(self):
        source = parse_source(f"Home|https://{USER}:x@shop.your-server.example", 1)
        expected = hashlib.sha256(b"Homehttps://shop.your-server.example").hexdigest().upper()
        self.assertEqual(source.uid, expected)
        self.assertRegex(source.uid, r"^[0-9A-F]{64}$")
        again = parse_source("Home|https://shop.your-server.example/", 1)
        self.assertEqual(again.uid, source.uid)

    def test_identity_headers(self):
        source = parse_source(
            f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example||H1", 1
        )
        headers = source.headers_for("https://shop.your-server.example/api/remote/sections")
        self.assertEqual(headers["User-Agent"], "cyberfoil")
        self.assertEqual(headers["Theme"], "0" * 64)
        self.assertEqual(headers["Version"], "1.5")
        self.assertEqual(headers["Revision"], "1")
        self.assertEqual(headers["Language"], "en")
        self.assertEqual(headers["Hauth"], "H1")
        self.assertEqual(headers["Uauth"], "0")
        self.assertEqual(headers["Uid"], source.uid)
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_credentials_and_hauth_stay_on_their_origin(self):
        source = parse_source(
            f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example||H1", 1
        )
        for other in (
            "https://cdn.your-server.example/file.nsp",
            "http://shop.your-server.example/file.nsp",
            "https://shop.your-server.example:8443/file.nsp",
        ):
            with self.subTest(url=other):
                headers = source.headers_for(other)
                self.assertNotIn("Authorization", headers)
                self.assertEqual(headers["Hauth"], "0")

    def test_redact(self):
        source = parse_source(f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example", 1)
        text = f"user {USER} with {PASSWORD} sent {source.authorization()}"
        redacted = source.redact(text)
        for secret in (USER, PASSWORD, source.authorization()):
            self.assertNotIn(secret, redacted)


if __name__ == "__main__":
    unittest.main()
