# rom-hub-minerva

**MiNERVA Archive** as a [ROM Hub](https://github.com/BlizzHacker/rom-hub) plugin — the community continuation of Myrient (shut down 31 March 2026), which archives Myrient's entire catalogue and distributes it as collection torrents through a public, keyless JSON API.

This is a *plugin*: a sandboxed subprocess that declares its capabilities and its network allowlist in `manifest.toml` and never opens a socket itself. Nothing about it is welded into ROMarr, RomM, or any other host — any host that speaks the ROM Hub plugin protocol gets MiNERVA search by installing this repo.

## Capabilities

| Capability | What it does |
|---|---|
| `search` | Queries only `GET /v1/api/rom/search` with MiNERVA's filters (`group`, `console`, `region`, `tokenised`) and 100-row offset pagination. Each matching API file row stays distinct, even when several rows share a collection magnet. Results include the exact archive path, row `id` and `so_id`, plus the collection torrent hash. |
| `torrent` | MiNERVA's magnets arrive bare (no trackers — DHT-only). `resolve()` keeps the info-hash, appends twelve well-known public trackers (every host declared in the manifest, because the Hub gates each `tr=` against that list), and returns a `TorrentSource` the host validates and hands to your torrent client. |

No `importer`: MiNERVA distributes by torrent only, and the Hub is deliberately not a torrent client — the host hands the magnet off to yours (qBittorrent, Transmission, Deluge).

## Install

```sh
rom-hub plugin install https://github.com/BlizzHacker/rom-hub-minerva
```

In ROMarr: **Settings → Hub → Plugins** shows it like any other; enabled plugins join Interactive Search and the request pipeline automatically.

## Config

| Key | Default | Meaning |
|---|---|---|
| `group` | `""` | Only this dump group (e.g. `TOSEC`, `No-Intro`, `Redump` — values from MiNERVA's filter bar) |
| `console` | `""` | Only this console (MiNERVA's own console names) |
| `region` | `""` | Only this region (e.g. `USA`) |
| `tokenised` | `false` | Match each query word separately instead of the whole phrase — better recall, more noise |
| `max_pages` | `1` | Search pages to walk (100 rows each) |

## Things worth knowing

- The result names a file; the magnet names a collection. A result shows the exact path, API row id and so_id. Several matching rows can share one magnet and remain separate results.
- Picking a row does not select only that file inside the torrent. The selected result hands off the collection magnet; ROM Hub does not currently pass MiNERVA's file index to the torrent client. The client may download the whole collection unless you choose files there.
- The plugin requests only /v1/api/rom/search. MiNERVA's robots.txt disallows /api/, /browse/ and /rom/; the API route starts with /v1/api/ and is outside the /api/ prefix. The result's /rom?id= URL is an external link for a person and is never fetched by the plugin.
- The API is slow (20-25s per query, measured live). Hosts should allow at least 60s for plugin HTTP.
- No sizes or seeder counts are published. size_bytes is None (unknown), never fabricated.
- The API publishes no per-file download; the collection magnet is the only route.

## Tests

```sh
python -m pytest tests/ -q     # needs rom_hub_sdk installed (the ROMarr venv has it)
```

16 tests: query/filters/pagination/dedupe against canned API shapes, magnet rebuild + refusal paths, and the decisive one — the Hub's own `check_magnet` gate accepts what `resolve()` returns, with every declared tracker surviving.

## License

MIT.
