"""Offline unit tests for the FitGirl & DODI Repacks ROM Hub plugin."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repacks.search import (
    DODI_FEED,
    FITGIRL_FEED,
    PLATFORM,
    Search,
    parse_rss_items,
    parse_wp_json_posts,
)
from repacks.torrent import DEFAULT_TRACKERS, Torrent, TorrentRefused
from rom_hub_sdk import SearchResult

FITGIRL_RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>FitGirl Repacks</title>
    <link>https://fitgirl-repacks.site/</link>
    <item>
      <title>Upcoming Repacks</title>
      <link>https://fitgirl-repacks.site/upcoming-repacks-10/</link>
      <pubDate>Thu, 26 Nov 2026 12:08:01 +0000</pubDate>
      <content:encoded><![CDATA[<p>Upcoming games list</p>]]></content:encoded>
    </item>
    <item>
      <title>Updates Digest for November 25, 2026</title>
      <link>https://fitgirl-repacks.site/updates-digest/</link>
      <pubDate>Thu, 26 Nov 2026 12:07:15 +0000</pubDate>
      <content:encoded><![CDATA[<p>Digest links</p>]]></content:encoded>
    </item>
    <item>
      <title>Cities: Skylines – Collection, v1.21.2-f5 + 94 DLCs/Bonuses</title>
      <link>https://fitgirl-repacks.site/cities-skylines-deluxe-edition/</link>
      <pubDate>Thu, 26 Nov 2026 02:00:41 +0000</pubDate>
      <content:encoded><![CDATA[
        <p>Original Size: <strong>9.8 GB</strong><br/>
        Repack Size: <strong>from 6.7 GB</strong> [Selective Download]</p>
        <ul>
          <li><a href="https://1337x.to/torrent/123/">1337x</a> | [<a href="magnet:?xt=urn:btih:C1389977986A805EAC49BC5AD4608CBEAB5CF177&#038;dn=Cities%3A+Skylines+-+Collection+%28v1.21.2-f5+%2B+94+DLCs%2FBonuses%2C+MULTi10%29+%5BFitGirl+Repack%2C+Selective+Download+-+from+6.7+GB%5D&#038;tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce">magnet</a>]</li>
          <li><a href="https://datanodes.to/1bq3qv1er3vp/Cities_Skylines.part1.rar">DataNodes</a></li>
        </ul>
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""

DODI_RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>DODI Repacks</title>
    <link>https://dodi-repacks.site</link>
    <item>
      <title>2352- Gears of War: E-Day (Update 5 + All DLCs + MULTi22) (From 93.2 GB) (Fast Install) [DODI Repack]</title>
      <link>https://dodi-repacks.site/gears-of-war-e-day/</link>
      <pubDate>Thu, 26 Nov 2026 00:05:22 +0000</pubDate>
      <content:encoded><![CDATA[
        <p><strong>Torrent – <a href="magnet:?xt=urn:btih:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2&amp;dn=Gears.of.War.E-Day.DODI">Click Here</a></strong></p>
        <p><strong>DataNodes – <a href="https://zovo.to/6r4pqPn">Click Here</a></strong></p>
      ]]></content:encoded>
    </item>
    <item>
      <title>155- Cities: Skylines – Collection (v1.21.2.F5 + All DLCs) (From 7.5 GB) [DODI Repack]</title>
      <link>https://dodi-repacks.site/cities-skylines/</link>
      <pubDate>Thu, 26 Nov 2026 00:04:18 +0000</pubDate>
      <content:encoded><![CDATA[
        <p><strong>Torrent – <a href="https://zovo.to/DXMchy9">Click Here</a></strong></p>
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""


class FakeHttp:
    def __init__(self, routes: dict[str, tuple[int, object]]):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None):
        self.calls.append((url, params))
        status, body = self.routes.get(url, (404, ""))
        if isinstance(body, (list, dict)):
            return SimpleNamespace(status_code=status, text="", json=lambda: body)
        return SimpleNamespace(status_code=status, text=str(body), json=lambda: {})


def _provider(routes: dict[str, tuple[int, object]], config: dict | None = None) -> Search:
    provider = Search.__new__(Search)
    provider.ctx = SimpleNamespace(http=FakeHttp(routes), config=config or {})
    return provider


def test_parse_fitgirl_rss_skips_digests_and_extracts_magnet_and_size():
    items = parse_rss_items(FITGIRL_RSS_SAMPLE, "FitGirl")
    assert len(items) == 1
    item = items[0]
    assert "FitGirl Repack" in item["title"]
    assert "Cities: Skylines" in item["title"]
    assert item["magnet"].startswith("magnet:?xt=urn:btih:C1389977986A805EAC49BC5AD4608CBEAB5CF177")
    assert item["size_bytes"] == int(6.7 * 1024**3)
    assert "https://datanodes.to/1bq3qv1er3vp/Cities_Skylines.part1.rar" in item["mirrors"]


def test_parse_dodi_rss_strips_catalogue_number_and_keeps_mirrors():
    items = parse_rss_items(DODI_RSS_SAMPLE, "DODI")
    assert len(items) == 2
    assert items[0]["title"].startswith("Gears of War: E-Day")
    assert "[DODI Repack]" in items[0]["title"]
    assert items[0]["size_bytes"] == int(93.2 * 1024**3)
    assert items[0]["magnet"].startswith("magnet:?xt=urn:btih:a1b2c3d4e5f6")
    assert items[1]["magnet"] == ""
    assert items[1]["mirrors"] == ["https://zovo.to/DXMchy9"]


def test_rss_feed_subscription_poll_returns_latest_from_both_feeds():
    provider = _provider({
        FITGIRL_FEED: (200, FITGIRL_RSS_SAMPLE),
        DODI_FEED: (200, DODI_RSS_SAMPLE),
    })
    results = provider.search("latest", PLATFORM, limit=10)
    assert len(results) == 3
    # Magnet-bearing rows are ranked ahead of browser-only mirror rows.
    assert results[0].extra["downloadable"] == "torrent"
    assert results[1].extra["downloadable"] == "torrent"
    assert results[2].extra["downloadable"] == "browser"
    assert {r.extra["repacker"] for r in results} == {"FitGirl", "DODI"}
    assert all(r.platform == "win" for r in results)


def test_query_search_combines_rss_and_wp_search_and_deduplicates():
    provider = _provider({
        FITGIRL_FEED: (200, FITGIRL_RSS_SAMPLE),
        DODI_FEED: (200, DODI_RSS_SAMPLE),
        "https://fitgirl-repacks.site/?s=cities+skylines&feed=rss2": (200, FITGIRL_RSS_SAMPLE),
        "https://dodi-repacks.site/?s=cities+skylines&feed=rss2": (200, DODI_RSS_SAMPLE),
    })
    results = provider.search("Cities Skylines", "win", limit=10)
    assert len(results) == 2
    assert results[0].extra["repacker"] == "FitGirl"
    assert results[0].extra["torrent_hash"] == "c1389977986a805eac49bc5ad4608cbeab5cf177"
    assert results[1].extra["repacker"] == "DODI"


def test_wp_json_fallback_when_search_rss_unavailable():
    wp_posts = [{
        "link": "https://fitgirl-repacks.site/elden-ring/",
        "date_gmt": "2026-11-20T10:00:00",
        "title": {"rendered": "Elden Ring: Shadow of the Erdtree (v1.16)"},
        "content": {
            "rendered": (
                "<p>Repack Size: <strong>47.5 GB</strong></p>"
                '<a href="magnet:?xt=urn:btih:1111222233334444555566667777888899990000&amp;dn=Elden+Ring+%5BFitGirl+Repack%5D">magnet</a>'
            )
        },
    }]
    provider = _provider({
        FITGIRL_FEED: (200, ""),
        "https://fitgirl-repacks.site/?s=elden+ring&feed=rss2": (503, ""),
        "https://fitgirl-repacks.site/wp-json/wp/v2/posts": (200, wp_posts),
    }, config={"dodi_enabled": False})
    results = provider.search("Elden Ring", "win", limit=5)
    assert len(results) == 1
    assert results[0].title == "Elden Ring [FitGirl Repack]"
    assert results[0].size_bytes == int(47.5 * 1024**3)
    assert results[0].extra["torrent_hash"] == "1111222233334444555566667777888899990000"


def test_non_pc_platform_returns_empty():
    provider = _provider({FITGIRL_FEED: (200, FITGIRL_RSS_SAMPLE)})
    assert provider.search("Cities Skylines", "snes", limit=10) == []


def test_torrent_resolve_normalises_info_hash_and_appends_declared_trackers():
    result = SearchResult(
        source_id="repack:fitgirl:c1389977986a805eac49bc5ad4608cbeab5cf177",
        title="Cities: Skylines [FitGirl Repack]",
        platform="win",
        size_bytes=int(6.7 * 1024**3),
        url="https://fitgirl-repacks.site/cities-skylines-deluxe-edition/",
        extra={
            "repacker": "FitGirl",
            "torrent_magnet": (
                "magnet:?xt=urn:btih:C1389977986A805EAC49BC5AD4608CBEAB5CF177"
                "&dn=Cities%3A+Skylines+%5BFitGirl+Repack%5D"
                "&tr=udp%3A%2F%2Fundeclared.example.com%3A6969%2Fannounce"
            ),
        },
    )
    resolved = Torrent().resolve(result)
    assert resolved.kind == "magnet"
    assert resolved.info_hash == "c1389977986a805eac49bc5ad4608cbeab5cf177"
    assert "undeclared.example.com" not in resolved.source
    for tr in DEFAULT_TRACKERS:
        assert tr.split("://", 1)[1].split(":", 1)[0] in resolved.source


def test_torrent_resolve_refuses_browser_only_result():
    result = SearchResult(
        source_id="repack:dodi:abc123",
        title="Cities: Skylines [DODI Repack]",
        platform="win",
        extra={"repacker": "DODI", "downloadable": "browser"},
    )
    with pytest.raises(TorrentRefused, match="no inline magnet"):
        Torrent().resolve(result)
