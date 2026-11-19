# rom-hub-compressatorium

A [ROM Hub](https://github.com/BlizzHacker/rom-hub) plugin that connects [ROMarr](https://github.com/BlizzHacker/romarr)'s batch decompression to [Compressatorium](https://github.com/pacnpal/compressatorium), pacnpal's self-hosted tool for converting and checking game images (CHD, RVZ, Z3DS, NSZ, CSO and more).

ROMarr ships with this plugin preinstalled. It installs it once, on the first start with ROM Hub available. If you disable it in **Hub → Plugins** (or remove it by hand), ROMarr leaves it that way.

## What it does

- **Tells ROMarr where Compressatorium is.** Set `url` (for example `http://compressatorium:8080`) and, if your Compressatorium has authentication on, `api_key`. ROM Hub keeps the key in its secret store. The `COMPRESSATORIUM_URL` and `COMPRESSATORIUM_API_KEY` environment variables still work and take precedence.
- **Offers Compressatorium's compose file.** The `cores` entry lists Compressatorium's latest release. Installing it fetches that release's `docker-compose.yml` from pacnpal's repository.

ROMarr does the work itself. When you run **System → Tasks → Decompress**, ROMarr:

- sends CHD, RVZ/WIA/GCZ, NKit, Z3DS, NSZ, CSO/ZSO/DAX and WUX files, and a `.7z`/`.zip` holding one cartridge ROM, to Compressatorium;
- reads each CHD's header so chdman gets the right extract mode (CD, DVD, hard disk, LaserDisc or raw);
- skips anything whose Compressatorium tool is unavailable, such as NSZ without `prod.keys`;
- extracts RAR sets and multi-file archives itself;
- checks every output against your DATs, and deletes an original only when you asked and it verified.

The plugin never connects to Compressatorium. ROM Hub plugins run in a sandbox that may only make HTTPS requests to the hosts in `manifest.toml` (`api.github.com`, `raw.githubusercontent.com`), and a container on your network is not one of them.

## Setting up Compressatorium

1. Install the plugin's `compressatorium-compose` core, or copy the compose file from [Compressatorium's repository](https://github.com/pacnpal/compressatorium).
2. Mount your ROM library into Compressatorium **at the same path ROMarr uses**. Both programs pass file paths to each other, so a library at `/roms` in ROMarr must be `/roms` in Compressatorium too.
3. Start it, then set this plugin's `url` in **Hub → Plugins**.

## Credits and licence

[Compressatorium](https://github.com/pacnpal/compressatorium) is by pacnpal. It does not publish a licence, so this plugin includes none of its code: it only stores connection settings and points at the upstream compose file, which is downloaded from pacnpal's repository when you ask for it.

This plugin is MIT-licensed, Copyright (c) 2026 MOVE WEIGHT. See [LICENSE](LICENSE).

## Development

```bash
git clone https://github.com/BlizzHacker/rom-hub ../rom-hub
python -m pytest tests -q
```
