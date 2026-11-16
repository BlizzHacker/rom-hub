# StrategyWiki Walkthroughs plugin for ROM Hub (RPP v1)

Provides walkthrough search, multi-chapter import planning, and web streaming for StrategyWiki guides.

## Capabilities

- **`search`**: Queries `https://strategywiki.org/w/api.php` with `list=search` to find game walkthroughs.
- **`importer`**: Queries subpages via `list=allpages&apprefix={Title}/` (up to 50 chapters) and builds a `FetchPlan` targeting MediaWiki parse endpoints with clean filenames.
- **`stream`**: Resolves game guides to `https://strategywiki.org/wiki/{page_slug}`.

## Permissions

- Network: `strategywiki.org`
