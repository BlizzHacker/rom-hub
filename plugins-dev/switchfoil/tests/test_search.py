import json
import unittest
from contextlib import redirect_stderr
from io import StringIO

from fakes import BASE, DLC, OTHER, PASSWORD, PASSWORD_IN_URL, UPDATE, USER, ctx, too_large

from switchfoil.search import Search, SearchRefused
from switchfoil.titledb import VERSIONS_URL

A = "https://a.your-server.example"
B = "https://b.your-server.example"
SOURCES = [
    f"ShopA|https://{USER}:{PASSWORD_IN_URL}@a.your-server.example|tinfoil",
    "ShopB|https://b.your-server.example|tinfoil",
]


def shop(*files):
    return (200, {"files": [{"url": f"/f/{name}", "size": size} for name, size in files]})


def run(context, query="", platform=None, limit=50):
    err = StringIO()
    with redirect_stderr(err):
        results = Search(context).search(query, platform, limit)
    return results, err.getvalue()


class SearchTests(unittest.TestCase):
    def two_shops(self, titledb=False, extra=None):
        routes = {
            A: shop((BASE, 3_000_000_000), (UPDATE, 400_000_000), (DLC, 1000), (OTHER, 5)),
            B: shop((BASE, 4_400_000_000)),
        }
        routes.update(extra or {})
        return ctx(routes, SOURCES, titledb=titledb)

    def test_platform_gating(self):
        context = self.two_shops()
        self.assertEqual(run(context, "fixture", "snes")[0], [])
        self.assertEqual(context.http.calls, [])
        self.assertTrue(run(context, "fixture", "switch")[0])
        self.assertTrue(run(context, "fixture", None)[0])

    def test_every_word_must_match_case_insensitively(self):
        titles = [r.title for r in run(self.two_shops(), "QUEST fixture")[0]]
        self.assertEqual(len(titles), 4)
        self.assertEqual(run(self.two_shops(), "fixture racer")[0], [])
        self.assertEqual(len(run(self.two_shops(), "extra")[0]), 1)

    def test_title_id_query(self):
        exact = run(self.two_shops(), "0100F1C7A0B10800")[0]
        self.assertEqual([r.extra["type"] for r in exact], ["update"])
        family = run(self.two_shops(), "0100F1C7A0B10000")[0]
        self.assertEqual(sorted(r.extra["type"] for r in family), ["base", "base", "dlc", "update"])
        lower = run(self.two_shops(), "0x0100F1C7A0B20000")[0]
        self.assertEqual([r.extra["source"] for r in lower], ["ShopA"])

    def test_title_and_extra(self):
        results = run(self.two_shops(), "fixture quest")[0]
        update = next(r for r in results if r.extra["type"] == "update")
        self.assertEqual(update.title, "Fixture Quest [0100F1C7A0B10800][v131072] UPD NSZ | ShopA")
        self.assertEqual(update.platform, "switch")
        self.assertEqual(update.size_bytes, 400_000_000)
        self.assertEqual(update.url, A)
        self.assertEqual(
            update.extra,
            {
                "source": "ShopA",
                "filename": UPDATE,
                "format": "nsz",
                "title_id": "0100F1C7A0B10800",
                "version": "131072",
                "type": "update",
                "size": "400000000",
            },
        )
        self.assertEqual(update.source_id, f"ShopA|{A}/f/{UPDATE}")

    def test_duplicates_across_two_shops(self):
        results = run(self.two_shops(), "fixture quest")[0]
        bases = [r for r in results if r.extra["type"] == "base"]
        by_source = {r.extra["source"]: r for r in bases}
        self.assertEqual(
            by_source["ShopA"].title,
            "Fixture Quest [0100F1C7A0B10000][v0] BASE NSP | ShopA | 1.4 GB smaller than ShopB",
        )
        self.assertTrue(by_source["ShopB"].title.endswith("| ShopB | 1.4 GB larger than ShopA"))
        self.assertEqual(by_source["ShopA"].extra["comparison"], "1.4 GB smaller than ShopB")

    def test_titledb_reports_updates_behind_latest(self):
        versions = {"0100F1C7A0B10000": {"65536": "2026-01-01", "196608": "2026-06-01"}}
        context = self.two_shops(titledb=True, extra={VERSIONS_URL: (200, versions)})
        results = run(context, "fixture quest")[0]
        update = next(r for r in results if r.extra["type"] == "update")
        self.assertTrue(update.title.endswith("| behind latest v196608"))
        self.assertEqual(update.extra["latest_version"], "196608")
        self.assertIn(VERSIONS_URL, context.http.urls())

    def test_titledb_off_or_unneeded_makes_no_request(self):
        context = self.two_shops(titledb=False)
        run(context, "fixture")
        self.assertNotIn(VERSIONS_URL, context.http.urls())
        context = self.two_shops(titledb=True)
        run(context, "racer")
        self.assertNotIn(VERSIONS_URL, context.http.urls())

    def test_titledb_failure_is_only_a_warning(self):
        context = self.two_shops(titledb=True, extra={VERSIONS_URL: (500, "")})
        results, err = run(context, "fixture")
        self.assertEqual(len(results), 4)
        self.assertIn("titledb", err)

    def test_a_failing_source_is_skipped_and_named(self):
        context = ctx(
            {A: too_large(A), B: shop((BASE, 1))},
            SOURCES,
        )
        results, err = run(context, "fixture")
        self.assertEqual([r.extra["source"] for r in results], ["ShopB"])
        self.assertIn("ShopA", err)
        self.assertIn("4 MiB", err)
        self.assertNotIn(PASSWORD, err)

    def test_401_on_one_source(self):
        context = ctx({A: (401, ""), B: shop((BASE, 1))}, SOURCES)
        results, err = run(context, "fixture")
        self.assertEqual(len(results), 1)
        self.assertIn("ShopA", err)
        self.assertIn("HTTP 401", err)

    def test_every_source_failing_raises(self):
        context = ctx({A: (401, ""), B: (500, "")}, SOURCES)
        with self.assertRaises(SearchRefused) as caught:
            run(context, "fixture")
        self.assertIn("ShopA", str(caught.exception))
        self.assertIn("ShopB", str(caught.exception))

    def test_no_sources(self):
        with self.assertRaisesRegex(SearchRefused, "no usable source"):
            run(ctx({}, []), "fixture")
        with self.assertRaisesRegex(SearchRefused, "no usable source"):
            run(ctx({}, ["broken line"]), "fixture")

    def test_limit(self):
        self.assertEqual(len(run(self.two_shops(), "", limit=2)[0]), 2)

    def test_credentials_never_reach_a_result(self):
        results, err = run(self.two_shops(), "")
        dumped = json.dumps([r.model_dump() for r in results]) + err
        token = __import__("base64").b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        for secret in (USER, PASSWORD, PASSWORD_IN_URL, token, "@a.your-server"):
            self.assertNotIn(secret, dumped)


if __name__ == "__main__":
    unittest.main()
