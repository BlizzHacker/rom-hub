# GameFAQs Guides & FAQs plugin for ROM Hub (RPP v1)

Provides guide search, fetch planning, and web streaming for GameFAQs walkthroughs and FAQs.

## Capabilities

- **`search`**: Looks the title up through the JSON endpoint behind the site's own search box (`/ajax/home_game_search?term={query}`), then reads up to three games' `/{plat}/{id}-{slug}/faqs` pages for their guides. The `/search?game=` results page renders with script and carries no links, so it is only a fallback. GameFAQs sits behind Cloudflare: set a FlareSolverr URL on the Hub (ROMarr: Settings -> General) or every request answers 403.
- **`importer`**: Generates a `FetchPlan` targeting printable, single-page guide views (`https://gamefaqs.gamespot.com/{path}?single=1&print=1`) saved as clean `.html` files.
- **`stream`**: Resolves guides to web stream targets viewable in-browser.

## Permissions

- Network: `gamefaqs.gamespot.com`
