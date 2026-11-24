"""Search, plan and download against the fixture shop on loopback.

Everything but the shop is rom-hub's own code at the pinned revision. The
plugin runs as a subprocess through `rom_hub.broker.host.PluginProcess`,
its `ctx.http` calls go through the broker's `HttpxFetcher` and the
`NetworkPolicy` built from `ROM_HUB_NETWORK_SWITCHFOIL`, and the plan is
downloaded by `rom_hub.importer.HttpDownloader` with that same policy.
Where seccomp is unavailable, as on macOS, the subprocess runs unconfined
with `allow_unsandboxed=True`. The network calls are the same either way.
"""

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fakes  # noqa: F401 - puts the plugin on sys.path
from rom_hub.broker.fetcher import HttpxFetcher
from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.importer import DownloadError, HttpDownloader
from rom_hub.manifest import load_manifest
from rom_hub.netpolicy import network_policy

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fixture_shop  # noqa: E402

USER = "fixture-user"
PASSWORD = "fixture pass!"
ENV = "ROM_HUB_NETWORK_SWITCHFOIL"


class FixtureShopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest(ROOT / "manifest.toml")
        cls.shop, cls.mirror = fixture_shop.start(user=USER, password=PASSWORD)
        cls.both = f"{cls.shop.origin},{cls.mirror.origin}"

    @classmethod
    def tearDownClass(cls):
        fixture_shop.stop(cls.shop, cls.mirror)

    def setUp(self):
        self.shop.requests.clear()
        self.mirror.requests.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def sources(self, password=PASSWORD):
        port = self.shop.server_port
        auth = f"{USER}:{password.replace(' ', '%20').replace('!', '%21')}@"
        return [
            f"FixtureCF|http://{auth}127.0.0.1:{port}",
            f"FixtureTF|http://{auth}127.0.0.1:{port}|tinfoil",
        ]

    def plugin(self, grants, sources=None):
        fetcher = HttpxFetcher(timeout=10)
        self.addCleanup(fetcher.close)
        with mock.patch.dict(os.environ, {ENV: grants}):
            process = PluginProcess(
                ROOT,
                self.manifest,
                {"sources": sources or self.sources(), "titledb": False},
                fetcher,
                timeout=30,
                allow_unsandboxed=True,
            )
        process.start()
        self.addCleanup(process.close)
        return process

    def download(self, grants, file):
        with mock.patch.dict(os.environ, {ENV: grants}):
            policy = network_policy(self.manifest)
        dest = Path(self.tmp.name) / file.filename
        with HttpDownloader(policy, timeout=10) as downloader:
            downloader.download(file.url, dest, file.size_bytes, file.headers)
        return dest.read_bytes()

    def search_and_plan(self, process, token):
        results = process.search("fixture game", "switch", 50)
        wanted = fixture_shop.BY_TOKEN[token]
        hit = next(
            r
            for r in results
            if r.extra["source"] == "FixtureCF" and r.extra["filename"] == wanted.filename
        )
        return results, process.plan(hit).files[0]

    def test_search_covers_sections_and_nested_tinfoil_index(self):
        process = self.plugin(self.both)
        results = process.search("fixture game", "switch", 50)
        by_source = {}
        for result in results:
            by_source.setdefault(result.extra["source"], set()).add(result.extra["filename"])
        expected = {f.filename for f in fixture_shop.FILES}
        self.assertEqual(by_source, {"FixtureCF": expected, "FixtureTF": expected})
        paths = [path for path, _ in self.shop.requests]
        self.assertIn("/api/remote/sections", paths)
        self.assertIn("/more", paths)
        for result in results:
            self.assertNotIn(USER, result.model_dump_json())
            self.assertEqual(result.url, self.shop.origin)
        update = next(r for r in results if r.extra.get("type") == "update")
        self.assertIn("[0100F1C700000800][v65536] UPD NSP", update.title)

    def test_download_with_basic_auth(self):
        process = self.plugin(self.both)
        _, file = self.search_and_plan(process, "base")
        self.assertEqual(file.url, f"{self.shop.origin}/api/download/base")
        token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        self.assertEqual(file.headers["Authorization"], f"Basic {token}")
        self.shop.requests.clear()
        body = self.download(self.both, file)
        self.assertEqual(body, fixture_shop.BY_TOKEN["base"].body)
        (path, headers), *_ = self.shop.requests
        self.assertEqual(path, "/api/download/base")
        self.assertEqual(headers["Authorization"], f"Basic {token}")
        self.assertEqual(headers["User-Agent"], "cyberfoil")

    def test_redirect_to_a_granted_second_origin(self):
        process = self.plugin(self.both)
        _, file = self.search_and_plan(process, "mirror")
        body = self.download(self.both, file)
        self.assertEqual(body, fixture_shop.BY_TOKEN["mirror"].body)
        self.assertEqual([p for p, _ in self.mirror.requests], ["/files/mirror"])
        # The credential stayed on the shop's origin.
        self.assertNotIn("Authorization", self.mirror.requests[0][1])

    def test_redirect_to_an_ungranted_second_origin_is_refused(self):
        process = self.plugin(self.shop.origin)
        _, file = self.search_and_plan(process, "mirror")
        with self.assertRaisesRegex(DownloadError, "outside the plugin's allowlist"):
            self.download(self.shop.origin, file)
        self.assertEqual(self.mirror.requests, [])
        self.assertIn("/api/download/mirror", [p for p, _ in self.shop.requests])

    def test_ungranted_shop_fails_with_the_variable_named(self):
        process = self.plugin("")
        with self.assertRaisesRegex(PluginCallError, ENV) as caught:
            process.search("fixture", "switch", 10)
        self.assertNotIn(PASSWORD, str(caught.exception))
        self.assertEqual(self.shop.requests, [])

    def test_wrong_password_fails_both_sources_without_echoing_it(self):
        process = self.plugin(self.both, self.sources(password="wrong-pass"))
        with self.assertRaisesRegex(PluginCallError, "HTTP 401") as caught:
            process.search("fixture", "switch", 10)
        self.assertNotIn("wrong-pass", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
