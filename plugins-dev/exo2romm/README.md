# eXo2romm (eXoDOS to RomM Repackager) plugin for ROM Hub

Repackages [eXoDOS](https://www.retro-exo.com/exodos.html) game `.zip` archives so they launch standalone inside [RomM](https://github.com/rommapp/romm) and **ROMarr** without breaking pack-root `.\eXoDOS\<GameDir>\...` DOSBox mount paths.

## Credits & Inspiration

- **Original Creator:** [**derkommutator**](https://github.com/derkommutator/eXo2romm) ([`derkommutator/eXo2romm`](https://github.com/derkommutator/eXo2romm), released into the public domain under **The Unlicense**).
- **Why this plugin exists:** This plugin was inspired to assist with more users having this workflow directly inside ROM Hub and ROMarr — cross-platform in pure Python (Linux, Alpine/Debian Docker, macOS, and Windows, without requiring `bash`, `awk`, or `zip`/`unzip` binaries) and integrated cleanly into the ROMarr **Hub → Plugins** UI.

## What it does

For each input eXoDOS game `.zip`:

1. Validates the archive unzips cleanly into a single top-level directory `<top>` (with zip-slip/path-traversal checks).
2. Reads the matching template `<dos_dir>/<top>/dosbox.conf` from the eXoDOS `!dos` directory tree.
3. On `[autoexec]` lines whose first keyword is `mount`, `imgmount`, or `boot` (with optional leading `@`, case-insensitive), rewrites literal `.\eXoDOS\` references:
   - `.\eXoDOS\<top>\...` → `.\...`
   - `.\eXoDOS\<top>` → `.\`
   - Bare `.\eXoDOS\` → `.\`
   - Comments (`#`, `rem`), other lines, and paths targeting a different directory (`.\eXoDOS\<other>\...`) are preserved untouched, along with original CRLF/LF line endings.
4. Clones the input `.zip` into `output_dir` (never mutating the original `.zip`) with `<top>/dosbox.conf` cleanly grafted/replaced with zero duplicate central-directory entries, atomic staging, and resume-safe skipping when an output archive already exists.

## Install via ROM Hub

```bash
rom-hub plugin install exo2romm
rom-hub cores list exo2romm
rom-hub cores install exo2romm exo2romm
```

Or use it directly from **ROMarr → Hub → Plugins** (`/#hub`) or `/api/v1/hub/exo2romm`.
