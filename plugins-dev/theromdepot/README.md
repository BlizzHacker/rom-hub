# The ROM Depot provider for ROM Hub / ROMarr

An installable, browser-assisted provider for The ROM Depot's complete file
catalogue.

| Capability | What it does |
|---|---|
| `search` | Searches a browser-saved `/api/getContents` tree locally |
| `stream` | Opens that file's authenticated `/api/download/<path>` route in the operator's own browser |
| `importer` | Not declared: a server-side fetch would not have the browser's login cookie |

No The ROM Depot username, password, cookie or token is accepted by this
plugin. Sign-in stays in the browser where it already works.

## Why the browser is part of the design

The site's current web application reads the entire directory with one call:

```text
GET https://theromdepot.com/api/getContents
```

The returned recursive tree uses the fields the application itself consumes:
`type`, `name`, `path`, `size`, `mtime` and `children`. Files link to:

```text
GET https://theromdepot.com/api/download/<path>
```

Both routes require the site's session cookie. ROM Hub plugins intentionally
cannot receive that cookie, but a `stream` URL is handed to the operator's own
browser. The workflow therefore has a clean boundary:

```text
signed-in browser -> save the one full-tree JSON document
ROM Hub plugin     -> search that local document, with no network request
signed-in browser <- open the selected /api/download path
```

This is not a crawl and it is not a login automation. The operator performs
the ordinary site login, and one API response contains the full directory.

## Setup

1. Sign in at `https://theromdepot.com` in Brave, Chrome, Edge, Firefox, or
   another normal browser.
2. In that signed-in browser open
   `https://theromdepot.com/api/getContents`.
3. Save the JSON response. If ROM Hub runs on another machine or in a
   container, copy or mount the file somewhere that process can read.
4. Install this repository and configure the saved path:

   ```sh
   rom-hub plugin install https://git.moveweight.com/BlizzHacker/rom-hub-theromdepot
   rom-hub plugin config theromdepot --set catalog_path=/srv/romarr/theromdepot-getContents.json
   ```

5. Search normally:

   ```sh
   rom-hub search "mario party" --platform n64
   ```

When a result is opened through the stream/browser handoff, the browser must
still be logged in to The ROM Depot. If the session expired, the site will ask
you to sign in; the plugin does not intercept or replace that flow.

### Refreshing the catalogue

Repeat steps 2-3. The plugin reads the configured file for each search, so an
updated export is visible without reinstalling the plugin. Replace the old
file only after the new JSON is completely saved to avoid a partial read.

ROMarr Capture's normalized shape is accepted too:

```json
{
  "items": [
    {
      "id": "Nintendo 64/Europe/Mario Party (Europe).z64",
      "title": "Mario Party (Europe)",
      "name": "Mario Party (Europe).z64",
      "platform": "Nintendo 64",
      "region": "Europe",
      "size": 33554432
    }
  ]
}
```

## Search and validation behavior

The full tree is flattened locally. Search terms may match the title,
filename, platform, region, or complete directory path. Exact and prefix title
matches rank first, platform filters accept either the site's label
(`Nintendo 64`) or ROMarr's canonical slug (`n64`), and the caller's result
limit is always honored.

Every result is revalidated before it becomes a browser URL:

- the path must be relative, with no traversal, query, fragment, control
  character, empty component, or embedded URL;
- each URL component is percent-encoded independently;
- a stream handoff must still exist in the configured catalogue, so a forged
  or stale `source_id` cannot manufacture an arbitrary download path;
- only directory names with an exact platform mapping are emitted. Unknown or
  ambiguous folders, including bare `Neo Geo`, are omitted instead of guessed.

The search result records `login_required=true`,
`browser_authenticated=true`, `action=download`, the original filename and
site platform, and the fact that `/api/getContents` was the catalogue source.

## What is deliberately absent

There is no `username`, `password`, `cookie` or `session` config key. There is
also no importer. An importer tells the ROM Hub host to fetch bytes, but the
authenticated cookie belongs to the user's browser, not the host. Advertising
an importer would turn a working browser download into a guaranteed server
401. The browser handoff is the supported download path.

The plugin makes no request to The ROM Depot during search or resolution. Its
network allowlist exists because the URL handed to the browser is still checked
by ROM Hub before it is opened.

## Development

The suite uses only local fixtures and never contacts the site:

```sh
pytest -q
```

It covers the live application's recursive tree shape, normalized captures,
platform mapping against ROMarr's real table, query/filter/ranking behavior,
authenticated download URL construction, path traversal and URL-injection
refusals, stale-id refusal, manifest parsing, catalogue/manifest drift, and the
absence of credential fields and an importer.

## Terms

An account grants access under The ROM Depot's own terms; it does not grant a
new redistribution licence. This plugin indexes metadata the signed-in
operator can already see and opens the site's own download route in that same
operator's browser. Operators remain responsible for what they download and
how they use it.

## License

MIT — see `LICENSE`.
