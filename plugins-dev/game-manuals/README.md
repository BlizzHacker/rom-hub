# Game Manuals & Strategy Guides (Archive.org) plugin for ROM Hub (RPP v1)

Provides game manual and strategy guide search, multi-file import planning, and web streaming from Internet Archive texts collections.

## Capabilities

- **`search`**: Queries `https://archive.org/advancedsearch.php` with `q=mediatype:texts AND title:({query}) AND (manual OR "player's guide" OR "strategy guide")` to find game documentation.
- **`importer`**: Queries `https://archive.org/metadata/{identifier}/files` and selects all downloadable manuals (`.pdf`, `.cbz`, `.epub`, `_djvu.txt`, etc., up to 32 files) into a clean `FetchPlan`.
- **`stream`**: Resolves to `https://archive.org/details/{identifier}`.

## Permissions

- Network: `archive.org`, `*.archive.org`
