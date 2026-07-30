"""Installing the emulator support files a plugin described.

    plugin.assets() -> AssetArtifact[]
      -> plugin.asset_plan(item) -> FetchPlan
      -> host downloads into <dir for item.kind>/<plugin slug>/

Shaders, bezels and overlays, cheat files, controller profiles. The
largest unserved part of a real emulation stack: `cores` gets you an
emulator and `firmware` gets you a BIOS, and neither of them is why a
twenty-year-old game looks wrong on a modern panel.

## Why this module is not called `assets.py`

Because that name was taken, by something genuinely different, and
conflating the two would be worse than an awkward filename.

`rom_hub.assets` is **plugin data assets**: a dataset a plugin declares in
`[[data_assets]]` and the host fetches *for the plugin's own use*, cached
in `var/plugin-data/<slug>/` and handed back as a path the plugin opens.
OpenVGDB's SQLite database is the case it exists for.

`rom_hub.emuassets` -- this module -- is the **`assets` capability**: files
the host fetches *for the operator's emulator*, which the plugin never
sees. The plugin is a catalogue and a URL, nothing more.

The CLI keeps them apart the same way: `rom-hub plugin assets <slug>`
reports the former, `rom-hub assets list <plugin>` lists the latter.

## Why `[[data_assets]]` is the wrong mechanism here

It looks close -- manifest-declared, sha256-verified, host-fetched, cached,
bounded at 128 MiB -- and it is wrong for the same four reasons
`rom_hub.firmware` gives, each of which bites harder here.

* **A data asset is the plugin's own file.** It lands in the plugin's data
  directory and the plugin opens it. An overlay is for RetroArch, and the
  plugin never touches it.
* **A data asset is fetched for every command.** `ensure_assets` runs
  before the subprocess starts, on `search` as much as on anything else.
  Nobody running `rom-hub search` should be pulling bezels.
* **The set is fixed at install time, and capped at 8.** These catalogues
  are 310 overlays, 437 controller profiles for one input driver, and
  2,265 cheat files for the NES alone. Eight manifest entries do not
  describe them; they are chosen one at a time by an operator reading a
  catalogue, which is the `cores install <plugin> <item>` shape.
* **The mandatory sha256 pins the manifest to an upstream commit.** These
  repositories take contributions continuously. A digest per file would
  mean a plugin release every time somebody upstreams a pad.

So: `FetchPlan`, like `cores` and `firmware`.

## Size is the design problem, and it is solved by never cloning

The sources behind this capability are enormous -- libretro-database is
795 MB, `common-overlays` is 29 MB, and the two shader repositories that
were dropped on licensing are 139 MB and 56 MB. **Nothing here downloads a
repository.** The rule for a plugin is: list from an *index*, fetch one
*file*.

For the three plugins that ship with this, that index is GitHub's Git
Trees API, which answers one directory at a time
(`/git/trees/<ref>:<path>`) with a compact JSON list carrying each blob's
path and size. One 12 KB call enumerates libretro-database's 44 cheat
platforms; one 704 KB call enumerates all 2,265 NES cheat files. The
install then fetches exactly one file from `raw.githubusercontent.com` --
a 512-byte `.cht`, a 1.7 KB `.cfg`.

Two traps found while building this, recorded so the next author does not
re-find them. The **contents API silently truncates at 1,000 entries**:
`/contents/cht/Nintendo - Nintendo Entertainment System` returns exactly
1,000 of 2,265 files with no error and no flag, so a plugin built on it
would quietly offer a third of the catalogue. The Trees API returns all
2,265 and sets `truncated: false` when it means it. And the Trees API
response for one directory is *smaller* than the truncated contents
response for the same directory (704 KB against 1.4 MB), because it
carries no per-entry URLs.

## Where the bytes go

One place, and that is the whole point of this capability: the operator's
disk, in the directory their emulator reads. `kind` selects which:

    shader     -> <assets root>/shaders
    overlay    -> <assets root>/overlays
    cheat      -> <assets root>/cheats
    controller -> <assets root>/autoconfig

Then `<that>/<plugin slug>/`, one directory per plugin, so two plugins
shipping a `nes.cfg` cannot overwrite each other.

Those leaf names are RetroArch's own, deliberately, so that pointing
`ROM_HUB_ASSETS_DIR` at an existing RetroArch configuration directory puts
every file exactly where RetroArch already looks. That is a *default an
operator can adopt*, not a path compiled in: `ROM_HUB_ASSETS_DIR` moves
the root and `ROM_HUB_SHADERS_DIR`, `ROM_HUB_OVERLAYS_DIR`,
`ROM_HUB_CHEATS_DIR` and `ROM_HUB_CONTROLLERS_DIR` each move one kind
outright, for the setup whose cheats and shaders do not live under one
parent. A plugin can influence none of them.

## No backend, at all

This is the first RPP capability that never touches a library server.
`cores` does not either, but `cores` is a lone exception in a codebase
where everything else ends in an upload; `assets` makes it a category. No
function in this module accepts a `backend` argument, opens one, or
imports `rom_hub.backends` -- so there is no capability to require, none to
degrade, and no `SkippedStep` this can ever produce. See
`backends/base.BACKEND_INDEPENDENT_CAPABILITIES` for what that means for
the essential/optional scheme, and `docs/DESIGN.md` for why it is worth
saying out loud.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rom_hub.paths import UnsafeDestination, dest_in_job_dir
from rom_hub.types import KNOWN_ASSET_KINDS, AssetArtifact

#: A shader preset is kilobytes, a bezel is a PNG, a cheat file is bytes.
#: Nothing here is a multi-gigabyte ROM the operator asked for by name, so
#: unlike an import -- where `HttpDownloader` is given no ceiling at all --
#: the ceiling is the host's. Generous next to the real files (the largest
#: overlay PNG in `common-overlays` is under 2 MB) and small enough that a
#: source which started serving something else entirely is stopped.
MAX_ASSET_BYTES = 32 * 1024 * 1024

#: `kind` -> the leaf directory under the assets root. RetroArch's own
#: names; see "Where the bytes go" in the module docstring.
#:
#: Every member of `KNOWN_ASSET_KINDS` must appear here -- the host has to
#: be able to choose a destination for every kind it will accept off the
#: wire, and a kind with no entry would be a plugin able to reach an
#: install with nowhere to put it. A test asserts the two agree.
KIND_DIRECTORIES = {
    "shader": "shaders",
    "overlay": "overlays",
    "cheat": "cheats",
    "controller": "autoconfig",
}

#: `kind` -> the environment variable that overrides that kind's directory
#: outright. Read by the CLI, which owns configuration; kept beside the
#: map it overrides so the two cannot drift apart.
KIND_ENV_VARS = {
    "shader": "ROM_HUB_SHADERS_DIR",
    "overlay": "ROM_HUB_OVERLAYS_DIR",
    "cheat": "ROM_HUB_CHEATS_DIR",
    "controller": "ROM_HUB_CONTROLLERS_DIR",
}


class AssetInstallError(Exception):
    """Listing or installing an asset failed, with an operator-fit message."""


@dataclass
class AssetInstallResult:
    asset_id: str
    kind: str
    directory: Path
    files: list[Path]
    license: str
    system: str | None = None
    message: str = ""


def find_asset(items: list[AssetArtifact], asset_id: str) -> AssetArtifact:
    """The item with this id, or a message naming some that exist.

    Matching is exact, for the reason `find_core` gives. The suggestion
    list is *truncated*, which the core and firmware versions do not need
    to do: those catalogues are hundreds of items at most, while an asset
    catalogue is thousands, and pasting 2,265 cheat filenames into a
    terminal is not an error message, it is a denial of service against
    the person reading it.
    """
    for item in items:
        if item.asset_id == asset_id:
            return item

    ids = sorted(i.asset_id for i in items)
    if not ids:
        raise AssetInstallError(
            f"no asset {asset_id!r} is offered by this plugin; it offers "
            f"nothing at all"
        )
    # Near-misses first when there are any: a typo is the common case, and
    # a substring match finds it where the head of an alphabetical list
    # never would.
    needle = asset_id.casefold()
    close = [i for i in ids if needle in i.casefold()][:10]
    shown = close or ids[:10]
    label = "did you mean" if close else "it offers, for example"
    more = "" if len(ids) <= len(shown) else f" (and {len(ids) - len(shown)} more)"
    listed = ", ".join(repr(i) for i in shown)
    raise AssetInstallError(
        f"no asset {asset_id!r} is offered by this plugin; {label}: "
        f"{listed}{more}. Run 'rom-hub assets list' to see the catalogue."
    )


def directory_for(
    asset_kind: str, *, assets_dir: Path, overrides: dict[str, str] | None = None
) -> Path:
    """Where a `kind` installs, before the per-plugin subdirectory.

    An override wins outright rather than being joined onto the root: an
    operator setting `ROM_HUB_CHEATS_DIR` is naming RetroArch's actual
    cheat directory, which is very unlikely to be a child of wherever they
    put the others.
    """
    if asset_kind not in KIND_DIRECTORIES:
        # Unreachable through the wire type, which is a closed Literal.
        # Kept because this function is also reachable from the CLI, and a
        # kind with no destination must never resolve to a default one.
        raise AssetInstallError(
            f"unknown asset kind {asset_kind!r}; this host knows "
            f"{', '.join(KNOWN_ASSET_KINDS)}"
        )
    override = (overrides or {}).get(asset_kind, "")
    if override.strip():
        return Path(override.strip())
    return Path(assets_dir) / KIND_DIRECTORIES[asset_kind]


def install_asset(
    plugin,
    asset: AssetArtifact,
    *,
    assets_dir: Path,
    overrides: dict[str, str] | None = None,
    downloader=None,
) -> AssetInstallResult:
    """Download one asset into the directory configured for its kind.

    `plugin` is a started `PluginProcess` (anything with `.asset_plan()`
    and a `.manifest`). Every URL in the returned plan has already been
    checked against the plugin's allowlist by `asset_plan()`; every
    filename is re-checked here against the directory it may land in.

    There is no `backend` parameter, and that is not an omission. See "No
    backend, at all" in the module docstring.
    """
    manifest = getattr(plugin, "manifest", None)
    slug = getattr(manifest, "slug", "") or "unknown"
    allowlist = list(getattr(manifest, "network", []))

    kind_dir = directory_for(asset.kind, assets_dir=assets_dir, overrides=overrides)

    # One directory per plugin, so two plugins shipping an overlay of the
    # same name do not overwrite each other. The slug is manifest-validated
    # already; running it through the same containment check as a filename
    # costs nothing and means there is no unchecked path join in this
    # module.
    try:
        target = dest_in_job_dir(kind_dir, slug)
    except UnsafeDestination as exc:
        raise AssetInstallError(str(exc)) from exc

    try:
        plan = plugin.asset_plan(asset)
    except Exception as exc:  # noqa: BLE001 - reported, never propagated raw
        raise AssetInstallError(
            f"plugin {slug!r} could not plan a download for asset "
            f"{asset.asset_id!r}: {exc}"
        ) from exc

    destinations = []
    for entry in plan.files:
        try:
            destinations.append((entry, dest_in_job_dir(target, entry.filename)))
        except UnsafeDestination as exc:
            raise AssetInstallError(str(exc)) from exc

    target.mkdir(parents=True, exist_ok=True)

    owns_downloader = downloader is None
    if owns_downloader:
        # Imported here rather than at module scope: importer pulls in the
        # job queue, the dedup hasher and the socket.io scanner, none of
        # which installing a shader needs.
        from rom_hub.importer import HttpDownloader

        downloader = HttpDownloader(allowlist=allowlist, max_bytes=MAX_ASSET_BYTES)

    written: list[Path] = []
    try:
        for entry, dest in destinations:
            try:
                downloader.download(entry.url, dest, expected_size=entry.size_bytes)
            except Exception as exc:  # noqa: BLE001
                raise AssetInstallError(
                    f"downloading {entry.filename!r} for asset "
                    f"{asset.asset_id!r} from {entry.url!r} failed: {exc}"
                ) from exc
            written.append(dest)
    finally:
        if owns_downloader:
            downloader.close()

    result = AssetInstallResult(
        asset_id=asset.asset_id,
        kind=asset.kind,
        directory=target,
        files=written,
        license=asset.license,
        system=asset.system,
    )
    result.message = _describe(result)
    return result


def _describe(result: AssetInstallResult) -> str:
    names = ", ".join(path.name for path in result.files)
    system = f" for {result.system!r}" if result.system else ""
    return (
        f"installed {result.kind} {result.asset_id!r}{system} "
        f"({len(result.files)} file(s): {names}; licence: {result.license}) "
        f"into {result.directory}"
    )
