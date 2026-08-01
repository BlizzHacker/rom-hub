# ROM Hub

> Part of **[Cartridge](https://github.com/BlizzHacker/rom-hub/blob/master/BRAND.md)** by MoveWeight — the plugin layer beneath **ROMarr**, Cartridge's acquisition pillar. Unofficial; not affiliated with RomM, Gaseous or Retrom.

[![CI](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml)
[![coverage 87%](https://img.shields.io/badge/coverage-87%25-brightgreen)](#coverage)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)
[![licence MIT](https://img.shields.io/github/license/BlizzHacker/rom-hub)](LICENSE)

The suite is 1461 tests and it runs on every push, on Linux and Windows, on
Python 3.12 and 3.13. On Linux the seccomp confinement tests must *pass* — CI
fails if they merely skip, because a skipped containment test looks exactly
like a passing one. [docs/PROOF.md](docs/PROOF.md) is a generated matrix of
what actually works against a live server of each of the three backends, cell
by cell, with the evidence for each; [Coverage](#coverage) has the honest
numbers and says which one of them is misleading and why.

**A plugin host for self-hosted ROM library managers.** It runs beside
[RomM](https://github.com/rommapp/romm),
[Gaseous](https://github.com/gaseous-project/gaseous-server) or
[Retrom](https://github.com/JMBeresford/retrom) as a sidecar and never modifies
the library server. Plugins add sources — searching them, importing from them,
enriching what you already have — to a server that has no plugin system of its
own.

**A plugin is backend-agnostic, and that is structural rather than a promise.**
A plugin never talks to a library server and holds no credential for one. It
returns a *description* of work — which files to fetch, which metadata to set,
where an item can be streamed — and the Hub executes that description against
whichever server `ROM_HUB_BACKEND` selects. Nothing inside a plugin has ever
known which of the three is on the other side, so a plugin written against one
works against all of them, as far as that server is capable (`rom-hub backend
info` says what the chosen one can do).

The same shape is what makes untrusted plugins tractable: a plugin runs as its
own subprocess with no token, no filesystem mount and no sockets, and reaches
the network only through an RPC the host checks against the allowlist that
plugin declared. See [Security model](#security-model) — including, plainly,
what is *not* confined.

- **[docs/PLUGINS.md](docs/PLUGINS.md)** — the plugin directory: seven
  published plugins, what each one asks for, and the terms of the source it
  reads from.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to write a plugin and get it
  listed.
- **[docs/DESIGN.md](docs/DESIGN.md)** — the architecture.
- **[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md)** —
  deferred federation and multiplayer work.

MIT licensed; see [LICENSE](LICENSE). Each plugin is a separate work under its
own licence, carried in its own repository.

## It works — here is what that looks like

![RomM's Game Gear shelf, box art written by a ROM Hub plugin](docs/screenshots/romm.png)

Imported by the `nointro-archive` plugin, box art written by the
`libretro-thumbnails` plugin, both installed by slug from their public repos.
Nothing was hand-copied and no database was written directly.

And the plugin system itself, which is the actual point:

![Turning a plugin off and back on](docs/screenshots/cli-plugin-toggle.png)

Disable one and it drops out of the fan-out — the source count falls — and any
command aimed at it refuses and names the command that undoes it.

**[docs/SHOWCASE.md](docs/SHOWCASE.md)** is the sixty-second tour: what a plugin
is, the sandbox, the nine capabilities, all three backends, the fan-out search
and an import running end to end. It also carries the honest half — the real
cover-art ratio, which roms RomM's player cannot actually run, and the import
batch that lost roms to a scan race.

## Quick start

    git clone https://github.com/BlizzHacker/rom-hub
    cd rom-hub
    python -m pip install -e ".[dev]"

    rom-hub plugin browse                  # the seven published plugins
    rom-hub plugin install archive-org     # clones the repo, pinned to its tag
    rom-hub search "oregon trail" --limit 5

`plugin install` takes a catalog slug, a git URL, or a local path. A slug is
resolved through [`catalog/plugins.json`](catalog/plugins.json), which supplies
the repository **and** the tag, so these two are the same install:

    rom-hub plugin install archive-org
    rom-hub plugin install https://github.com/BlizzHacker/rom-hub-archive-org --ref v0.2.0

Every install is pinned to a tag and the resolved commit SHA is recorded, so a
tag moved after the fact does not change what you have. Updating is an explicit
re-run with a new ref; nothing updates itself.

**That directory is not the only one.** The Hub reads an ordered list of them,
so anybody can publish plugins without going through this repository:

    rom-hub catalog add mine https://git.moveweight.com/wade/rom-hub-catalog/raw/branch/main/plugins.json
    rom-hub catalog list                   # what is configured, and its health

https URLs and local paths only. The bundled directory is always first and
**first source wins**, so a third-party directory can add plugins but never
replace one this project ships — and the collision is printed rather than
silently resolved. `plugin browse` marks each entry with the directory it came
from and reports `N of M catalog(s) reachable` when one cannot be read, so a
source that is down looks like a source that is down rather than like plugins
that do not exist. A directory still **grants nothing**: what an installed
plugin may reach comes from its own `manifest.toml`. See *Publishing your own
catalog* in [`CONTRIBUTING.md`](CONTRIBUTING.md).

**Searching needs no library server at all** — it fans out across installed
plugins and prints results. `import` and `enrich` are the commands that need
one configured.

On Linux the install also pulls `pyseccomp`, which is what lets the plugin
subprocess confine itself. If it is missing, `rom-hub` refuses to run plugins
rather than running them unconfined. On **Windows and macOS there is no
confinement available at all**, and plugins refuse to run without

    ROM_HUB_ALLOW_UNSANDBOXED=1

which means exactly what it says. See [Security model](#security-model).

## Renamed from `romm-hub`

The project, its packages and its `ROMM_HUB_*` environment variables lost a
letter, because the host is no longer about one library server. Nothing in the
plugin contract changed with it.

| Was | Is | Old name still works? |
|---|---|---|
| `romm-hub` (CLI, project) | `rom-hub` | no — reinstall |
| `romm_hub`, `romm_hub_sdk` | `rom_hub`, `rom_hub_sdk` | **yes**, deprecated |
| `ROMM_HUB_HOME` | `ROM_HUB_HOME` | **yes**, deprecated |
| `ROMM_HUB_ALLOW_UNSANDBOXED` | `ROM_HUB_ALLOW_UNSANDBOXED` | **yes**, deprecated |
| `ROMM_HUB_CORES_DIR` | `ROM_HUB_CORES_DIR` | **yes**, deprecated |
| "RomM Provider Protocol" | "**ROM** Provider Protocol" | acronym unchanged |

**`rpp_version = "1"` is still correct and must not be bumped.** The protocol
was renamed, not revised: the acronym, the capability names, the wire format
and every validation rule are byte-for-byte what they were. A manifest written
last week needs no edit.

**For plugin authors, one line changes:** `from romm_hub_sdk import ...` becomes
`from rom_hub_sdk import ...`. The old import still resolves — to the *same*
objects, so `isinstance` still holds — and warns. It will be removed.

`ROMM_URL`, `ROMM_USER` and `ROMM_PASSWORD` were **not** renamed. They are
RomM's name, not the Hub's, and they configure one backend among several.

## Status

**RPP v1 is fully implemented.** All nine capabilities have a host
implementation and a CLI command:

| Capability | Command | What it does |
|---|---|---|
| `search` | `rom-hub search <query>` | fans out across every enabled plugin, then merges the results into one row per game per platform -- variants and cross-source duplicates collapse behind a count, `--expand <#>` opens one, `--no-group` turns it off. `--limit`/`--offset` page the merged set |
| `importer` | `rom-hub import <plugin> <source_id>` | plan → download → hash-dedup → upload → register → collection, warning first if the platform has no emulator core |
| `metadata` | `rom-hub enrich <plugin> <rom_id>` | plugin describes a name, a summary, provider ids and artwork; the Hub fetches the cover, asks the backend which ids it will accept, and writes what survives |
| `stream` | `rom-hub stream <plugin> <source_id>` | resolves one item to a validated target and hands it over — prints what to do with it, `--open`s it, or emits it as JSON |
| `cores` | `rom-hub cores list\|install <plugin> [<core>]` | lists a plugin's emulator cores, downloads one |
| `firmware` | `rom-hub firmware list\|install <plugin> [<firmware>]` | lists a plugin's BIOS files **with each one's licence**, installs one to disk and to the library |
| `assets` | `rom-hub assets list\|install <plugin> [<asset>]` | lists a plugin's shaders, overlays, cheats and controller profiles **with each one's licence**, installs one to disk. No library involved |
| `census` | `rom-hub census build\|report\|list <plugin>` | enumerates a whole source into a local catalogue and states its coverage against the source's **own** declared total, per unit -- `29,955 of 29,955 declared entries across 43 units; 28 units excluded`, each exclusion named. Resumable; search is then served from it |
| `torrent` | `rom-hub torrent <plugin> <source_id>` | resolves one item to a `.torrent` URL or magnet, reads the torrent as a verified file manifest, and prints it, hands it to the client you already run, or pulls one named file from the torrent's own https web seed and checks it against the torrent's own digest. Links no BitTorrent client |

Plus the broker, a seccomp-confined plugin subprocess, and a job queue that
survives a restart. No web UI yet.

> The rest of this README (backends, importing, enriching, streaming, cores,
> firmware, assets, security model) is unchanged — see the sections below.

## Which library server

`ROM_HUB_BACKEND` selects it; `romm` is the default. Three ship:
[RomM](https://github.com/rommapp/romm),
[Gaseous](https://github.com/gaseous-project/gaseous-server) and
[Retrom](https://github.com/JMBeresford/retrom).

| Backend | Settings | Can | Cannot |
|---|---|---|---|
| `romm` | `ROMM_URL`, `ROMM_USER`, `ROMM_PASSWORD` | import, scan, metadata, artwork, collections | — |
| `gaseous` | `GASEOUS_URL`, `GASEOUS_USER`, `GASEOUS_PASSWORD` | import, scan | collections, metadata, artwork |
| `retrom` | `RETROM_URL` | import, scan, metadata, artwork | collections |

`ROM_HUB_BACKEND_URL`/`_USER`/`_PASSWORD` also work for any of them, for a
deployment that would rather not name a product in its unit file. Retrom has no
accounts, so it reads only the URL.

    rom-hub backend info

**It opens no connection.** The person most likely to run it is the one whose
connection is not working yet.

## Security model

Plugins run as subprocesses and are given **no RomM token and no filesystem
mount**, and the plugin API offers no way to open a socket. A plugin calls
`ctx.http`, which is an RPC back to the host; the host checks the URL against
the plugin's declared `network` allowlist before opening any connection.

> ### ⚠️ Only install plugins you trust
>
> An untrusted plugin can no longer reach an undeclared host and can no longer
> exec its way out. It still runs with the Hub's own file-read reach: **a
> plugin can read any file the Hub process can read.** See
> [docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model) for the full model,
> including what is *not* confined.

The full security model — seccomp egress enforcement, the environment allowlist,
what is and isn't confined and why — is in
[docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model). It has not been
trimmed, only moved out of the README's front matter so the brand header reads
cleanly.
