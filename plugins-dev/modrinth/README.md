# Modrinth Mods, Modpacks & Shaders plugin for ROM Hub

Implements RPP v1 `search`, `importer`, and `stream` capabilities against the Modrinth API (`api.modrinth.com`, `cdn.modrinth.com`, `modrinth.com`).

| Capability | Target | Description |
|---|---|---|
| `search` | `api.modrinth.com/v2/search` | Search mods, modpacks, shaders, and plugins with optional project_type filtering. |
| `importer` | `api.modrinth.com/v2/project/{id}/version` | Imports all distribution files from the latest version. |
| `stream` | `modrinth.com/project/{slug}` | Resolves project web page URL as stream target. |

## Config

| Key | Type | Default | Description |
|---|---|---|---|
| `project_type` | `str` | `""` | Optional project type filter (`mod`, `modpack`, `shader`, `plugin`, or empty for all). |
