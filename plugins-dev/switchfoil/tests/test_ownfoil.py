"""Search, plan and download against a real Ownfoil server.

Skipped unless `SWITCHFOIL_OWNFOIL_URL` names a running Ownfoil, as
`http://IP:port`, whose library holds the files from
`tools/seed_ownfoil.py`. `SWITCHFOIL_OWNFOIL_USER` and
`SWITCHFOIL_OWNFOIL_PASSWORD` name a non-admin account with shop access.
The CI `ownfoil` job starts one in Docker.

As in `test_fixture_shop.py`, the plugin runs as a subprocess through
rom-hub's `PluginProcess`, with `ctx.http` going through the broker's
fetcher and network policy, and the plan is downloaded by rom-hub's
`HttpDownloader` under the same policy. Only the server differs.
"""

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import fakes  # noqa: F401 - puts the plugin on sys.path
from rom_hub.broker.fetcher import HttpxFetcher
from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.importer import HttpDownloader
from rom_hub.manifest import load_manifest
from rom_hub.netpolicy import network_policy

from switchfoil.sources import parse_source

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fixture_shop  # noqa: E402

URL = os.environ.get("SWITCHFOIL_OWNFOIL_URL", "").rstrip("/")
USER = os.environ.get("SWITCHFOIL_OWNFOIL_USER", "")
PASSWORD = os.environ.get("SWITCHFOIL_OWNFOIL_PASSWORD", "")
WAIT = float(os.environ.get("SWITCHFOIL_OWNFOIL_WAIT", "300"))
ENV = "ROM_HUB_NETWORK_SWITCHFOIL"
EXPECTED = {f.filename: f for f in fixture_shop.FILES}


def source_line(name, protocol="", password=None):
    secret = quote(PASSWORD if password is None else password, safe="")
    scheme, _, rest = URL.partition("://")
    return f"{name}|{scheme}://{quote(USER, safe='')}:{secret}@{rest}|{protocol}"


def listed_filenames() -> set[str]:
    """What Ownfoil lists to CyberFoil right now, read without the plugin."""
    source = parse_source(source_line("Probe"), 1)
    request = urllib.request.Request(URL + "/", headers=source.headers_for(URL + "/"))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return set()
    files = data.get("files") if isinstance(data, dict) else None
    names = set()
    for item in files or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            names.add(urllib.request.unquote(item["url"].partition("#")[2]))
    return names


@unittest.skipUnless(URL and USER and PASSWORD, "SWITCHFOIL_OWNFOIL_URL is not set")
class OwnfoilTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest(ROOT / "manifest.toml")
        deadline = time.monotonic() + WAIT
        seen: set[str] = set()
        while time.monotonic() < deadline:
            seen = listed_filenames()
            if set(EXPECTED) <= seen:
                return
            time.sleep(3)
        raise AssertionError(
            f"Ownfoil at {URL} did not list the seeded files within {WAIT:.0f}s; "
            f"it listed {sorted(seen)}"
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def plugin(self, sources, grants=None):
        fetcher = HttpxFetcher(timeout=30)
        self.addCleanup(fetcher.close)
        with mock.patch.dict(os.environ, {ENV: URL if grants is None else grants}):
            process = PluginProcess(
                ROOT,
                self.manifest,
                {"sources": sources, "titledb": False},
                fetcher,
                timeout=60,
                allow_unsandboxed=True,
            )
        process.start()
        self.addCleanup(process.close)
        return process

    def search(self, protocol=""):
        process = self.plugin([source_line("Ownfoil", protocol)])
        return process, process.search("fixture game", "switch", 50)

    def test_web_page_for_an_unknown_client(self):
        # Without the CyberFoil headers Ownfoil serves its browser interface,
        # which is why the plugin presents itself as CyberFoil.
        try:
            with urllib.request.urlopen(URL + "/", timeout=10) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
        self.assertNotIn('"files"', body)

    def test_auto_protocol_reads_the_flat_index(self):
        _, results = self.search()
        self.assertEqual({r.extra["filename"] for r in results}, set(EXPECTED))
        by_name = {r.extra["filename"]: r for r in results}
        update = by_name["Fixture Game [0100F1C700000800][v65536].nsp"]
        self.assertEqual((update.extra["type"], update.extra["version"]), ("update", "65536"))
        self.assertEqual(by_name["Fixture Game [0100F1C700000000][v0].nsz"].extra["format"], "nsz")
        for result in results:
            self.assertEqual(result.url, URL)
            self.assertEqual(result.size_bytes, 4096)
            self.assertNotIn(PASSWORD, result.model_dump_json())

    def test_tinfoil_protocol_matches_auto(self):
        _, auto = self.search()
        _, tinfoil = self.search("tinfoil")
        self.assertEqual(
            {r.extra["filename"] for r in auto}, {r.extra["filename"] for r in tinfoil}
        )

    def test_cyberfoil_protocol_fails_since_ownfoil_has_no_sections(self):
        process = self.plugin([source_line("Ownfoil", "cyberfoil")])
        with self.assertRaisesRegex(PluginCallError, "no sections"):
            process.search("fixture game", "switch", 50)

    def test_download_every_file_with_basic_auth(self):
        process, results = self.search()
        with mock.patch.dict(os.environ, {ENV: URL}):
            policy = network_policy(self.manifest)
        for result in results:
            with self.subTest(filename=result.extra["filename"]):
                (file,) = process.plan(result).files
                self.assertTrue(file.url.startswith(URL + "/api/download/"))
                self.assertIn("Authorization", file.headers)
                dest = Path(self.tmp.name) / file.filename
                with HttpDownloader(policy, timeout=30) as downloader:
                    downloader.download(file.url, dest, file.size_bytes, file.headers)
                self.assertEqual(dest.read_bytes(), EXPECTED[file.filename].body)

    def test_download_without_credentials_is_refused(self):
        process, results = self.search()
        (file,) = process.plan(results[0]).files
        request = urllib.request.Request(file.url, headers={"User-Agent": "cyberfoil"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 401)

    def test_wrong_password_is_reported_without_echoing_it(self):
        process = self.plugin([source_line("Ownfoil", password="wrong-pass")])
        with self.assertRaisesRegex(PluginCallError, "Shop requires authentication") as caught:
            process.search("fixture game", "switch", 50)
        self.assertNotIn("wrong-pass", str(caught.exception))

    def test_ungranted_shop_is_refused_before_any_request(self):
        process = self.plugin([source_line("Ownfoil")], grants="")
        with self.assertRaisesRegex(PluginCallError, ENV):
            process.search("fixture game", "switch", 50)


if __name__ == "__main__":
    unittest.main()
