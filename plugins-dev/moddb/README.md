# ModDB Mods, Addons & Total Conversions (`moddb`)

RPP v1 plugin for searching, importing, and streaming mods, addons, and total conversions from [ModDB](https://www.moddb.com/).

## Capabilities

- **`search`**: Search mods across all games or filtered by game slug (e.g. `diablo-2-lod`). Supports query prefix filtering (e.g. `diablo-2-lod:plugy`).
- **`importer`**: Discovers download IDs from the mod's `/downloads` page, resolves mirrors at `/downloads/start/{id}`, and constructs a `FetchPlan` pointing to direct mirror URLs (which follow 302 redirects to `media.moddb.com`).
- **`stream`**: Hands over a web target (`StreamTarget(kind="url")`) to view the mod on ModDB (`https://www.moddb.com/mods/{slug}`).

## Configuration

- `game_slug` (`str`, default `""`): Default game slug to filter mod searches (e.g. `diablo-2-lod`, `half-life`, `quake`).
