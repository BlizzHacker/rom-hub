# Thunderstore Game Mods (100+ Games) plugin for ROM Hub

Implements RPP v1 `search`, `importer`, and `stream` capabilities against Thunderstore (`thunderstore.io`, `cdn.thunderstore.io`).

| Capability | Target | Description |
|---|---|---|
| `search` | `thunderstore.io/c/{community}/api/v1/package/` | Searches game packages across one or more Thunderstore communities. Supports `"community:query"` search syntax. |
| `importer` | `thunderstore.io/c/{community}/api/v1/package/` | Resolves latest package version zip download. |
| `stream` | `thunderstore.io/c/{community}/p/{owner}/{name}/` | Resolves web page URL as stream target. |

## Config

| Key | Type | Default | Description |
|---|---|---|---|
| `community` | `str` | `"valheim"` | Default game community (e.g. `valheim`, `lethal-company`, `riskofrain2`). Also supports comma-separated list. |
