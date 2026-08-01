"""rom-hub command line.

Install plugins, list them, search across them, import what a search
found, and inspect the import job queue.

`import` is the only command that writes anything anywhere. It refuses
early and cheaply -- unknown plugin, disabled plugin, missing plugin
capability, unconfigured backend, **and a backend that cannot do what
was asked** -- so that by the time a connection is opened the only things
left to go wrong are real ones.

Nothing here names RomM. The library server is whatever
`ROM_HUB_BACKEND` selects (`romm` by default) and is reached only through
`rom_hub.backends.LibraryBackend`; `rom-hub backend info` prints which
one is active and what it can do.
"""

import argparse
import getpass
import importlib
import json
import os
import sys
from pathlib import Path

from . import backends, emuassets, env, secrets
from .assets import (
    DATA_DIR_NAME,
    AssetError,
    describe as describe_assets,
    ensure_assets,
    human_bytes,
)
from .backends import (
    COLLECTIONS,
    IMPORT,
    METADATA,
    BackendError,
    LibraryBackend,
    capabilities_of,
    require,
)
from .broker.fetcher import HttpxFetcher
from .broker.host import PluginCallError, PluginProcess
from .catalog import CatalogError, load_catalog, symbol_for
from .cores import CoreError, find_core, install_core
from .dispatcher import fanout_limit, search_all
from .emuassets import AssetInstallError, find_asset, install_asset
from .firmware import FirmwareError, find_firmware, install_firmware
from .grouping import group_results, paginate
from .importer import run_import
from .jobs import JobQueue, JobState
from .manifest import ManifestError
from .metadata import EnrichError, rom_ref_from, run_enrich
from .registry import Registry, RegistryError
from .sandbox import probe
from .secrets import SecretError
from .stream import (
    STREAM_SERVER_ENV,
    StreamError,
    StreamOutcome,
    StreamServerClient,
    library_handover,
    library_player_path,
    open_handover,
    open_library_url,
    plan_handover,
)
from .types import KNOWN_ASSET_KINDS, SearchResult

CATALOG_PATH = Path(__file__).resolve().parents[2] / "catalog" / "plugins.json"

# Exit codes an operator (or a cron job) can branch on.
EXIT_OK = 0
EXIT_ERROR = 1

# What an unencodable character degrades to. `backslashreplace` writes
# `日` where `replace` would write `?`: still ASCII, but it names the
# character instead of erasing it, so a redirected file or a piped consumer
# keeps something greppable and reversible rather than a row of question
# marks. This is the only thing that changes -- the stream's *encoding* is
# left exactly as the terminal, the redirect or the pipe set it.
_OUTPUT_ERRORS = "backslashreplace"


def configure_output_encoding() -> None:
    """Make CLI output unable to crash on a character it cannot encode.

    A Windows console is cp1252. Every string the CLI prints is ultimately
    attacker-adjacent: a plugin chooses its own name, its result titles, its
    refusal messages and its error strings, and Archive.org alone is full of
    Japanese, Cyrillic and accented titles. Printing one to a cp1252 stdout
    raises UnicodeEncodeError from inside `print`, which killed the whole
    command -- including every result that had already been fetched
    successfully, and every line that would have come after.

    This is fixed once, here, rather than at the ~60 `print` sites that
    would each have to remember. `reconfigure(errors=...)` changes only the
    error handler, so:

    - a UTF-8 stdout can represent everything and is therefore untouched --
      nothing is mangled on a terminal that was already fine;
    - a cp1252 stdout degrades only the individual characters it genuinely
      cannot represent, and keeps printing;
    - a redirect or a pipe keeps whatever encoding it was given, so
      `rom-hub search x > out.txt` behaves like the console it replaced.

    Note this does *not* replace `catalog.symbol_for`, which picks a
    deliberately readable ASCII fallback (`ok`, `!`, `x`) for the status
    column instead of the mechanical `\\u2714` this would produce. That
    handles one known symbol well; this handles arbitrary text safely. The
    two are complementary -- the first is for legibility, the second is the
    guarantee that nothing crashes.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Not a TextIOWrapper: pytest's capture object, a StringIO, or a
            # detached stream. Nothing to do, and nothing to fail over.
            continue
        try:
            reconfigure(errors=_OUTPUT_ERRORS)
        except (ValueError, OSError):
            # Closed or detached mid-flight. Refusing to run the command
            # over a cosmetic setting would be the wrong trade.
            continue

# Job states an import can end in without anything being wrong.
_SUCCESS_STATES = (JobState.DONE, JobState.SKIPPED_DUPLICATE)


def default_root() -> Path:
    """Where installed plugins and runtime state live.

    `ROM_HUB_HOME` first (its pre-rename `ROMM_HUB_HOME` spelling still
    works -- see `rom_hub.env`). Failing that, `~/.rom-hub` -- unless the
    pre-rename `~/.romm-hub` is the one that actually exists, in which
    case that is where this install's plugins and job queue already are
    and moving them silently would be the worst possible reading of a
    rename.
    """
    configured = env.get("ROM_HUB_HOME")
    if configured:
        return Path(configured)
    root = Path.home() / ".rom-hub"
    if not root.exists():
        legacy = Path.home() / ".romm-hub"
        if legacy.is_dir():
            return legacy
    return root


def jobs_db_path(root: Path | None = None) -> Path:
    """Where the import job queue lives.

    Under `var/` beside the installed plugins, because it is runtime state
    that grows: it must not land in the repo, and on a workstation it must
    not land on the system drive by default either.
    """
    return Path(root or default_root()) / "var" / "jobs.db"


def downloads_dir(root: Path | None = None) -> Path:
    """Where in-flight downloads land. Same reasoning as jobs_db_path."""
    return Path(root or default_root()) / "var" / "downloads"


def artwork_dir(root: Path | None = None) -> Path:
    """Where a fetched cover lands on its way to RomM. Same reasoning again."""
    return Path(root or default_root()) / "var" / "artwork"


def plugin_data_root(root: Path | None = None) -> Path:
    """Where a plugin's declared data assets are cached.

    Under `var/` beside the job queue, for the same reason: it is runtime
    state that grows -- OpenVGDB alone unpacks to 42 MiB -- so it must not
    land in the repo, and on a workstation it must not land on the system
    drive by default either. Never inside the plugin's own directory: that
    is a git checkout the registry deletes and replaces on every reinstall.
    """
    return Path(root or default_root()) / "var" / DATA_DIR_NAME


def allow_asset_fetch() -> bool:
    """Whether the Hub may download a declared data asset when one is missing.

    On by default, because a plugin that cannot get its dataset cannot do
    its job. `ROM_HUB_NO_ASSET_FETCH=1` is the veto for a metered link: a
    missing asset then refuses and names `rom-hub plugin assets <slug>
    --fetch`, rather than pulling megabytes mid-command.
    """
    return env.get("ROM_HUB_NO_ASSET_FETCH") != "1"


def _announce_asset(message: str) -> None:
    """Where a data-asset notice goes.

    stderr, so `rom-hub search x > out.txt` keeps its results clean, and
    unconditionally, because a multi-megabyte download nobody was told
    about is the exact surprise this mechanism exists to avoid.
    """
    print(f"note: {message}", file=sys.stderr)


def prepare_assets(plugin, root: Path | None = None) -> dict[str, str]:
    """Resolve a plugin's declared data assets to verified local paths.

    Called before the subprocess starts, at every site that starts one, so
    a plugin is never handed a path to bytes the host has not verified.
    Costs nothing for the ten plugins that declare no assets.
    """
    return ensure_assets(
        plugin.manifest,
        plugin_data_root(root),
        announce=_announce_asset,
        allow_fetch=allow_asset_fetch(),
    )


def secret_store(root: Path | None = None):
    """The store behind `secret`-typed config fields.

    Built at call time, like every other setting, so `ROM_HUB_SECRET_STORE`
    and `ROM_HUB_SECRET_KEY` can be flipped by a shell.
    """
    return secrets.open_store(Path(root or default_root()))


def _announce_secret(message: str) -> None:
    """Where a secret-store notice goes. stderr, and never with a value."""
    print(f"note: {message}", file=sys.stderr)


def prepare_secrets(plugin, root: Path | None = None) -> dict[str, str]:
    """This plugin's `secret` config values, ready for the `init` frame.

    Called at every site that starts a plugin subprocess, immediately before
    it starts, so a secret exists as a string in host memory for as short a
    time as the call takes and is never written to `state.json` on the way.

    It is also where a pre-`secret` plaintext value is migrated out of the
    plain config, because this is the one path every capability command
    shares -- an operator who never reinstalls still gets moved over, and is
    told, once, on stderr.

    Costs nothing for the nine plugins that declare no secrets: the schema
    is checked first and the store is never opened.
    """
    if not secrets.secret_fields(plugin.manifest):
        return {}
    root = Path(root or default_root())
    store = secret_store(root)
    secrets.migrate_plaintext(Registry(root), plugin, store, announce=_announce_secret)
    resolved = {}
    for key in secrets.secret_fields(plugin.manifest):
        value = store.get(plugin.slug, key)
        if value:
            resolved[key] = value
    return resolved


def cores_dir(root: Path | None = None) -> Path:
    """Where installed emulator cores land.

    Configuration, not a constant. On the deployment target this points at
    the `romm-stream` core directory, but that path is the *operator's* to
    choose -- hard-coding `/opt/romm-stream/cores` here would make the Hub
    unusable anywhere else and would put a plugin-supplied download outside
    `ROM_HUB_HOME` on every host that did not happen to match.

    Read at call time so a shell can flip it, like every other setting.
    """
    configured = env.get("ROM_HUB_CORES_DIR").strip()
    if configured:
        return Path(configured)
    return Path(root or default_root()) / "var" / "cores"


def firmware_dir(root: Path | None = None) -> Path:
    """Where installed BIOS/firmware lands.

    Configuration, not a constant, for exactly the reasons `cores_dir`
    gives -- and one more. An emulator is usually pointed at a `system` or
    `bios` directory it already owns (RetroArch's `system/`, EmulationStation's
    `bios/`), and `ROM_HUB_FIRMWARE_DIR` is how an operator says "put it
    where the emulator already looks" instead of copying files by hand
    afterwards.

    Read at call time so a shell can flip it, like every other setting.
    """
    configured = env.get("ROM_HUB_FIRMWARE_DIR").strip()
    if configured:
        return Path(configured)
    return Path(root or default_root()) / "var" / "firmware"


def assets_dir(root: Path | None = None) -> Path:
    """The root under which emulator support files land.

    Configuration, not a constant, for exactly the reasons `cores_dir` and
    `firmware_dir` give. `kind` picks a leaf directory beneath this one --
    `shaders`, `overlays`, `cheats`, `autoconfig` -- and those names are
    RetroArch's own, so `ROM_HUB_ASSETS_DIR=~/.config/retroarch` puts every
    file exactly where RetroArch already looks for it.

    Read at call time so a shell can flip it, like every other setting.
    """
    configured = env.get("ROM_HUB_ASSETS_DIR").strip()
    if configured:
        return Path(configured)
    return Path(root or default_root()) / "var" / "assets"


def asset_dir_overrides() -> dict[str, str]:
    """Per-kind directory overrides, for a layout that is not one tree.

    `ROM_HUB_ASSETS_DIR` assumes the four kinds share a parent, which is
    true of a RetroArch install and need not be true of anything else --
    an operator may keep cheats with their frontend and shaders with their
    GPU profiles. Each of these names one kind's directory outright rather
    than relative to the root; see `emuassets.directory_for`.
    """
    return {
        kind: env.get(name) for kind, name in emuassets.KIND_ENV_VARS.items()
    }


def backend_name() -> str:
    """Which library backend to use. `romm` unless told otherwise.

    Read at call time, like every other setting, so a shell can flip it
    between two servers without reinstalling anything.
    """
    return env.get("ROM_HUB_BACKEND").strip() or backends.DEFAULT_BACKEND


def open_backend(name: str | None = None) -> LibraryBackend:
    """Build the configured backend. Opens no connection.

    Raises `UnknownBackend` for a name that is not installed and
    `BackendNotConfigured` for one that is but has nothing to connect
    to -- both before a subprocess is started or a socket opened, which
    is the whole reason this is read first.
    """
    return backends.load(name or backend_name())


def allow_unsandboxed() -> bool:
    """Whether the operator has opted out of the fail-closed sandbox policy.

    Read at call time, not import time, so tests and shells can flip it.
    """
    return env.get("ROM_HUB_ALLOW_UNSANDBOXED") == "1"


def _catalog_entry(slug: str):
    """Resolve a bare slug through the directory, or return None.

    A source that looks like a URL or an existing path is used as given --
    the catalog is a convenience, never a required intermediary.
    """
    if "/" in slug or "\\" in slug or Path(slug).exists():
        return None
    try:
        entries = load_catalog(CATALOG_PATH)
    except CatalogError:
        return None
    return next((e for e in entries if e.slug == slug), None)


def _cmd_plugin_browse(args) -> int:
    try:
        entries = load_catalog(CATALOG_PATH)
    except CatalogError as exc:
        print(f"catalog unreadable: {exc}", file=sys.stderr)
        return 1
    print(f"{'':<3}{'SLUG':<16} {'VERSION':<9} {'CAPABILITIES':<22} AUTHOR")
    for e in sorted(entries, key=lambda x: x.slug):
        caps = ",".join(e.capabilities)
        mark = symbol_for(e.status, getattr(sys.stdout, "encoding", None))
        print(f"{mark:<3}{e.slug:<16} {e.version:<9} {caps:<22} {e.author}")
        print(f"   {e.repository}")
        # Shown before install so the permission ask is a decision, not a
        # surprise discovered afterwards.
        print(f"   requests network: {', '.join(e.network) or '(none)'}")
    print()
    print(f"{len(entries)} plugin(s). Install with: rom-hub plugin install <slug>")
    return 0


def _cmd_platforms(args) -> int:
    """Which platforms play, which are catalogue-only, and who targets each.

    Reads the *catalog* rather than the installed plugins, and does so on
    purpose: the question "should I install this" is asked before there is
    anything installed to ask about. `--installed` narrows it afterwards.
    """
    from rom_hub.playability import (
        CATALOGUE_ONLY,
        NEEDS_NETPLAY,
        PLAYS,
        ROMM_VERSION,
        verdict_for,
    )

    try:
        entries = load_catalog(CATALOG_PATH)
    except CatalogError as exc:
        print(f"catalog unreadable: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.installed:
        have = {p.slug for p in Registry(default_root()).installed() if p.enabled}
        entries = [e for e in entries if e.slug in have]
        if not entries:
            print("no enabled plugins are listed in the directory")
            return EXIT_OK

    # Only importers, because only an importer can file an unplayable ROM.
    # A metadata plugin covering `vectrex` is offering to identify a
    # Vectrex ROM somebody already has; there is no dead import in it.
    importers = [e for e in entries if "importer" in e.capabilities]

    by_platform: dict[str, list[str]] = {}
    for entry in importers:
        for platform in entry.platforms:
            by_platform.setdefault(platform, []).append(entry.slug)

    groups = {
        PLAYS: ("PLAYABLE", "an emulator core ships with RomM"),
        NEEDS_NETPLAY: (
            "NEEDS NETPLAY",
            "core is in RomM's nightly build, read only when "
            "EJS_NETPLAY_ENABLED is set",
        ),
        CATALOGUE_ONLY: (
            "CATALOGUE ONLY",
            "no emulator core; imports land and will not start",
        ),
    }
    for key, (heading, why) in groups.items():
        rows = sorted(p for p in by_platform if verdict_for(p).verdict == key)
        print(f"\n{heading} ({len(rows)}) -- {why}")
        if not rows:
            print("  (none)")
            continue
        for platform in rows:
            plugins = ", ".join(sorted(set(by_platform[platform])))
            print(f"  {platform:<26} {plugins}")

    total = len(by_platform)
    dead = sum(1 for p in by_platform if verdict_for(p).verdict == CATALOGUE_ONLY)
    print()
    print(
        f"{total} platform(s) across {len(importers)} importer plugin(s); "
        f"{dead} cannot be played."
    )
    print(
        f"Playability is RomM {ROMM_VERSION}'s own EmulatorJS core map, and the "
        f"Xbox client ships the same player."
    )
    print(
        "An import to a catalogue-only platform warns and proceeds; pass "
        "--allow-unplayable to silence it."
    )
    return EXIT_OK


def _cmd_plugin_install(args) -> int:
    reg = Registry(default_root())
    source, ref = args.source, args.ref
    entry = _catalog_entry(source)
    if entry is not None:
        source, ref = entry.install, ref or entry.ref
        print(f"resolved {entry.slug!r} from the catalog: {source} @ {ref}")
    plugin = reg.install(source, ref)
    caps = ", ".join(sorted(plugin.manifest.capabilities))
    print(f"installed {plugin.slug} {plugin.manifest.version} (capabilities: {caps})")
    print(f"  pinned commit: {plugin.commit or '(unknown)'}")
    print(f"  declared network allowlist: {plugin.manifest.network or '(none)'}")
    # Printed at install, before the first command that would trigger it,
    # because "this plugin will pull 8.7 MiB from github.com" is a thing to
    # learn while deciding whether to install rather than halfway through a
    # search. The same lines are available later from `plugin assets`.
    _print_declared_assets(plugin.manifest, indent="  ")
    _print_declared_secrets(plugin, indent="  ")
    # An operator approving an install must be told exactly how much of that
    # allowlist is a boundary and how much is a declaration of intent, and the
    # answer depends on whether this host can confine the subprocess at all.
    available, reason = probe()
    if available:
        print("  note: the plugin subprocess installs a seccomp filter on itself")
        print("        before importing any plugin code, so network egress and")
        print("        process spawn are blocked outright — the allowlist above is")
        print("        enforced, not advisory. File reads are NOT confined: seccomp")
        print("        cannot filter on a path, so a plugin can still read any file")
        print("        this process can. Install only plugins you trust.")
    else:
        print("  note: this host cannot confine plugins, so plugins will refuse to")
        print("        run unless ROM_HUB_ALLOW_UNSANDBOXED=1 is set. With it set")
        print("        there is no confinement at all: a hostile plugin can ignore")
        print("        the allowlist above, open its own sockets, read any file this")
        print("        process can, and spawn processes. Install only plugins you")
        print("        trust.")
        print(f"        reason: {reason}")
    return 0


def _print_declared_secrets(plugin, indent: str = "") -> None:
    """The credentials this plugin will need, and the command that sets them.

    Printed at install for the same reason the asset sizes are: "this needs
    an API key" is a thing to learn while deciding, not halfway through the
    first enrich that refuses.
    """
    fields = secrets.secret_fields(plugin.manifest)
    if not fields:
        return
    print(f"{indent}needs {len(fields)} secret(s): {', '.join(fields)}")
    for key in fields:
        print(f"{indent}  rom-hub plugin secret set {plugin.slug} {key}")
    print(
        f"{indent}  kept out of the Hub's plain config and redacted from every "
        f"command's output;"
    )
    print(f"{indent}  'rom-hub plugin secret list' says where it is stored")


def _print_declared_assets(manifest, indent: str = "") -> None:
    """The datasets a manifest declares, in the form an operator judges.

    Size and origin first, because those are the two facts that decide
    whether the download is acceptable; the digest after, because it is
    what makes the download safe to accept at all.
    """
    if not manifest.data_assets:
        return
    total = sum(a.size_bytes or 0 for a in manifest.data_assets)
    print(
        f"{indent}declares {len(manifest.data_assets)} data asset(s), "
        f"{human_bytes(total) if total else 'size undeclared'} to download:"
    )
    for asset in manifest.data_assets:
        unpacked = f", unpacked from {asset.archive}" if asset.archive else ""
        print(
            f"{indent}  {asset.name}  {human_bytes(asset.size_bytes)} "
            f"from {asset.host}{unpacked}"
        )
        print(f"{indent}    {asset.url}")
        print(f"{indent}    sha256 {asset.sha256}")
        if asset.description:
            print(f"{indent}    {asset.description}")
    print(
        f"{indent}  fetched on first use, verified against the sha256 above, "
        f"and cached"
    )
    print(f"{indent}  in {plugin_data_root() / manifest.slug}")


def _cmd_plugin_assets(args) -> int:
    """Show a plugin's declared data assets and whether they are cached.

    `--fetch` is the pre-fetch: the same code path a capability command
    takes, run deliberately and on its own, so the download happens when
    the operator chose it rather than in the middle of something else.
    """
    plugin = Registry(default_root()).get(args.slug)
    if not plugin.manifest.data_assets:
        print(f"plugin {plugin.slug!r} declares no data assets")
        return EXIT_OK

    _print_declared_assets(plugin.manifest)
    print()
    if args.fetch:
        # Deliberately not `allow_asset_fetch()`: the operator typed
        # --fetch, which is a more specific instruction than the standing
        # "do not download during ordinary commands" setting.
        ensure_assets(
            plugin.manifest,
            plugin_data_root(),
            announce=_announce_asset,
            allow_fetch=True,
        )

    for state in describe_assets(plugin.manifest, plugin_data_root()):
        mark = "ok" if state.ready else "--"
        print(f"{mark:<3}{state.asset.name:<24} {state.detail}")
    return EXIT_OK


def _cmd_plugin_list(args) -> int:
    plugins = Registry(default_root()).installed()
    if not plugins:
        print("no plugins installed")
        return 0
    for p in plugins:
        state = "enabled" if p.enabled else "disabled"
        caps = ",".join(sorted(p.manifest.capabilities))
        print(f"{p.slug:<20} {p.manifest.version:<10} {state:<9} [{caps}]")
    return 0


def _parse_config_assignment(raw: str, schema: dict) -> tuple[str, object]:
    """`KEY=VALUE` from the command line, coerced to the declared type.

    The declared type is the only thing consulted. A `list[str]` field takes
    a comma-separated value because that is what fits on a command line,
    and an `int` field refuses `"seven"` here rather than letting a string
    reach a plugin that will index with it.
    """
    key, sep, value = raw.partition("=")
    key = key.strip()
    if not sep:
        raise ValueError(f"{raw!r} is not KEY=VALUE")
    spec = schema.get(key)
    if spec is None:
        declared = ", ".join(schema) or "(none)"
        raise ValueError(
            f"no config field named {key!r} is declared by this plugin "
            f"(it declares: {declared})"
        )
    declared = spec.get("type", "str") if isinstance(spec, dict) else "str"
    if declared == "list[str]":
        return key, [part.strip() for part in value.split(",") if part.strip()]
    if declared == "int":
        try:
            return key, int(value.strip())
        except ValueError:
            raise ValueError(
                f"{key!r} is declared int, and {value.strip()!r} is not one"
            ) from None
    if declared == "bool":
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return key, True
        if lowered in ("0", "false", "no", "off"):
            return key, False
        raise ValueError(
            f"{key!r} is declared bool, and {value.strip()!r} is not one "
            f"(use true/false)"
        )
    return key, value


def _cmd_plugin_config(args) -> int:
    """Dump one plugin's settings, with every secret redacted.

    The command that exists so "where do I check what this is set to?" has
    an answer that is safe to screenshot. A `secret`-typed field is never
    printed, whatever it holds -- including a legacy plaintext value that
    has not been migrated yet.

    `--set KEY=VALUE` writes one non-secret field. Secrets are refused here
    and sent to `plugin secret set`, which is the path that puts them in the
    OS keyring instead of `state.json` -- accepting one here would silently
    downgrade where it is stored.
    """
    registry = Registry(default_root())
    plugin = registry.get(args.slug)
    schema = plugin.manifest.config_schema or {}
    secret_keys = set(secrets.secret_fields(plugin.manifest))
    if not schema:
        print(f"plugin {plugin.slug!r} declares no config")
        return EXIT_OK

    if getattr(args, "set", None):
        config = dict(plugin.config or {})
        for assignment in args.set:
            try:
                key, value = _parse_config_assignment(assignment, schema)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if key in secret_keys:
                print(
                    f"error: {key!r} is declared secret; set it with "
                    f"'rom-hub plugin secret set {plugin.slug} {key}' so it "
                    f"lands in the keyring rather than in state.json",
                    file=sys.stderr,
                )
                return EXIT_ERROR
            config[key] = value
            print(f"set {key} = {value!r} for {plugin.slug}")
        registry.set_config(plugin.slug, config)
        plugin = registry.get(args.slug)
        print()

    shown = secrets.redact_config(plugin.manifest, plugin.config)
    print(f"{'KEY':<24} {'TYPE':<10} VALUE")
    for key, spec in schema.items():
        declared = spec.get("type", "?") if isinstance(spec, dict) else "?"
        if key in secret_keys:
            value = secrets.REDACTED if _secret_is_set(plugin, key) else "(not set)"
        else:
            value = shown.get(key, spec.get("default") if isinstance(spec, dict) else "")
        print(f"{key:<24} {declared:<10} {value}")
    if secret_keys:
        print()
        print(
            "secret values are never printed here; see 'rom-hub plugin secret "
            "list' for where they are stored"
        )
    return EXIT_OK


def _secret_is_set(plugin, key: str) -> bool:
    """Whether a value exists for `key`, without ever returning it.

    A legacy plaintext value still sitting in `state.json` counts as set:
    the field *is* configured, it is simply not migrated yet, and reporting
    "(not set)" for a plugin that works would be the wrong answer.
    """
    if str((plugin.config or {}).get(key) or "").strip():
        return True
    try:
        return bool(secret_store().get(plugin.slug, key))
    except SecretError:
        return False


def _read_secret_value(args) -> str:
    """The value for `plugin secret set`, from wherever it was offered.

    Deliberately four routes, because "do not put it in your shell history"
    only works if there is somewhere else to put it:

    * a TTY prompt (the default), which echoes nothing and asks twice;
    * `--stdin`, or a pipe, for `pass show x | rom-hub ...`;
    * `--env VAR`, for a systemd unit or a CI runner;
    * `--value`, which works and **warns**, because it is already in the
      history by the time this runs and pretending otherwise helps nobody.
    """
    if args.value is not None:
        print(
            "warning: the value was passed as a command-line argument, so it "
            "is now in your shell history and in this machine's process list. "
            "Clear it (history -d, or your shell's equivalent) and consider "
            "the key compromised enough to rotate. Next time, run "
            "'rom-hub plugin secret set <slug> <key>' with no value and type "
            "it at the prompt, or pipe it in with --stdin.",
            file=sys.stderr,
        )
        return args.value
    if args.env:
        value = os.environ.get(args.env, "")
        if not value:
            raise SecretError(
                f"--env {args.env} was given but ${args.env} is unset or empty"
            )
        return value
    if args.stdin or not sys.stdin.isatty():
        if not args.stdin:
            print("note: stdin is not a terminal; reading the value from it",
                  file=sys.stderr)
        return sys.stdin.read().strip("\r\n")
    first = getpass.getpass("value (not echoed): ")
    again = getpass.getpass("again: ")
    if first != again:
        raise SecretError("the two values did not match; nothing was stored")
    return first


def _cmd_plugin_secret_set(args) -> int:
    plugin = Registry(default_root()).get(args.slug)
    fields = secrets.secret_fields(plugin.manifest)
    if args.key not in fields:
        declared = ", ".join(fields) or "(none)"
        print(
            f"error: plugin {plugin.slug!r} declares no secret named "
            f"{args.key!r} (it declares: {declared})",
            file=sys.stderr,
        )
        return EXIT_ERROR

    value = _read_secret_value(args)
    if not value.strip():
        print("error: an empty value is not a secret; nothing was stored",
              file=sys.stderr)
        return EXIT_ERROR

    store = secret_store()
    store.set(plugin.slug, args.key, value)
    info = store.info()
    # The name, never the value -- this line is the one most likely to end
    # up in a terminal recording.
    print(f"stored {args.key} for {plugin.slug} in the {info.kind} store")
    print(f"  {info.detail}")
    print(f"  {info.protection}")
    return EXIT_OK


def _cmd_plugin_secret_clear(args) -> int:
    plugin = Registry(default_root()).get(args.slug)
    removed = secret_store().delete(plugin.slug, args.key)
    # Also drop any un-migrated plaintext, or "cleared" would be a lie for
    # exactly the operator most likely to be running this.
    config = dict(plugin.config or {})
    if config.pop(args.key, None) is not None:
        Registry(default_root()).set_config(plugin.slug, config)
        removed = True
    print(
        f"cleared {args.key} for {plugin.slug}"
        if removed
        else f"{args.key} was not set for {plugin.slug}"
    )
    return EXIT_OK


def _cmd_plugin_secret_list(args) -> int:
    """Which secrets are set, where they live, and what that protects.

    Never a value and never a hash of one: a fingerprint would let anyone
    holding this output confirm a guess. The character count is printed
    because "did my paste get truncated" is a real question and a length is
    not a verifier.
    """
    store = secret_store()
    info = store.info()
    print(f"{'store':<12} {info.kind}")
    print(f"{'location':<12} {info.detail}")
    print(f"{'protects':<12} {info.protection}")
    if not info.at_rest_secret:
        print(
            f"{'':<12} (so: safe against a config dump, a screenshot or a "
            f"commit -- not against someone who can read the directory)"
        )
    print()

    plugins = Registry(default_root()).installed()
    if args.slug:
        plugins = [p for p in plugins if p.slug == args.slug]
    rows = []
    for plugin in plugins:
        for key in secrets.secret_fields(plugin.manifest):
            plaintext = str((plugin.config or {}).get(key) or "")
            value = plaintext or (store.get(plugin.slug, key) or "")
            if not value:
                status = "not set"
            elif plaintext:
                status = (
                    f"set ({len(value)} characters) -- STILL IN PLAIN CONFIG, "
                    f"moved on next use"
                )
            else:
                status = f"set ({len(value)} characters)"
            rows.append((plugin.slug, key, status))

    if not rows:
        print("no installed plugin declares a secret")
        return EXIT_OK
    print(f"{'PLUGIN':<22} {'FIELD':<20} STATUS")
    for slug, key, status in rows:
        print(f"{slug:<22} {key:<20} {status}")
    return EXIT_OK


def _cmd_plugin_enable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, True)
    print(f"enabled {args.slug}")
    return 0


def _cmd_plugin_disable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, False)
    print(f"disabled {args.slug}")
    return 0


def _search_size(size_bytes: int | None) -> str:
    return f"{size_bytes / 1_048_576:.1f} MB" if size_bytes else "-"


def _expanded_rows(args, page) -> set[int]:
    """Which printed row numbers should list their variants.

    Row numbers are **absolute** -- `#26` is the first row of
    `--offset 25` -- so an operator can copy a number off one page and
    expand it from the next command without recounting.
    """
    if getattr(args, "all_variants", False):
        return {page.offset + i + 1 for i in range(len(page.groups))}
    raw = getattr(args, "expand", None)
    if not raw:
        return set()
    if str(raw).strip().lower() == "all":
        return {page.offset + i + 1 for i in range(len(page.groups))}
    wanted = set()
    for part in str(raw).replace(",", " ").split():
        try:
            wanted.add(int(part))
        except ValueError:
            print(
                f"note: --expand {part!r} is not a row number or 'all'; ignored",
                file=sys.stderr,
            )
    return wanted


def _print_flat(results) -> None:
    """The pre-grouping listing, unchanged, for `--no-group`."""
    for r in results:
        flag = " [stream-only]" if r.extra.get("stream_only") == "true" else ""
        print(
            f"{r.plugin:<14} {r.platform or '-':<12} "
            f"{_search_size(r.size_bytes):>10}  {r.title}{flag}"
        )


def _print_groups(page, expand: set[int]) -> None:
    for index, group in enumerate(page.groups, start=page.offset + 1):
        sources = group.sources
        source_cell = sources[0] if len(sources) == 1 else f"{len(sources)} sources"
        variants = (
            f"  [{group.variant_count} variants]" if group.variant_count > 1 else ""
        )
        flag = " [stream-only]" if group.stream_only else ""
        print(
            f"{index:>4}  {source_cell:<14} {group.platform or '-':<12} "
            f"{_search_size(group.size_bytes):>10}  {group.title}{variants}{flag}"
        )
        if index not in expand:
            continue
        for variant in group.variants:
            vflag = " [stream-only]" if variant.stream_only else ""
            print(
                f"        - {variant.label:<24} {', '.join(variant.sources):<28} "
                f"{_search_size(variant.size_bytes):>10}  "
                f"{variant.primary.title}{vflag}"
            )


def _cmd_search(args) -> int:
    plugins = Registry(default_root()).installed()
    searchable = [p for p in plugins if p.enabled and "search" in p.manifest.capabilities]
    if not searchable:
        print("no plugins available for search — install one with 'rom-hub plugin install'")
        return 0

    # `--limit` counts merged rows; each source has to be asked for more
    # than that, because grouping only ever collapses. `--per-source` is
    # the override for anyone who wants to say exactly how much work the
    # fan-out is allowed to be.
    per_source = fanout_limit(args.limit, args.offset, args.per_source)

    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            plugins,
            fetcher=fetcher,
            query=args.query,
            platform=args.platform,
            limit=per_source,
            allow_unsandboxed=allow_unsandboxed(),
            assets_for=prepare_assets,
            secrets_for=prepare_secrets,
        )
    finally:
        fetcher.close()

    if args.no_group:
        _print_flat(outcome.results)
        print()
        print(
            f"{outcome.responded} of {outcome.total} sources responded, "
            f"{len(outcome.results)} results"
        )
    else:
        groups = group_results(outcome.results, args.query)
        page = paginate(groups, args.limit, args.offset)
        _print_groups(page, _expanded_rows(args, page))
        print()
        shown = (
            f" (showing {page.first}-{page.last} of {page.total_groups})"
            if page.total_groups
            else ""
        )
        print(
            f"{outcome.responded} of {outcome.total} sources responded, "
            f"{page.total_results} results in {page.total_groups} games{shown}"
        )
        if page.has_more:
            print(f"  next page: --offset {page.last}")
        if any(g.variant_count > 1 for g in page.groups):
            print("  variants:  --expand <#>  |  --all-variants  |  --no-group")

    # Partial answers stay partial: grouping reorganises what came back, it
    # cannot know what a source that failed would have said.
    if outcome.capped:
        print(
            f"  note: {', '.join(outcome.capped)} returned the full "
            f"{per_source} results asked for -- there may be more; raise "
            f"--per-source",
            file=sys.stderr,
        )
    for status in outcome.statuses:
        if not status.ok:
            print(f"  ! {status.slug}: {status.error}", file=sys.stderr)
    return 0


class _PlanOverrides:
    """A started PluginProcess with the operator's `--platform` /
    `--collection` applied to whatever plan it returns.

    The override lands on the host side, after `PluginProcess.plan()` has
    validated the plan's shape and gated every URL against the allowlist,
    so it cannot be used to widen anything -- it retargets where a ROM
    files, never where the bytes come from.

    It also cannot rescue a refusal. A plugin that raises "needs mapping"
    never returns a plan for this to modify, which is deliberate: the fix
    for a missing emulator mapping is to add the mapping, not to paper
    over it once per invocation and leave the next operator to hit it.
    """

    def __init__(self, proc, platform: str | None, collection: str | None):
        self._proc = proc
        self._platform = platform
        self._collection = collection

    @property
    def manifest(self):
        return self._proc.manifest

    def plan(self, result: SearchResult):
        plan = self._proc.plan(result)
        update = {}
        if self._platform:
            update["platform"] = self._platform
        if self._collection:
            update["collection"] = self._collection
        # Reconstruct rather than model_copy(update=...), which skips
        # validation entirely. Defence in depth, and honestly labelled as
        # such: today no argparse value can actually fail this, because the
        # `if` guards above drop the empty string and any non-empty string
        # is a legal `platform`/`collection`. It is here so that widening
        # either field's constraints, or adding a third override, cannot
        # quietly land an unvalidated value in a FetchPlan. A mutation test
        # confirms the suite does not currently notice its removal.
        return type(plan)(**{**plan.model_dump(), **update}) if update else plan


def _cmd_import(args) -> int:
    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "importer")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    # Read before anything is started, so an unconfigured Hub costs no
    # subprocess and no half-open connection.
    backend = open_backend()

    # And refused before either, if the backend cannot do this at all.
    require(backend, IMPORT, "importing a ROM")

    # `--collection` is the one place a missing *optional* capability
    # still refuses, and the asymmetry is deliberate. The pipeline
    # degrades a collection, because the name it sees is usually a
    # plugin's default (archive-org files everything under "Archive.org")
    # and dropping boilerplate costs the operator nothing they asked for.
    # This is the opposite case: they typed the name. Quietly importing
    # somewhere other than where they said is how a library ends up
    # unsorted with no error to explain it -- so it stops here, before the
    # plugin process starts, and the hint says what to run instead.
    if args.collection:
        require(
            backend,
            COLLECTIONS,
            f"--collection {args.collection!r}",
            hint=(
                f"Re-run without --collection to import the ROM anyway; it "
                f"will land in the library ungrouped, and no collection "
                f"named {args.collection!r} will be created."
            ),
        )

    result = SearchResult(
        source_id=args.source_id,
        # The identifier is all the CLI knows. The plugin looks the real
        # title up; this is only what a failed job will be labelled with.
        title=args.source_id,
        platform=args.platform,
        plugin=plugin.slug,
    )

    root = default_root()
    data_assets = prepare_assets(plugin, root)
    fetcher = HttpxFetcher()
    try:
        with (
            JobQueue(jobs_db_path(root)) as queue,
            PluginProcess(
                plugin_dir=plugin.path,
                manifest=plugin.manifest,
                config=plugin.config,
                fetcher=fetcher,
                allow_unsandboxed=allow_unsandboxed(),
                data_assets=data_assets,
                secrets=prepare_secrets(plugin, root),
            ) as proc,
        ):
            outcome = run_import(
                _PlanOverrides(proc, args.platform, args.collection),
                result,
                backend=backend,
                queue=queue,
                download_dir=downloads_dir(root),
                warn_unplayable=not args.allow_unplayable,
            )
    finally:
        fetcher.close()
        backend.close()

    # Before the outcome line, not after it. This is the sentence that
    # explains why an import that says DONE will do nothing when clicked,
    # and a reader who stops at the first line has to have read it. On
    # stderr regardless of how the job ended, because it is a warning about
    # a *successful* import -- putting it on stdout would mean a shell
    # pipeline collecting job output silently swallowed it.
    for warning in outcome.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    stream = sys.stdout if outcome.state in _SUCCESS_STATES else sys.stderr
    # ASCII only: a Windows console defaults to cp1252, and this project has
    # already been bitten once by a non-encodable character in CLI output
    # (see catalog.symbol_for).
    print(f"job {outcome.job_id}: {outcome.state.value} - {outcome.message}",
          file=stream)
    return EXIT_OK if outcome.state in _SUCCESS_STATES else EXIT_ERROR


def _require_capability(plugin, capability: str) -> str | None:
    """The two refusals every capability command makes first.

    Returns an operator-fit message, or None if the plugin can do this.
    Both checks are cheap and both happen before a subprocess is started
    or a RomM connection is opened.
    """
    if not plugin.enabled:
        return (
            f"plugin {plugin.slug!r} is disabled; enable it with "
            f"'rom-hub plugin enable {plugin.slug}'"
        )
    if capability not in plugin.manifest.capabilities:
        declared = ", ".join(sorted(plugin.manifest.capabilities)) or "(none)"
        return (
            f"plugin {plugin.slug!r} does not provide the {capability!r} "
            f"capability (it declares: {declared})"
        )
    return None


def _cmd_enrich(args) -> int:
    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "metadata")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    # Read before anything is started, so an unconfigured Hub costs no
    # subprocess and no half-open connection.
    backend = open_backend()
    require(backend, METADATA, "enriching a rom's metadata")

    root = default_root()
    # Before the subprocess, before the backend call: a plugin whose
    # dataset cannot be fetched or verified should cost neither.
    data_assets = prepare_assets(plugin, root)
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
            data_assets=data_assets,
            secrets=prepare_secrets(plugin, root),
        ) as proc:
            rom = backend.get_rom(args.rom_id)
            result = run_enrich(
                proc,
                rom_ref_from(rom, args.rom_id, {"source_id": args.source_id or ""}),
                backend=backend,
                work_dir=artwork_dir(root) / str(args.rom_id),
            )
    finally:
        fetcher.close()
        backend.close()

    print(result.message)
    return EXIT_OK


def stream_server_base() -> str:
    """The operator's `romm-stream`, if they run one. Configuration only.

    Read at call time like every other setting, and empty by default: an
    operator with no stream server must not have `rom-hub stream` try to
    reach one.
    """
    return env.get(STREAM_SERVER_ENV).strip()


def _print_outcome(outcome, as_json: bool) -> None:
    """One printer for both ways into `stream`, so `--json` has one schema."""
    if as_json:
        print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True))
        return

    target = outcome.handover.target
    print(f"{target.kind}\t{target.target}")
    if target.title:
        print(f"title\t{target.title}")
    if target.mime_type:
        print(f"type\t{target.mime_type}")
    for key in sorted(target.extra):
        print(f"{key}\t{target.extra[key]}")
    print(f"play\t{outcome.handover.how}")
    if outcome.route is not None:
        print(f"server\t{outcome.route.describe()}")
    for note in outcome.notes:
        print(f"note\t{note}")
    if outcome.opened:
        print(f"opened\t{outcome.opened}")


def _cmd_stream(args) -> int:
    """Resolve one item to a stream target and hand it over.

    Two ways in, one handover. A plugin resolves an item the operator does
    not have (`rom-hub stream archive-org <id>`), or the operator names a
    rom their own library already holds (`--library-rom <id>`), and either
    way the host decides what the answer can be given to and -- with
    `--open` -- gives it.

    What it deliberately does not do is stream. `romm-stream` is the
    streaming server; the Hub resolves, validates and hands over. When a
    stream server is configured the Hub asks it, read-only, whether the
    platform is playable there, because that is the one question it can
    answer for a target it has never seen. See `rom_hub.stream`.
    """
    as_json = getattr(args, "json", False)

    if args.library_rom is not None:
        if args.plugin or args.source_id:
            print(
                "error: --library-rom names a rom your library already has, "
                "so there is no plugin to resolve it; drop the plugin and "
                "source id, or drop --library-rom",
                file=sys.stderr,
            )
            return EXIT_ERROR
        return _stream_from_library(args, as_json)

    if not args.plugin or not args.source_id:
        print(
            "error: name a plugin and a source id (rom-hub stream <plugin> "
            "<source_id>), or use --library-rom <id> for a rom your library "
            "already holds",
            file=sys.stderr,
        )
        return EXIT_ERROR

    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "stream")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    result = SearchResult(
        source_id=args.source_id,
        # The identifier is all the CLI knows; the plugin looks the rest up.
        title=args.source_id,
        platform=args.platform,
        plugin=plugin.slug,
    )

    data_assets = prepare_assets(plugin)
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
            data_assets=data_assets,
            secrets=prepare_secrets(plugin),
        ) as proc:
            target = proc.resolve_stream(result)
    finally:
        fetcher.close()

    allowlist = list(plugin.manifest.network)
    try:
        handover = plan_handover(target, allowlist, source=plugin.slug)
    except StreamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    notes: list[str] = []
    opened = ""
    if args.open:
        try:
            opened = open_handover(handover, allowlist)
        except StreamError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    outcome = StreamOutcome(
        handover=handover,
        route=_ask_stream_server(args, target, notes),
        opened=opened,
        notes=notes,
    )
    _print_outcome(outcome, as_json)
    return EXIT_OK


def _ask_stream_server(args, target, notes: list[str]):
    """Ask a configured `romm-stream` whether it could play this platform.

    Optional in the `backends.degrade` sense: the operator's answer -- the
    resolved target -- is already in hand, so a stream server that is down
    or not configured must never turn this command into a failure.
    """
    base = args.server or stream_server_base()
    if not base:
        return None
    platform = args.platform or target.extra.get("platform", "")
    if not platform:
        notes.append(
            "a stream server is configured but this target names no "
            "platform, so there was nothing to ask it about; pass "
            "--platform <slug> to ask"
        )
        return None
    try:
        with StreamServerClient(base) as client:
            return client.route(platform)
    except StreamError as exc:
        notes.append(f"stream server not asked: {exc}")
        return None


def _stream_from_library(args, as_json: bool) -> int:
    """Hand over the library's own player for a rom it already holds.

    No plugin, no subprocess and no connection: the URL is the configured
    backend's base plus the id the operator typed. See
    `stream.library_player_url` for why it is not pre-flighted.
    """
    name = env.get("ROM_HUB_BACKEND") or backends.DEFAULT_BACKEND
    try:
        # Refuse on the player table first: a backend with no player must
        # not be told instead that it is unconfigured.
        library_player_path(name)
        handover = library_handover(
            name, _backend_base_url(name), args.library_rom
        )
    except (BackendError, StreamError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    opened = ""
    if args.open:
        try:
            opened = open_library_url(handover.url)
        except StreamError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    _print_outcome(
        StreamOutcome(handover=handover, opened=opened), as_json
    )
    return EXIT_OK


def _backend_base_url(name: str) -> str:
    """The active backend's base URL, without connecting.

    `library_player_url` needs the base URL and nothing else, and opening
    a backend would authenticate -- a round trip and a token spent to
    build a string.

    Backends spell `settings_from_env()` differently on purpose: one that
    authenticates returns a `(url, user, password)` triple, one that does
    not returns the URL alone. Both shapes are accepted here rather than
    made uniform, because each is correct for the backend that chose it
    and the only thing this function wants is the URL either way.
    """
    module = importlib.import_module(f"rom_hub.backends.{name}.backend")
    settings_from_env = getattr(module, "settings_from_env", None)
    if settings_from_env is None:
        raise BackendError(
            f"backend {name!r} does not expose its connection settings, so "
            f"the Hub cannot build its player URL"
        )
    settings = settings_from_env()
    return settings if isinstance(settings, str) else settings[0]


def _with_cores_plugin(args, action):
    """Start `args.plugin` for a cores call, or return the refusal.

    Both cores subcommands need the same three things -- an installed,
    enabled plugin that declares `cores`, and a running subprocess -- and
    neither needs RomM at all: a core never touches the library.
    """
    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "cores")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    data_assets = prepare_assets(plugin)
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
            data_assets=data_assets,
            secrets=prepare_secrets(plugin),
        ) as proc:
            return action(proc)
    finally:
        fetcher.close()


def _cmd_cores_list(args) -> int:
    def show(proc) -> int:
        cores = proc.cores()
        if not cores:
            print("this plugin offers no cores")
            return EXIT_OK
        # Widths from the data, not from constants. The fixed 14-column
        # SYSTEM this used to print was set before any plugin implemented
        # `cores`; libretro's own system names run to 45 characters
        # ("Nintendo - Super Nintendo Entertainment System"), which pushed
        # every following column out of line and made the listing unreadable
        # for the first plugin that actually produced one. Capped because
        # the widths come from an untrusted plugin -- CoreArtifact bounds
        # each field, but 200 characters of name should not decide the
        # layout of 218 rows.
        rows = [
            (core.core_id, core.version or "-", core.system or "-", core.name)
            for core in cores
        ]
        headers = ("CORE", "VERSION", "SYSTEM", "NAME")
        widths = [
            min(max([len(h), *(len(row[i]) for row in rows)]), 48)
            for i, h in enumerate(headers)
        ]
        print(
            f"{headers[0]:<{widths[0]}} {headers[1]:<{widths[1]}} "
            f"{headers[2]:<{widths[2]}} {headers[3]}"
        )
        for core_id, version, system, name in rows:
            print(
                f"{core_id:<{widths[0]}} {version:<{widths[1]}} "
                f"{system:<{widths[2]}} {name}"
            )
        print()
        print(f"{len(cores)} core(s). Install with: rom-hub cores install "
              f"{args.plugin} <core>")
        return EXIT_OK

    return _with_cores_plugin(args, show)


def _cmd_cores_install(args) -> int:
    def install(proc) -> int:
        core = find_core(proc.cores(), args.core)
        result = install_core(proc, core, cores_dir=cores_dir())
        print(result.message)
        return EXIT_OK

    return _with_cores_plugin(args, install)


def _with_firmware_plugin(args, action):
    """Start `args.plugin` for a firmware call, or return the refusal.

    The same three checks `_with_cores_plugin` makes -- installed, enabled,
    declares the capability -- and the same subprocess. What it does *not*
    do is open a backend: `firmware list` never needs one, and `install`
    decides for itself (see `_cmd_firmware_install`).
    """
    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "firmware")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    data_assets = prepare_assets(plugin)
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
            data_assets=data_assets,
            secrets=prepare_secrets(plugin),
        ) as proc:
            return action(proc)
    finally:
        fetcher.close()


def _cmd_firmware_list(args) -> int:
    def show(proc) -> int:
        items = proc.firmware()
        if not items:
            print("this plugin offers no firmware")
            return EXIT_OK
        # LICENCE is a column, not a footnote. An operator choosing a BIOS
        # is choosing on two axes -- does it fit my system, and am I
        # allowed to have it -- and the second one is the whole reason a
        # firmware plugin is worth installing. Widths come from the data,
        # capped, for the reason `cores list` explains.
        rows = [
            (
                item.firmware_id,
                item.platform,
                item.license,
                item.name,
            )
            for item in items
        ]
        headers = ("FIRMWARE", "PLATFORM", "LICENCE", "NAME")
        widths = [
            min(max([len(h), *(len(row[i]) for row in rows)]), 48)
            for i, h in enumerate(headers)
        ]
        print(
            f"{headers[0]:<{widths[0]}} {headers[1]:<{widths[1]}} "
            f"{headers[2]:<{widths[2]}} {headers[3]}"
        )
        for firmware_id, platform, license_name, name in rows:
            print(
                f"{firmware_id:<{widths[0]}} {platform:<{widths[1]}} "
                f"{license_name:<{widths[2]}} {name}"
            )
        print()
        print(
            f"{len(items)} item(s). Install with: rom-hub firmware install "
            f"{args.plugin} <firmware>"
        )
        return EXIT_OK

    return _with_firmware_plugin(args, show)


def _cmd_firmware_install(args) -> int:
    """Download one firmware item, and file it in the library unless told not to.

    The backend is opened *before* the plugin subprocess starts, so an
    unconfigured Hub costs nothing -- and it is opened at all because the
    common case is wanting the BIOS in both places.

    `--no-library` is the opt-out, and an unconfigured backend without it
    is an error rather than a silent local-only install. That is not a
    contradiction of `FIRMWARE` being an *optional* capability: optional
    describes a backend that genuinely cannot store firmware, which the
    Hub can see and report. A backend nobody has configured is not a
    backend that cannot; it is a question, and guessing "they meant local
    only" is how an operator ends up wondering why RomM never got the
    file.
    """
    backend = None
    if not args.no_library:
        try:
            backend = open_backend()
        except backends.BackendError as exc:
            print(
                f"error: {exc} Firmware still installs without a library -- "
                f"re-run with --no-library to download it into "
                f"{firmware_dir()} and stop there.",
                file=sys.stderr,
            )
            return EXIT_ERROR

    def install(proc) -> int:
        item = find_firmware(proc.firmware(), args.firmware)
        result = install_firmware(
            proc, item, firmware_dir=firmware_dir(), backend=backend
        )
        print(result.message)
        return EXIT_OK

    try:
        return _with_firmware_plugin(args, install)
    finally:
        if backend is not None:
            backend.close()


def _with_assets_plugin(args, action):
    """Start `args.plugin` for an assets call, or return the refusal.

    The same three checks `_with_cores_plugin` makes -- installed, enabled,
    declares the capability -- and, like that one, no backend anywhere near
    it. Unlike `_with_firmware_plugin`, that is not a decision either
    subcommand gets to revisit: an asset never goes into a library, so
    there is nothing a backend could contribute to this command. See
    `rom_hub.emuassets`.
    """
    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "assets")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    data_assets = prepare_assets(plugin)
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
            data_assets=data_assets,
            secrets=prepare_secrets(plugin),
        ) as proc:
            return action(proc)
    finally:
        fetcher.close()


def _cmd_assets_list(args) -> int:
    def show(proc) -> int:
        items = proc.assets()
        if args.kind:
            items = [i for i in items if i.kind == args.kind]
        if not items:
            scope = f" of kind {args.kind!r}" if args.kind else ""
            print(f"this plugin offers no assets{scope}")
            return EXIT_OK

        # LICENCE is a column here for the reason it is one in `firmware
        # list`: every source behind this capability is a community
        # repository of contributed files, the terms genuinely vary, and
        # two candidate sources were dropped from this release because
        # their terms could not be established at all. An operator should
        # not have to leave the terminal to find that out.
        #
        # Widths come from the data, capped, for the reason `cores list`
        # explains -- and the cap earns its keep here, because an asset id
        # is a path within a source tree and runs far longer than a core id.
        rows = [
            (
                item.asset_id,
                item.kind,
                item.system or "-",
                item.license,
                item.name,
            )
            for item in items
        ]
        headers = ("ASSET", "KIND", "SYSTEM", "LICENCE", "NAME")
        widths = [
            min(max([len(h), *(len(row[i]) for row in rows)]), 48)
            for i, h in enumerate(headers)
        ]
        print(
            f"{headers[0]:<{widths[0]}} {headers[1]:<{widths[1]}} "
            f"{headers[2]:<{widths[2]}} {headers[3]:<{widths[3]}} {headers[4]}"
        )
        for asset_id, kind, system, license_name, name in rows:
            print(
                f"{asset_id:<{widths[0]}} {kind:<{widths[1]}} "
                f"{system:<{widths[2]}} {license_name:<{widths[3]}} {name}"
            )
        print()
        print(
            f"{len(items)} asset(s). Install with: rom-hub assets install "
            f"{args.plugin} <asset>"
        )
        return EXIT_OK

    return _with_assets_plugin(args, show)


def _cmd_assets_install(args) -> int:
    def install(proc) -> int:
        item = find_asset(proc.assets(), args.asset)
        result = install_asset(
            proc,
            item,
            assets_dir=assets_dir(),
            overrides=asset_dir_overrides(),
        )
        print(result.message)
        return EXIT_OK

    return _with_assets_plugin(args, install)


def _cmd_backend_info(args) -> int:
    """Which library server the Hub is pointed at, and what it can do.

    **Deliberately connectionless.** The operator most likely to run this
    is the one whose connection is not working yet, or who is about to
    find out that `--collection` will not be honoured. A command that had
    to authenticate first would be useless to both of them, so nothing
    here opens a socket: the capability set is a declaration the backend
    class makes, and whether the settings are present is a question about
    the environment.

    The unsupported list is printed as well as the supported one. A
    capability that is simply absent from a list is easy to read as an
    oversight; one printed under "cannot" is an answer.
    """
    name = args.backend or backend_name()
    info = backends.describe(name)

    print(f"{'backend':<16} {info.name}")
    source = (
        f"ROM_HUB_BACKEND={env.get('ROM_HUB_BACKEND').strip()}"
        if env.get("ROM_HUB_BACKEND").strip()
        else f"default ({backends.DEFAULT_BACKEND})"
    )
    print(f"{'selected by':<16} {source}")
    if info.summary:
        print(f"{'summary':<16} {info.summary}")
    print(f"{'available':<16} {', '.join(backends.available())}")

    missing = [n for n in info.settings if not env.get(n)]
    configured = "yes" if not missing else f"no -- {', '.join(missing)} not set"
    print(f"{'settings':<16} {', '.join(info.settings) or '(none)'}")
    print(f"{'configured':<16} {configured}")

    print()
    print("can:")
    for capability in sorted(info.capabilities):
        help_text = backends.CAPABILITY_HELP.get(capability, "")
        print(f"  {capability:<14} {help_text}")
    cannot = sorted(backends.ALL_CAPABILITIES - info.capabilities)
    if cannot:
        print("cannot:")
        for capability in cannot:
            help_text = backends.CAPABILITY_HELP.get(capability, "")
            print(f"  {capability:<14} {help_text}")
    else:
        print("cannot: (nothing -- this backend supports every capability)")
    return EXIT_OK


def _cmd_jobs(args) -> int:
    state = None
    if args.state:
        try:
            state = JobState(args.state.upper())
        except ValueError:
            legal = ", ".join(s.value for s in JobState)
            print(
                f"error: unknown job state {args.state!r}; expected one of "
                f"{legal}",
                file=sys.stderr,
            )
            return EXIT_ERROR

    with JobQueue(jobs_db_path()) as queue:
        jobs = queue.list(state)

    if not jobs:
        scope = f" in state {state.value}" if state else ""
        print(f"no import jobs{scope}")
        return EXIT_OK

    print(f"{'ID':>5}  {'STATE':<18} {'PLUGIN':<14} {'PLATFORM':<10} SOURCE")
    for job in jobs:
        print(
            f"{job.id:>5}  {job.state.value:<18} {job.plugin:<14} "
            f"{job.platform or '-':<10} {job.source_id}"
        )
        if job.title and job.title != job.source_id:
            print(f"       {job.title}")
        # The error is the whole reason anyone runs this command after a
        # failure, so it is never truncated away.
        if job.error:
            print(f"       ! {job.error}")
        # A note is not an error: the job succeeded, minus something
        # optional the backend cannot do. Marked differently so a DONE
        # import carrying one does not read as broken -- but shown, because
        # a degradation nobody is told about is just a silent difference
        # between what was asked for and what happened.
        if job.notes:
            print(f"       ~ {job.notes}")
    print()
    print(f"{len(jobs)} job(s)")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rom-hub",
        description=(
            "Plugin host for a self-hosted ROM library (ROM Provider "
            "Protocol v1). 'rom-hub backend info' says which library "
            "server is active and what it can do."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plugin = sub.add_parser("plugin", help="manage plugins")
    psub = plugin.add_subparsers(dest="plugin_command", required=True)

    install = psub.add_parser("install", help="install a plugin from a git repo or path")
    install.add_argument("source", help="a catalog slug, git URL, or local path")
    install.add_argument(
        "--ref", default=None, help="branch, tag, or commit SHA to install"
    )
    install.set_defaults(func=_cmd_plugin_install)

    browse = psub.add_parser("browse", help="list plugins in the directory")
    browse.set_defaults(func=_cmd_plugin_browse)

    listing = psub.add_parser("list", help="list installed plugins")
    listing.set_defaults(func=_cmd_plugin_list)

    enable = psub.add_parser("enable")
    enable.add_argument("slug")
    enable.set_defaults(func=_cmd_plugin_enable)

    disable = psub.add_parser("disable")
    disable.add_argument("slug")
    disable.set_defaults(func=_cmd_plugin_disable)

    assets = psub.add_parser(
        "assets",
        help="show the data assets a plugin declares, and fetch them",
    )
    assets.add_argument("slug", help="slug of an installed plugin")
    assets.add_argument(
        "--fetch",
        action="store_true",
        help=(
            "download and verify any that are missing now, instead of on "
            "the next command that needs them"
        ),
    )
    assets.set_defaults(func=_cmd_plugin_assets)

    config = psub.add_parser(
        "config", help="show or change a plugin's settings (secrets redacted)"
    )
    config.add_argument("slug", help="slug of an installed plugin")
    config.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help=(
            "set one declared, non-secret config field, then print the "
            "result; repeatable. A list[str] field takes a comma-separated "
            "value. Secrets are refused here -- use 'plugin secret set'"
        ),
    )
    config.set_defaults(func=_cmd_plugin_config)

    secret = psub.add_parser(
        "secret",
        help="set, clear and inspect a plugin's `secret` config fields",
        description=(
            "A config field declared `type = \"secret\"` is kept out of the "
            "Hub's plain config and redacted from every command's output. "
            "'secret list' says where it is actually stored and what that "
            "does and does not protect."
        ),
    )
    ssub = secret.add_subparsers(dest="secret_command", required=True)

    secret_set = ssub.add_parser(
        "set",
        help="store one secret (prompts on a terminal; nothing is echoed)",
        description=(
            "With no source flag this prompts on a terminal and reads stdin "
            "otherwise, so the value never has to appear in your shell "
            "history."
        ),
    )
    secret_set.add_argument("slug", help="slug of an installed plugin")
    secret_set.add_argument("key", help="the config field, from 'plugin config'")
    source = secret_set.add_mutually_exclusive_group()
    source.add_argument(
        "--stdin", action="store_true", help="read the value from standard input"
    )
    source.add_argument(
        "--env",
        metavar="VAR",
        default=None,
        help="read the value from this environment variable",
    )
    source.add_argument(
        "--value",
        default=None,
        help=(
            "the value itself -- NOT RECOMMENDED: it lands in your shell "
            "history and this machine's process list, and the command warns "
            "you when you use it"
        ),
    )
    secret_set.set_defaults(func=_cmd_plugin_secret_set)

    secret_clear = ssub.add_parser("clear", help="remove one stored secret")
    secret_clear.add_argument("slug")
    secret_clear.add_argument("key")
    secret_clear.set_defaults(func=_cmd_plugin_secret_clear)

    secret_list = ssub.add_parser(
        "list", help="which secrets are set, and what the store protects"
    )
    secret_list.add_argument(
        "slug", nargs="?", default=None, help="only this plugin (default: all)"
    )
    secret_list.set_defaults(func=_cmd_plugin_secret_list)

    platforms = sub.add_parser(
        "platforms",
        help=(
            "which platforms the web player can actually run, which are "
            "catalogue-only, and which plugins import to each"
        ),
    )
    platforms.add_argument(
        "--installed",
        action="store_true",
        help="only the plugins installed and enabled on this host",
    )
    platforms.set_defaults(func=_cmd_platforms)

    search = sub.add_parser(
        "search",
        help="search across enabled plugins, merged and grouped by game",
    )
    search.add_argument("query")
    search.add_argument("--platform", default=None)
    search.add_argument(
        "--limit",
        type=int,
        default=25,
        help=(
            "how many GAMES to show. Counts merged rows, not raw results: "
            "before grouping this was a per-source limit, so ten sources at "
            "--limit 25 meant 250 rows"
        ),
    )
    search.add_argument(
        "--offset",
        type=int,
        default=0,
        help="skip this many merged rows -- paging over the combined set",
    )
    search.add_argument(
        "--per-source",
        type=int,
        default=None,
        dest="per_source",
        help=(
            "how many raw results to ask each source for (default: enough "
            "to fill the page, since grouping collapses rows)"
        ),
    )
    search.add_argument(
        "--expand",
        default=None,
        metavar="ROW",
        help="list the variants of these row numbers, or 'all'",
    )
    search.add_argument(
        "--all-variants",
        action="store_true",
        help="expand every row on the page",
    )
    search.add_argument(
        "--no-group",
        action="store_true",
        help="do not merge anything: one line per raw result, as before",
    )
    search.set_defaults(func=_cmd_search)

    importer = sub.add_parser(
        "import",
        help=(
            "import one item into the library (needs the active backend's "
            "settings -- see 'rom-hub backend info')"
        ),
    )
    importer.add_argument("plugin", help="slug of an installed importer plugin")
    importer.add_argument("source_id", help="the plugin's id for the item")
    importer.add_argument(
        "--platform",
        default=None,
        help="platform slug to file this under, overriding the plugin's",
    )
    importer.add_argument(
        "--collection",
        default=None,
        help=(
            "collection to add it to, overriding the plugin's; refused up "
            "front if the active backend has no collections"
        ),
    )
    importer.add_argument(
        "--allow-unplayable",
        action="store_true",
        help=(
            "do not warn when the platform has no emulator core. The import "
            "happens either way -- this only silences the notice, for a "
            "catalogue you are building on purpose ('rom-hub platforms')"
        ),
    )
    importer.set_defaults(func=_cmd_import)

    enrich = sub.add_parser(
        "enrich",
        help=(
            "enrich one rom's metadata through a plugin (needs the active "
            "backend's settings -- see 'rom-hub backend info')"
        ),
    )
    enrich.add_argument("plugin", help="slug of an installed metadata plugin")
    enrich.add_argument("rom_id", type=int, help="the library's id for the rom")
    enrich.add_argument(
        "--source-id",
        default=None,
        help=(
            "the plugin's own id for this game, when the library's record does not "
            "identify it (a plugin that will not guess says so and names this)"
        ),
    )
    enrich.set_defaults(func=_cmd_enrich)

    streamer = sub.add_parser(
        "stream",
        help="resolve one item to a stream target and hand it over",
        description=(
            "Resolve an item to a playable target and say what to do with "
            "it -- or, with --open, do it. The Hub is not a streaming "
            "server: it resolves, validates and hands over."
        ),
    )
    streamer.add_argument(
        "plugin", nargs="?", help="slug of an installed stream plugin"
    )
    streamer.add_argument(
        "source_id", nargs="?", help="the plugin's id for the item"
    )
    streamer.add_argument(
        "--library-rom",
        type=int,
        default=None,
        metavar="ID",
        help=(
            "skip the plugins: hand over the active backend's own "
            "in-browser player for a rom the library already holds"
        ),
    )
    streamer.add_argument(
        "--open",
        action="store_true",
        help=(
            "open the resolved URL in a browser; refused for a target that "
            "is a handle rather than a URL"
        ),
    )
    streamer.add_argument(
        "--json",
        action="store_true",
        help="print the handover as JSON, for a launcher to consume",
    )
    streamer.add_argument(
        "--platform",
        default=None,
        help=(
            "platform slug for this item, used to ask a configured stream "
            "server whether it could play it"
        ),
    )
    streamer.add_argument(
        "--server",
        default=None,
        metavar="URL",
        help=(
            f"a romm-stream server to ask about playability, overriding "
            f"${STREAM_SERVER_ENV}; only its read-only routing endpoints "
            f"are called and no session is started"
        ),
    )
    streamer.set_defaults(func=_cmd_stream)

    cores = sub.add_parser("cores", help="list and install emulator cores")
    csub = cores.add_subparsers(dest="cores_command", required=True)

    cores_list = csub.add_parser("list", help="list the cores a plugin offers")
    cores_list.add_argument("plugin", help="slug of an installed cores plugin")
    cores_list.set_defaults(func=_cmd_cores_list)

    cores_install = csub.add_parser(
        "install", help="download one core into the configured cores directory"
    )
    cores_install.add_argument("plugin", help="slug of an installed cores plugin")
    cores_install.add_argument("core", help="the core id, from 'cores list'")
    cores_install.set_defaults(func=_cmd_cores_install)

    assets = sub.add_parser(
        "assets",
        help=(
            "list and install emulator support files: shaders, overlays, "
            "cheats, controller profiles"
        ),
    )
    asub = assets.add_subparsers(dest="assets_command", required=True)

    assets_list = asub.add_parser(
        "list", help="list the support files a plugin offers, with each licence"
    )
    assets_list.add_argument("plugin", help="slug of an installed assets plugin")
    assets_list.add_argument(
        "--kind",
        choices=list(KNOWN_ASSET_KINDS),
        help="show only this kind of support file",
    )
    assets_list.set_defaults(func=_cmd_assets_list)

    assets_install = asub.add_parser(
        "install",
        help=(
            "download one support file into the directory configured for "
            "its kind (no library server is involved)"
        ),
    )
    assets_install.add_argument("plugin", help="slug of an installed assets plugin")
    assets_install.add_argument("asset", help="the asset id, from 'assets list'")
    assets_install.set_defaults(func=_cmd_assets_install)

    firmware = sub.add_parser(
        "firmware", help="list and install BIOS/firmware a plugin offers"
    )
    fsub = firmware.add_subparsers(dest="firmware_command", required=True)

    firmware_list = fsub.add_parser(
        "list", help="list the firmware a plugin offers, with each item's licence"
    )
    firmware_list.add_argument("plugin", help="slug of an installed firmware plugin")
    firmware_list.set_defaults(func=_cmd_firmware_list)

    firmware_install = fsub.add_parser(
        "install",
        help=(
            "download one firmware item into the configured firmware "
            "directory, and store it in the library too unless --no-library"
        ),
    )
    firmware_install.add_argument(
        "plugin", help="slug of an installed firmware plugin"
    )
    firmware_install.add_argument(
        "firmware", help="the firmware id, from 'firmware list'"
    )
    firmware_install.add_argument(
        "--no-library",
        action="store_true",
        help=(
            "download the files and stop; do not open the library backend "
            "at all"
        ),
    )
    firmware_install.set_defaults(func=_cmd_firmware_install)

    jobs = sub.add_parser("jobs", help="list import jobs")
    jobs.add_argument(
        "--state",
        default=None,
        help="only jobs in this state (e.g. FAILED, DONE, PENDING)",
    )
    jobs.set_defaults(func=_cmd_jobs)

    backend = sub.add_parser("backend", help="inspect the library backend")
    bsub = backend.add_subparsers(dest="backend_command", required=True)
    backend_info = bsub.add_parser(
        "info", help="show the active backend and what it can do"
    )
    backend_info.add_argument(
        "--backend",
        default=None,
        help=(
            "inspect this backend instead of the active one "
            f"(default: $ROM_HUB_BACKEND, or {backends.DEFAULT_BACKEND})"
        ),
    )
    backend_info.set_defaults(func=_cmd_backend_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Before anything can print -- argparse's own help and usage errors
    # included, since a plugin slug can reach those too.
    configure_output_encoding()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        RegistryError,
        ManifestError,
        CatalogError,
        PluginCallError,
        CoreError,
        FirmwareError,
        AssetInstallError,
        EnrichError,
        AssetError,
        BackendError,
        SecretError,
        OSError,
    ) as exc:
        # ManifestError escapes a bad manifest on an otherwise clean install,
        # and OSError is what a read-only or nonexistent ROM_HUB_HOME gives
        # from Registry.__init__'s mkdir. PluginCallError covers `import`,
        # which -- unlike `search`, where the dispatcher isolates each plugin
        # -- talks to one PluginProcess directly, so a SandboxRefused from
        # start() would otherwise reach the operator as a traceback with the
        # opt-out buried in it. EnrichError and BackendError are `enrich`'s
        # equivalents: unlike an import, an enrich has no job record to fail
        # into, so its failures reach the operator only here. BackendError is
        # one name for every backend's failures, including RomM's RommError
        # and the three refusals that keep this honest -- UnknownBackend,
        # BackendNotConfigured and CapabilityUnsupported. AssetError is the
        # data-asset equivalent, and its most important case -- a declared
        # sha256 that does not match what arrived -- is a refusal an
        # operator must be able to read, not a stack trace. SecretError is
        # the same shape for the credential store -- "the stored secret did
        # not authenticate", "ROM_HUB_SECRET_STORE=keyring but there is no
        # keyring here", "the two values did not match" -- refusals with a
        # next step in them.
        #
        # AssetInstallError is the *other* kind of asset failure and the two
        # are not the same thing despite the names: AssetError is a plugin's
        # own dataset going wrong, AssetInstallError is `rom-hub assets
        # install` going wrong. See the header of `rom_hub.emuassets` for why
        # both words are called "asset". Its commonest case is a mistyped id
        # out of a catalogue thousands of items long, where the message
        # carries the near-misses and would be useless underneath a
        # traceback. None of these deserve one.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
