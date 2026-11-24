import base64
import unittest
from pathlib import Path

from fakes import BASE, PASSWORD, PASSWORD_IN_URL, USER, ctx
from rom_hub.manifest import load_manifest
from rom_hub.netpolicy import check_url, network_policy
from rom_hub_sdk import SearchResult

from switchfoil.importer import Importer, ImportRefused
from switchfoil.search import Search

ROOT = Path(__file__).resolve().parent.parent
SHOP = "https://shop.your-server.example"
LINE = f"Home|https://{USER}:{PASSWORD_IN_URL}@shop.your-server.example|tinfoil|H1"
TOKEN = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()


def result(source_id, filename=BASE):
    return SearchResult(
        source_id=source_id,
        title="t",
        platform="switch",
        size_bytes=7,
        extra={"filename": filename},
    )


class ImporterTests(unittest.TestCase):
    def test_search_then_plan(self):
        index = {"files": [{"url": f"/api/download/tok1#{BASE}", "size": 7}]}
        context = ctx({SHOP: (200, index)}, [LINE])
        hit = Search(context).search("fixture", None, 10)[0]
        plan = Importer(context).plan(hit)
        self.assertEqual(plan.platform, "switch")
        (file,) = plan.files
        self.assertEqual(file.url, SHOP + "/api/download/tok1")
        self.assertEqual(file.filename, BASE)
        self.assertEqual(file.size_bytes, 7)
        self.assertEqual(file.headers["Authorization"], f"Basic {TOKEN}")
        self.assertEqual(file.headers["User-Agent"], "cyberfoil")
        self.assertEqual(file.headers["Hauth"], "H1")

    def test_plan_reads_credentials_from_current_config(self):
        context = ctx({}, [LINE])
        hit = result(f"Home|{SHOP}/f/{BASE}")
        changed = f"Home|https://{USER}:newpass@shop.your-server.example"
        context.config["sources"] = [changed]
        plan = Importer(context).plan(hit)
        expected = base64.b64encode(f"{USER}:newpass".encode()).decode()
        self.assertEqual(plan.files[0].headers["Authorization"], f"Basic {expected}")

    def test_no_credentials_for_another_origin(self):
        context = ctx({}, [LINE])
        for url in ("https://elsewhere.example/f.nsp", "http://shop.your-server.example/f.nsp"):
            with self.subTest(url=url):
                plan = Importer(context).plan(result(f"Home|{url}"))
                self.assertNotIn("Authorization", plan.files[0].headers)
                self.assertEqual(plan.files[0].headers["Hauth"], "0")

    def test_userinfo_in_a_result_is_removed(self):
        plan = Importer(ctx({}, [LINE])).plan(
            result("Home|https://x:y@shop.your-server.example/f.nsp")
        )
        self.assertEqual(plan.files[0].url, "https://shop.your-server.example/f.nsp")

    def test_refusals(self):
        context = ctx({}, [LINE])
        for source_id in (
            "no-separator",
            "Gone|https://shop.your-server.example/f.nsp",
            "Home|ftp://h/f.nsp",
        ):
            with self.subTest(source_id=source_id):
                with self.assertRaises(ImportRefused) as caught:
                    Importer(context).plan(result(source_id))
                self.assertNotIn(PASSWORD, str(caught.exception))

    def test_filename_is_made_safe(self):
        plan = Importer(ctx({}, [LINE])).plan(
            result(
                f"Home|{SHOP}/x", filename="C:\\Games\\Fixture: Quest [0100F1C7A0B10000][v0].nsp"
            )
        )
        self.assertEqual(plan.files[0].filename, "Fixture - Quest [0100F1C7A0B10000][v0].nsp")
        plan = Importer(ctx({}, [LINE])).plan(
            result(f"Home|{SHOP}/api/download/t#Fixture%20Quest.nsz", "")
        )
        self.assertEqual(plan.files[0].filename, "Fixture Quest.nsz")


class ManifestTests(unittest.TestCase):
    def test_manifest_loads_and_grants_come_from_the_environment(self):
        manifest = load_manifest(ROOT / "manifest.toml")
        self.assertEqual(manifest.slug, "switchfoil")
        self.assertEqual(manifest.network, ["raw.githubusercontent.com"])
        self.assertTrue(manifest.network_from_env)
        self.assertEqual(set(manifest.capabilities), {"search", "importer"})
        policy = network_policy(
            manifest,
            {"ROM_HUB_NETWORK_SWITCHFOIL": "shop.your-server.example,http://192.168.1.12:8465"},
        )
        check_url(SHOP + "/api/remote/sections", policy)
        check_url("http://192.168.1.12:8465/api/download/t", policy)
        check_url("https://raw.githubusercontent.com/blawar/titledb/master/versions.json", policy)

    def test_plan_urls_pass_the_policy(self):
        manifest = load_manifest(ROOT / "manifest.toml")
        policy = network_policy(
            manifest, {"ROM_HUB_NETWORK_SWITCHFOIL": "shop.your-server.example"}
        )
        plan = Importer(ctx({}, [LINE])).plan(result(f"Home|{SHOP}/api/download/t#{BASE}"))
        check_url(plan.files[0].url, policy)


if __name__ == "__main__":
    unittest.main()
