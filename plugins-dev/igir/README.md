# Igir (1G1R Collection Manager) plugin for ROM Hub

Implements the RPP v1 `cores` capability for [**Igir**](https://github.com/emmercm/igir) (`v5.8.0+`) — Christian Emmer's zero-setup ROM collection manager that sorts, filters (`--single` 1G1R), extracts, patches, and verifies ROMs against No-Intro, Redump, TOSEC, and MAME DATs — plus a universal 1G1R command & cleanup-script planner (`igir.planner`) for **RomM**, **Gaseous**, **Retrom**, **ES-DE**, and **Batocera** libraries.

## Credits

- **Igir (`emmercm/igir`)**: Created and maintained by [Christian Emmer (`emmercm`)](https://github.com/emmercm/igir), licensed under `GPL-3.0`. Release v5.8.0 adds BSDiff v4, Paper Mario Star Rod (PMSR) N64 patching, Xdelta3 LZMA secondary compression, and native `{romm}`, `{es}`, `{batocera}`, `{mister}`, and `{pocket}` output tokens.
- **RomM 1G1R Cleanup Workflow**: Inspired by the [RomM Igir cleanup script documentation](https://docs.romm.app/latest/ecosystem/igir/#the-cleanup-script) and community workflow suggestions from **Ant#1695** to make ROMarr and ROM Hub a universal 1G1R front-end across RomM, Gaseous, and Retrom.

## Install

```bash
rom-hub plugin install igir
rom-hub cores list igir
rom-hub cores install igir igir
```

## Targets

| `target` | Upstream Release Asset (v5.8.0) |
|---|---|
| `linux/x86_64` | `igir-5.8.0-Linux-amd64.tar.gz` |
| `linux/aarch64` | `igir-5.8.0-Linux-arm64v8.tar.gz` |
| `windows/x86_64` | `igir-5.8.0-Windows-x64.zip` |
| `windows/arm64` | `igir-5.8.0-Windows-arm64.zip` |
| `macos/x86_64` | `igir-5.8.0-macOS-x64.tar.gz` |
| `macos/arm64` | `igir-5.8.0-macOS-arm64.tar.gz` |

## 1G1R Command & Cleanup Planner (`igir.planner`)

ROMarr (`/api/v1/hub/igir`) and standalone callers can also import `igir.planner` to build deterministic 1G1R commands or generate the multi-backend RomM/Gaseous/Retrom cleanup script:

```python
from igir.planner import build_1g1r_command, render_cleanup_script

cmd = build_1g1r_command(
    input_dirs=["/downloads/rom-hub"],
    output_dir="/library/roms/{romm}",
    dat_dirs=["/config/dats"],
    single_1g1r=True,
    prefer_regions=["USA", "WORLD", "EUR", "JPN"],
)
```
