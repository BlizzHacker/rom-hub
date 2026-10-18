<div align="center">

# 🧩 ROM Hub

**Add new game sources to your retro library — without touching your library server.**

ROM Hub is the plugin engine behind [ROMarr](https://github.com/BlizzHacker/romarr).
Install a plugin and your library can search, import and enrich games from a new
place. It runs beside **RomM**, **Gaseous** or **Retrom** and never changes them.

[![CI](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/BlizzHacker/rom-hub/actions/workflows/ci.yml)
[![licence MIT](https://img.shields.io/github/license/BlizzHacker/rom-hub)](LICENSE)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](pyproject.toml)

[Quick start](#-quick-start) · [Plugins](docs/PLUGINS.md) · [60-second tour](docs/SHOWCASE.md) · [Write a plugin](CONTRIBUTING.md) · [Technical docs](docs/TECHNICAL.md)

![Box art and games imported into RomM by ROM Hub plugins](docs/screenshots/romm.png)

</div>

## 🎮 The family: pick what you need

Three projects that work alone and work better together.

| | What it does | Start here if you want… |
|---|---|---|
| **[Yarr.It](https://yarrit.com)** | One friendly front door for your whole media library: search, stream, play in the browser and on your TV. | a place to *use* your library |
| **[ROMarr](https://github.com/BlizzHacker/romarr)** | The *arr for games. Ask for a game, and it finds it, downloads it and files it into your library, just like Radarr does for movies. | your library to *fill itself* |
| **ROM Hub** *(you are here)* | The plugin engine. Each plugin adds a new source of games, box art or metadata to ROMarr and your library. | *more sources* than anyone ships by default |

```
Yarr.It ──▶ ROMarr ──▶ ROM Hub plugins ──▶ RomM / Gaseous / Retrom
 (watch &     (request &   (new sources,         (your existing
  play)        fetch)       art, metadata)        library server)
```

**Most people should start with ROMarr.** Its *Hub* tab installs and manages
these plugins for you, no command line needed. Use ROM Hub directly when you
run a library server without ROMarr, or when you want to write plugins.

## ✨ What you get

- **More sources, one search.** Search every installed plugin at once and import what you pick.
- **Better-looking libraries.** Plugins fill in box art and metadata your server is missing.
- **Works with your server.** RomM, Gaseous or Retrom. A plugin written once works with all three.
- **Safe by design.** Each plugin runs locked in its own sandbox: no passwords, no file access, and only the websites it declared.
- **Nothing updates behind your back.** Every plugin is pinned to a release; you choose when to upgrade.

## 🚀 Quick start

You need **Python 3.12+**.

```bash
git clone https://github.com/BlizzHacker/rom-hub
cd rom-hub
python -m pip install -e .

rom-hub plugin browse                 # see what's available
rom-hub plugin install archive-org    # add a source
rom-hub search "oregon trail"         # search across your sources
```

Searching works straight away. To **import** into your library, point ROM Hub
at your server (RomM, Gaseous or Retrom). [Which library server](docs/TECHNICAL.md#which-library-server)
covers each one.

> **Windows / macOS:** the plugin sandbox is Linux-only. Elsewhere, plugins
> run only if you opt in with `ROM_HUB_ALLOW_UNSANDBOXED=1`.
> [Why, and what that means](docs/TECHNICAL.md#security-model).

## 🔌 Plugins

Browse the directory in **[docs/PLUGINS.md](docs/PLUGINS.md)**: what each
plugin does, what it is allowed to reach, and the terms of the site it reads
from. Want your own directory? `rom-hub catalog add <name> <url>` adds one.

## 🛠️ Build a plugin

A plugin is a small Python package that describes work, such as "fetch these
files" or "set this cover", and ROM Hub carries it out safely against whichever
server you run. **[CONTRIBUTING.md](CONTRIBUTING.md)** walks you through
writing one and getting it listed.

## 📚 Going deeper

Everything technical lives in **[docs/TECHNICAL.md](docs/TECHNICAL.md)**: backends compared,
importing, metadata, streaming, emulator cores and BIOS, the security model
and exactly what it does and doesn't confine, test coverage, and the
[proof matrix](docs/PROOF.md) against live servers.

## 💬 Help & community

Found a bug or have an idea? [Open an issue](https://github.com/BlizzHacker/rom-hub/issues).
ROM Hub is an independent, unofficial project and is not affiliated with RomM,
Gaseous or Retrom. MIT licensed.
