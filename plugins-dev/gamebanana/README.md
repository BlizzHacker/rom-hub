# GameBanana Mods, Skins & Tools plugin for ROM Hub

Implements RPP v1 `search`, `importer`, and `stream` capabilities against the GameBanana API (`gamebanana.com`, `*.gamebanana.com`).

| Capability | Target | Description |
|---|---|---|
| `search` | `gamebanana.com/apiv11/Util/Search/Results` | Searches GameBanana items filtered by models and optional game id. |
| `importer` | `gamebanana.com/apiv11/{Model}/{id}/ProfilePage` | Plans all downloadable files associated with the item. |
| `stream` | `gamebanana.com/mods/{id}` | Resolves item web page URL as stream target. |

## Config

| Key | Type | Default | Description |
|---|---|---|---|
| `game_id` | `str` | `""` | Optional GameBanana game ID filter (e.g. `8` for Smash, `7273` for Friday Night Funkin). |
| `models` | `str` | `"Mod,Tool,Skin"` | Comma-separated list of GameBanana model names to include in search. |
