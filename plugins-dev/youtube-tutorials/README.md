# YouTube Game Tutorials & Walkthroughs plugin for ROM Hub (RPP v1)

Provides video walkthrough search (via YouTube Data API v3 or keyless search), video asset import planning (oEmbed metadata, high-resolution thumbnail, and transcript when available), and web streaming.

## Capabilities

- **`search`**: Searches YouTube using optional `api_key` (Google Data API v3 search endpoint) or keyless query endpoint parsing video renderers.
- **`importer`**: Generates a `FetchPlan` downloading the video's oEmbed metadata JSON (`https://www.youtube.com/oembed`), thumbnail image (`https://i.ytimg.com/vi/{video_id}/hqdefault.jpg`), and transcript captions if available.
- **`stream`**: Resolves video to `https://www.youtube.com/watch?v={video_id}`.

## Permissions

- Network: `www.youtube.com`, `www.googleapis.com`, `i.ytimg.com`
