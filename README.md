# ROM Hub

**A plugin host for self-hosted ROM library managers.** Runs beside
[RomM](https://github.com/rommapp/romm),
[Gaseous](https://github.com/gaseous-project/gaseous-server) or
[Retrom](https://github.com/JMBeresford/retrom) and adds sources — searching them,
importing from them, and enriching what you already have — to a server with no
plugin system of its own.

[![CI](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml)
[![coverage 87%](https://img.shields.io/badge/coverage-87%25-brightgreen)](#tests)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![licence MIT](https://img.shields.io/github/license/BlizzHacker/rom-hub)](LICENSE)

![RomM shelf populated by ROM Hub plugins](docs/screenshots/romm.png)

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
- [Configuration](#configuration)
- [Library backends](#library-backends)
- [Plugin catalogue](#plugin-catalogue)
- [Plugin credentials](#plugin-credentials)
- [Security](#security)
- [Using it from ROMarr](#using-it-from-romarr)
- [Development](#development)

---

## Features

- **Backend-agnostic plugins.** A plugin never talks to a library server and holds no credential for one. It returns a description of work; the Hub executes it against whichever backend is configured. One plugin works against all three servers.
- **Nine capabilities** — `search`, `importer`, `metadata`, `stream`, `cores`, `firmware`, `assets`, `census`, `torrent`.
- **22 published plugins** — Internet Archive, No-Intro, Demozoo, Aminet, IF Archive, itch.io, ScummVM, homebrew hubs, Hasheous, OpenVGDB, libretro DATs/thumbnails/cores/overlays/cheats, RetroAchievements, Open BIOS and more.
- **Sandboxed execution.** Plugins run as subprocesses with no token, no filesystem mount and no network except through a host RPC checked against the plugin's declared allowlist. On Linux a self-imposed seccomp filter blocks sockets and `exec` outright.
- **Third-party catalogues.** Publish your own plugin directory; the bundled one always wins collisions.
- **Resumable jobs.** Downloads and import state survive a restart.

## Requirements

| | |
|---|---|
| **Python** | 3.12 or 3.13 |
| **Library server** | RomM, Gaseous or Retrom (only for `import` and `enrich` — `search` needs none) |
| **Platform** | Linux for sandboxed plugin execution. Windows and macOS require an explicit opt-out (see [Security](#security)) |

---

## Installation

```bash
pip install "rom-hub @ git+https://github.com/BlizzHacker/rom-hub@master"
```

Or from a clone, for development:

```bash
git clone https://github.com/BlizzHacker/rom-hub
cd rom-hub
python -m pip install -e ".[dev]"
```

On Linux this also installs `pyseccomp`, which is what lets the plugin subprocess
confine itself. Without it, `rom-hub` refuses to run plugins rather than running
them unconfined.

---

## Quick start

```bash
rom-hub plugin browse                  # the published plugin catalogue
rom-hub plugin install archive-org     # clones the repo, pinned to its tag
rom-hub search "oregon trail" --limit 5
```

Searching needs no library server. `import` and `enrich` do — configure one under
[Library backends](#library-backends), then:

```bash
rom-hub import archive-org rubik_202308
rom-hub enrich openvgdb 42 --source-id 1234
```

Every install is pinned to a tag and the resolved commit SHA is recorded, so a tag
moved after the fact does not change what you have. Updating is an explicit re-run
with a new `--ref`; nothing updates itself.

---

## Command reference

### Plugins

| Command | Description |
|---|---|
| `rom-hub plugin browse` | List the catalogue |
| `rom-hub plugin install <slug\|url\|path>` | Install; `--ref` pins a branch, tag or SHA |
| `rom-hub plugin list` | Installed plugins, versions and capabilities |
| `rom-hub plugin enable\|disable <slug>` | Toggle without uninstalling |
| `rom-hub plugin uninstall <slug>` | Remove |
| `rom-hub plugin config <slug>` | Show configuration (secrets redacted) |
| `rom-hub plugin secret set <slug> <field>` | Store an API key |
| `rom-hub plugin assets <slug> [--fetch]` | Declared data assets and cache state |

### Capabilities

| Command | Description |
|---|---|
| `rom-hub search <query>` | Fan out across every enabled plugin; results merge to one row per game per platform. `--expand`, `--no-group`, `--limit`, `--offset` |
| `rom-hub import <plugin> <source_id>` | Plan → download → hash-dedup → upload → register → collection. `--platform`, `--collection` |
| `rom-hub enrich <plugin> <rom_id>` | Write name, summary, provider ids and artwork. `--source-id` |
| `rom-hub stream <plugin> <source_id>` | Resolve to a playable target. `--open`, `--json`, `--library-rom` |
| `rom-hub cores list\|install <plugin> [<core>]` | Emulator cores |
| `rom-hub firmware list\|install <plugin> [<fw>]` | BIOS files, with each one's licence |
| `rom-hub assets list\|install <plugin> [<asset>]` | Shaders, overlays, cheats, controller profiles |
| `rom-hub census build\|report\|list <plugin>` | Enumerate a whole source into a local catalogue |
| `rom-hub torrent <plugin> <source_id>` | Resolve to a `.torrent` or magnet |

### System

| Command | Description |
|---|---|
| `rom-hub backend info` | Selected backend, whether it is configured, what it can do |
| `rom-hub platforms [--installed]` | Which platforms play in the library's web player |
| `rom-hub jobs [--state FAILED]` | Job history and failure reasons |
| `rom-hub catalog add\|list <name> <url>` | Manage plugin directories |

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ROM_HUB_HOME` | `~/.rom-hub` | Plugins, job database, downloads, artwork, cores, firmware, assets |
| `ROM_HUB_BACKEND` | `romm` | `romm`, `gaseous` or `retrom` |
| `ROM_HUB_ALLOW_UNSANDBOXED` | unset | Required on Windows and macOS. Means no confinement at all |
| `ROM_HUB_SECRET_KEY` | unset | Encrypts stored plugin secrets with a key from outside the box |
| `ROM_HUB_CORES_DIR` | `$ROM_HUB_HOME/var/cores` | Where cores install |
| `ROM_HUB_FIRMWARE_DIR` | `$ROM_HUB_HOME/var/firmware` | Where BIOS files install |
| `ROM_HUB_ASSETS_DIR` | `$ROM_HUB_HOME/var/assets` | Shaders, overlays, cheats, controller profiles |
| `ROM_HUB_NO_ASSET_FETCH` | unset | Refuse to fetch plugin data assets automatically |
| `ROM_HUB_STREAM_SERVER` | unset | A `romm-stream` server to query for playability |

Point the install directories at what your emulator already reads and there is
nothing to copy afterwards:

```bash
ROM_HUB_ASSETS_DIR=~/.config/retroarch rom-hub assets install retroarch-autoconfig ...
ROM_HUB_FIRMWARE_DIR=/opt/retroarch/system rom-hub firmware install open-bios ...
```

`ROM_HUB_SHADERS_DIR`, `ROM_HUB_OVERLAYS_DIR`, `ROM_HUB_CHEATS_DIR` and
`ROM_HUB_CONTROLLERS_DIR` override a single asset kind.

---

## Library backends

| Backend | Settings | Import | Scan | Metadata | Artwork | Collections |
|---|---|:-:|:-:|:-:|:-:|:-:|
| `romm` | `ROMM_URL`, `ROMM_USER`, `ROMM_PASSWORD` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `gaseous` | `GASEOUS_URL`, `GASEOUS_USER`, `GASEOUS_PASSWORD` | ✅ | ✅ | — | — | — |
| `retrom` | `RETROM_URL` | ✅ | ✅ | ✅ | ✅ | — |

`ROM_HUB_BACKEND_URL` / `_USER` / `_PASSWORD` work for any of them. Retrom has no
accounts and reads only the URL.

```bash
rom-hub backend info      # opens no connection; reports what is configured
```

A backend that cannot do something **refuses up front** when the capability is
essential (`import`, `metadata`) and **proceeds with a reported skip** when it is an
extra (`collections`, `artwork`). An explicit `--collection` you typed is always
refused rather than silently dropped.

**Retrom** has no upload API — files land over its WebDAV service, so the content
directory must be inside `RETROM_DATA_DIR` (`/app/data` in the official image), and
a platform directory must exist before importing. **Gaseous** derives platform from
the file signature rather than the requested id, and its rom records are read-only.
Details and measurements: [docs/BACKENDS.md](docs/DESIGN.md) and
[docs/PROOF.md](docs/PROOF.md).

---

## Plugin catalogue

`rom-hub plugin browse` lists everything installable. Full directory with
per-plugin permissions and source terms: [docs/PLUGINS.md](docs/PLUGINS.md).

| Capability | Plugins |
|---|---|
| `search` / `importer` | `archive-org`, `nointro-archive`, `demozoo`, `aminet`, `if-archive`, `itch-io`, `scummvm-freeware`, `homebrew`, `libretro-content`, `universal-db` |
| `metadata` | `hasheous`, `openvgdb`, `libretro-database`, `libretro-thumbnails`, `retroachievements`, `ludusavi` |
| `cores` | `emulators`, `libretro-cores` |
| `assets` | `retroarch-autoconfig`, `libretro-overlays`, `libretro-cheats` |
| `firmware` | `open-bios` |

### Third-party catalogues

The Hub reads an ordered list of directories, so anyone can publish plugins without
going through this repository:

```bash
rom-hub catalog add mine https://example.com/plugins.json
rom-hub catalog list                   # what is configured, and its health
```

`https` URLs and local paths only. The bundled directory is always first and **first
source wins** — a third-party directory can add plugins but never replace one this
project ships, and collisions are printed rather than silently resolved. A directory
**grants nothing**: what a plugin may reach comes from its own `manifest.toml`.

See *Publishing your own catalog* in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Plugin credentials

A plugin that needs an API key declares the field as `type = "secret"`. The Hub keeps
it out of `state.json` and redacts it from every command's output.

```bash
rom-hub plugin secret set retroachievements api_key      # prompts, nothing echoed
pass show ra | rom-hub plugin secret set retroachievements api_key --stdin
rom-hub plugin secret set retroachievements api_key --env RA_KEY
rom-hub plugin secret list                               # what is set, and where
```

| Store | Protection |
|---|---|
| OS keyring | Whatever the OS provides |
| File + `ROM_HUB_SECRET_KEY` | Encryption at rest; the key comes from outside the box |
| File, generated key *(default)* | Obfuscation only — the key sits beside the ciphertext. Keeps the value out of `state.json`, config dumps, screenshots and commits |

Set `ROM_HUB_SECRET_KEY` from a Docker secret or systemd credential for real
encryption at rest.

---

## Security

Plugins run as subprocesses with **no library token, no filesystem mount and no
socket API**. Network access goes through an RPC the host checks against the
plugin's declared `network` allowlist. Every URL a plugin returns — fetch plans,
artwork, stream targets, core and firmware downloads — passes the same check.

| Control | Status |
|---|---|
| Network egress | **Enforced.** A self-imposed seccomp filter denies `socket`, `connect`, `sendto`, `sendmsg` before any plugin code is imported. Works inside default Docker with no added capabilities |
| Process spawn | **Enforced.** `execve` and `execveat` denied. Forked children inherit the filter |
| Environment inheritance | **Enforced.** The child environment is built from `{}` and allowlisted — measured 92 variables down to 7 |
| Path traversal | **Enforced.** One filename validator and one containment check for every file the Hub writes |
| Arbitrary file read | **Not enforced.** seccomp cannot filter on a path; confining reads needs a mount namespace, which default Docker denies |

> **Install only plugins you trust.** A plugin cannot reach an undeclared host or
> exec its way out, but it runs with the Hub's own file-read reach — it can read any
> file the Hub process can, including the Hub's own configuration.

On Windows and macOS no confinement is available and the Hub **fails closed**.
Setting `ROM_HUB_ALLOW_UNSANDBOXED=1` lifts the refusal and means exactly what it
says. It is a development convenience, not a deployment setting.

Full model, including what is deliberately not claimed:
[docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model).

---

## Using it from ROMarr

Installed alongside [ROMarr](https://github.com/BlizzHacker/romarr), the same
catalogue is a **Hub → Plugins** tab — every plugin with its capabilities,
platforms and network reach, and one-click install, enable and disable. ROMarr
drives this package through its Python API, so plugins installed there are the
sources ROMarr searches and imports from.

![ROM Hub plugins in ROMarr](https://raw.githubusercontent.com/BlizzHacker/romarr/main/docs/img/hub-plugins.png)

```bash
pip install "rom-hub @ git+https://github.com/BlizzHacker/rom-hub@master"
```

---

## Development

```bash
python -m pytest              # offline; live tests deselected
python -m pytest -m live      # also hits the real Archive.org
```

1461 tests across Linux and Windows, Python 3.12 and 3.13. Branch coverage is 86.6 %
on Linux and 86.9 % on Windows.

CI additionally asserts two things a green exit code does not prove: that the seccomp
containment tests **passed** rather than skipped on Linux, and that the
network-hitting tests are still excluded by default.

[`scripts/proof_matrix.py`](scripts/proof_matrix.py) runs the real import and enrich
pipelines against live RomM, Gaseous and Retrom servers and writes
[docs/PROOF.md](docs/PROOF.md), keeping **UNSUPPORTED** distinct from **FAIL**.
[`scripts/proof-stack.compose.yml`](scripts/proof-stack.compose.yml) stands up the
three disposable servers.

- [CONTRIBUTING.md](CONTRIBUTING.md) — writing a plugin and getting it listed
- [docs/DESIGN.md](docs/DESIGN.md) — architecture and the broker model
- [docs/PLUGINS.md](docs/PLUGINS.md) — the plugin directory
- [docs/SHOWCASE.md](docs/SHOWCASE.md) — a worked tour

### Renamed from `romm-hub`

The project, its packages and its `ROMM_HUB_*` variables lost a letter; the host is
no longer about one library server. The plugin contract did not change —
`rpp_version = "1"` is still correct and must not be bumped.

| Was | Is | Old name still works |
|---|---|:-:|
| `romm-hub` (CLI, project) | `rom-hub` | no — reinstall |
| `romm_hub`, `romm_hub_sdk` | `rom_hub`, `rom_hub_sdk` | yes, deprecated |
| `ROMM_HUB_HOME` | `ROM_HUB_HOME` | yes, deprecated |
| `ROMM_HUB_ALLOW_UNSANDBOXED` | `ROM_HUB_ALLOW_UNSANDBOXED` | yes, deprecated |
| `ROMM_HUB_CORES_DIR` | `ROM_HUB_CORES_DIR` | yes, deprecated |

`ROMM_URL`, `ROMM_USER` and `ROMM_PASSWORD` were **not** renamed — they are RomM's
own names and configure one backend among several.

---

## Cartridge ecosystem

ROM Hub is the plugin layer of **Cartridge**, a self-hosted retro-gaming stack by
MoveWeight.

| | Project | Purpose |
|---|---|---|
| **Acquire** | [ROMarr](https://github.com/BlizzHacker/romarr) | Request, find, grab, file |
| | [ROM Hub](https://github.com/BlizzHacker/rom-hub) | Plugin host — the sources ROMarr searches |
| **Play** | [Desktop](https://github.com/BlizzHacker/RommForDesktop) · [Xbox](https://github.com/BlizzHacker/RommForXbox) · [Roku](https://github.com/BlizzHacker/RommForRoku) | Clients |
| | [Stream Server](https://github.com/BlizzHacker/RommStreamServer) | Remote play |

Brand and naming: [BRAND.md](BRAND.md).

## Licence

MIT — see [LICENSE](LICENSE). Each plugin is a separate work under its own licence,
carried in its own repository.

Unofficial. Not affiliated with or endorsed by the RomM, Gaseous or Retrom projects.
