# rom-hub-mdm

A [ROM Hub](https://github.com/BlizzHacker/rom-hub) plugin that connects [ROMarr](https://github.com/BlizzHacker/romarr)'s standalone downloads to [Mister Download Manager](https://github.com/anefzaoui/misterdownloadmanager) (MDM), anefzaoui's multi-connection HTTP and BitTorrent download manager.

ROMarr ships with this plugin preinstalled. It installs it once, on the first start with ROM Hub available. If you disable it in **Hub → Plugins** (or remove it by hand), ROMarr leaves it that way.

## What it does

- **Holds ROMarr's MDM settings.** ROMarr's two MDM download clients, *Standalone Direct HTTP (MDM)* and *Standalone BitTorrent (MDM)*, take their connection from this plugin once its `mode` is set. Edit the settings here or in ROMarr's download-client form; a change in either place shows in the other. ROM Hub keeps `api_token` in its secret store.
- **Offers MDM's official release archives.** The `cores` entries list MDM's latest GitHub release, one per host it publishes a build for, each with the SHA-256 GitHub reports for it. Installing one fetches the archive and the release's `SHA256SUMS.txt` from anefzaoui's release.

| Setting | Meaning |
|---|---|
| `mode` | `cli` (ROMarr runs `misterdownloadmanager-cli` itself) or `remote` (ROMarr talks to `misterdownloadmanager --headless` or the desktop app over HTTPS). Blank leaves each ROMarr client's own settings alone. |
| `remote_url` | Remote mode only, for example `https://127.0.0.1:8463`. |
| `api_token` | Remote mode only: the bearer token MDM's Remote Access shows. Secret. |
| `connections` | Parallel HTTP range connections for direct downloads, 1 to 128. Default 8. |
| `category` | Optional MDM category in Remote mode: `programs`, `compressed`, `documents`, `music`, `videos` or `other`. |

ROMarr does the downloading itself, through MDM. The plugin never connects to MDM. ROM Hub plugins run in a sandbox that may only make HTTPS requests to the hosts in `manifest.toml` (`api.github.com`, `github.com` and `release-assets.githubusercontent.com`, where GitHub's release downloads redirect), and an MDM on your network is not one of them.

## Installing MDM from the plugin

| Core | Archive | Notes |
|---|---|---|
| `mdm-linux-x86_64` | `MisterDownloadManager-<ver>-linux-x86_64.tar.gz` | Built against glibc. An Alpine/musl container, such as ROMarr's Docker image, cannot run it: use Remote mode there. |
| `mdm-windows-x86_64` | `MisterDownloadManager-<ver>-portable.zip` | Portable build. |
| `mdm-macos-arm64`, `mdm-macos-x86_64` | `MisterDownloadManager-<ver>-macos-<arch>[-experimental].dmg` | App bundle; set `MDM_CLI_PATH` to the CLI inside it. |

The plugin only offers an archive whose SHA-256 GitHub reports and whose release publishes `SHA256SUMS.txt`. ROM Hub checks each download's size, and writes `SHA256SUMS.txt` beside the archive as `MisterDownloadManager-<ver>-SHA256SUMS.txt`. Check the archive against it before you unpack it, the same check ROMarr's **Install MDM** button makes:

```bash
# In the folder `rom-hub cores install` printed: <cores dir>/mdm, where the
# cores dir is ROM_HUB_CORES_DIR if set, otherwise ROM Hub's default.
# <ver> is the version just installed and <archive> the file it downloaded.
cd "<cores dir>/mdm"
grep -F "  <archive>" MisterDownloadManager-<ver>-SHA256SUMS.txt | sha256sum -c -
```

`grep` finds nothing, and the check fails, when the checksum file does not list that archive.

The plugin cannot run that check for you: its sandbox does not follow the redirect GitHub answers with, and ROM Hub does not hash what it fetches.

## Credits and licence

[Mister Download Manager](https://github.com/anefzaoui/misterdownloadmanager) is by anefzaoui and licensed GPL-3.0-or-later. This plugin includes none of its code: it stores ROMarr's connection settings and points at MDM's official release archives, which are downloaded from anefzaoui's GitHub releases when you ask for them. ROMarr drives MDM across a process or HTTP boundary only.

This plugin is MIT-licensed, Copyright (c) 2026 MOVE WEIGHT. See [LICENSE](LICENSE).

## Development

```bash
git clone https://github.com/BlizzHacker/rom-hub ../rom-hub
python -m pytest tests -q
```
