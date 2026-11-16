# GitHub Open-Source Games, Engines & Bots plugin for ROM Hub

Implements RPP v1 `search`, `importer`, and `stream` capabilities against the GitHub API (`api.github.com`, `github.com`, `codeload.github.com`).

| Capability | Target | Description |
|---|---|---|
| `search` | `api.github.com/search/repositories` | Searches open-source game, engine, bot, and modding repositories with mode-specific topic enrichment. |
| `importer` | `api.github.com/repos/...` | Plans release binary assets and/or default branch source zip. |
| `stream` | `github.com/...` | Resolves repo URL as stream target for in-browser inspection. |

## Config

| Key | Type | Default | Description |
|---|---|---|---|
| `github_token` | `secret` | (unset) | Optional GitHub personal access token for higher rate limits. |
| `mode` | `str` | `"all"` | Mode filter (`all`, `games`, `bots`, `tools`). Enriches query with relevant topics. |
| `include_source` | `bool` | `true` | Include source zip archive alongside release binary assets. |
