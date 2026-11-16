# CurseForge Mods & Addons (`curseforge`)

RPP v1 plugin for searching, importing, and streaming mods and addons from [CurseForge](https://www.curseforge.com/).

## Capabilities

- **`search`**: Search mods across supported games (World of Warcraft, Minecraft, Stardew Valley, etc.). Supports API key authentication or web search fallback. Supports query game prefixes (e.g., `wow:dbm` or `mc:sodium`).
- **`importer`**: Resolves mod files into a `FetchPlan` using the CurseForge Core API (`/v1/mods/{modId}/files` and `/v1/mods/{modId}/files/{fileId}/download-url`) with fallback to the ForgeCDN direct URL formula (`https://edge.forgecdn.net/files/{file_id // 1000}/{file_id % 1000}/{file_name}`).
- **`stream`**: Hands over a web target (`StreamTarget(kind="url")`) to view the mod on CurseForge.

## Configuration

- `api_key` (`secret`, optional): CurseForge Core API key from [console.curseforge.com](https://console.curseforge.com/).
- `game_id` (`int`, default `1`): Default game to search (`1` = World of Warcraft, `432` = Minecraft, `669` = Stardew Valley, `0` = all).
