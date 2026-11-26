# FitGirl & DODI Repacks plugin for ROM Hub (`repacks`)

Implements the RPP v1 `search` and `torrent` capabilities for official [**FitGirl Repacks**](https://fitgirl-repacks.site/) and [**DODI Repacks**](https://dodi-repacks.site/) RSS 2.0 feeds and WordPress search endpoints.

- **Automatic RSS Subscription**: Subscribes to [`https://fitgirl-repacks.site/feed/`](https://fitgirl-repacks.site/feed/) and [`https://dodi-repacks.site/feed/`](https://dodi-repacks.site/feed/) so ROMarr's scheduled **RssSync** job and plugin dashboard automatically pull the latest PC repacks as they are published.
- **Interactive Search**: Queries both the live RSS feeds and the WordPress search feeds (`/?s=<query>&feed=rss2`, with `/wp-json/wp/v2/posts?search=<query>` fallback) on `fitgirl-repacks.site` and `dodi-repacks.site`.
- **Validated Magnet & Tracker Resolution**: Extracts embedded BitTorrent v1 magnets (`magnet:?xt=urn:btih:…`) and repack sizes from feed entries, validates info-hashes through `repacks.torrent:Torrent`, and attaches twelve declared open public trackers for handoff to qBittorrent, Transmission, or Deluge.

## Install

```bash
rom-hub plugin install repacks
rom-hub search "Cyberpunk 2077" --platform win
rom-hub search "latest" --platform win
```

## Configuration

| Key | Type | Default | Meaning |
|---|---|---|---|
| `fitgirl_enabled` | `bool` | `true` | Poll and search the official FitGirl Repacks RSS feed (`fitgirl-repacks.site`) |
| `dodi_enabled` | `bool` | `true` | Poll and search the official DODI Repacks RSS feed (`dodi-repacks.site`) |
| `fitgirl_feed` | `str` | `https://fitgirl-repacks.site/feed/` | FitGirl RSS 2.0 feed URL for automatic subscription updates |
| `dodi_feed` | `str` | `https://dodi-repacks.site/feed/` | DODI RSS 2.0 feed URL for automatic subscription updates |
| `rss_subscribe` | `bool` | `true` | Include FitGirl and DODI RSS feeds in ROMarr's automatic RSS sync sweeps |
| `include_mirrors` | `bool` | `true` | Include browser mirror posts that publish direct-hoster links without an inline magnet |
