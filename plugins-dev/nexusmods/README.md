# Nexus Mods (`nexusmods`)

RPP v1 plugin for searching, importing, and streaming mods from [Nexus Mods](https://www.nexusmods.com/).

## Capabilities

- **`search`**: Anonymous zero-key GraphQL search (`POST https://api.nexusmods.com/v2/graphql`) across games (defaulting to `diablo2resurrected`, or specified via `game_domain` config or query prefix e.g. `skyrimspecialedition:skyui`).
- **`importer`**: Resolves mod files into a `FetchPlan` using the Nexus Mods v1 API (`https://api.nexusmods.com/v1/games/{domain}/mods/{mod_id}/files.json` and download links) for users with Premium API keys. If `api_key` is not configured or HTTP 403 occurs (free tier limitations on direct API downloads), raises an actionable error pointing to `stream` browser download.
- **`stream`**: Hands over a web target (`StreamTarget(kind="url")`) pointing directly to `https://www.nexusmods.com/{domain}/mods/{mod_id}?tab=files` for direct browser play/download.

## Configuration

- `api_key` (`secret`, optional): Nexus Mods personal API key (required for automated v1 file download with Premium accounts).
- `game_domain` (`str`, default `"diablo2resurrected"`): Default game domain (e.g. `diablo2resurrected`, `skyrimspecialedition`, `fallout4`, `cyberpunk2077`). Empty searches across all games.
