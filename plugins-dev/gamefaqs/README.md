# GameFAQs Guides & FAQs plugin for ROM Hub (RPP v1)

Provides guide search, fetch planning, and web streaming for GameFAQs walkthroughs and FAQs.

## Capabilities

- **`search`**: Searches GameFAQs (`https://gamefaqs.gamespot.com/search?game={query}`) and extracts game guides and FAQs. When search lands on a game hub or returns game links, fetches `/{plat}/{id}-{slug}/faqs` to discover guides.
- **`importer`**: Generates a `FetchPlan` targeting printable, single-page guide views (`https://gamefaqs.gamespot.com/{path}?single=1&print=1`) saved as clean `.html` files.
- **`stream`**: Resolves guides to web stream targets viewable in-browser.

## Permissions

- Network: `gamefaqs.gamespot.com`
