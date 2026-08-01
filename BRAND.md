# Cartridge — brand & hierarchy

**Cartridge** is a self-hosted retro-gaming ecosystem by **MoveWeight**. It has
two pillars, and a plugin layer beneath one of them:

```
MoveWeight                         the parent
└── Cartridge                      the product family — "Cartridge by MoveWeight"
    ├── ROMarr                     Pillar 1 — acquisition (the *arr for games)
    │   └── ROM Hub + plugins      ROMarr's plugin layer (backend-agnostic sources)
    └── Cartridge apps             Pillar 2 — play (Desktop · Xbox · Roku · Stream)
```

- **MoveWeight** — the parent brand. Everything is *by MoveWeight*.
- **Cartridge** — the umbrella product. Tagline: **"Cartridge by MoveWeight."**
- **ROMarr** *(keeps its name)* — the acquisition pillar: request a ROM, ROMarr
  finds it, grabs it, files it into your library. Positioned as
  **"ROMarr — part of Cartridge, by MoveWeight."**
- **ROM Hub + its plugins** — the backend-agnostic plugin system that powers
  ROMarr's sources. Positioned as **"a Cartridge project."** Technical names
  (`rom-hub`, `rom-hub-*`, the RPP protocol) are unchanged — this is branding,
  not a rename.
- **Cartridge apps** — the play pillar: the Desktop, Xbox, Roku clients and the
  stream server. Each is **"Cartridge — RomM for <platform>"** (RomM used only
  descriptively, for search — see *Third-party note* below).

## Third-party note (required on every repo)

Cartridge, ROMarr and ROM Hub are **unofficial** and are **not affiliated with
or endorsed by** the [RomM](https://romm.app), Gaseous or Retrom projects. They
interoperate with those servers; they are not those projects. Per RomM's brand
guidelines, "RomM" is never used as the name of any Cartridge product — only
descriptively (e.g. "a client for RomM").

## Voice

Plain, honest, second-person. Say what a thing does. Never claim something works
that hasn't been proven. Never imply endorsement by an upstream project.

## Palette

| token   | hex       | use                                   |
|---------|-----------|---------------------------------------|
| bg      | `#0A0E1A` | ground                                |
| accent  | `#7DD3FC` | Cartridge cyan — links, focus, brand  |
| warm    | `#FDB44B` | "plays locally" / highlight           |
| good    | `#4ADE80` | success                               |
| bad     | `#FF7B72` | error                                 |

## Badge

Every repo in the ecosystem carries this line under its title:

```markdown
> Part of **[Cartridge](https://github.com/BlizzHacker/rom-hub/blob/master/BRAND.md)** by MoveWeight — a self-hosted retro-gaming ecosystem. Unofficial; not affiliated with RomM, Gaseous or Retrom.
```

This BRAND.md is the single source of truth for the hierarchy; link to it rather
than restating it.
