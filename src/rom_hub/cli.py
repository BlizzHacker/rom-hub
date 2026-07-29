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
import sys
from pathlib import Path

from . import backends, env
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
from .dispatcher import search_all
from .importer import run_import
from .jobs import JobQueue, JobState
from .manifest import ManifestError
from .metadata import EnrichError, rom_ref_from, run_enrich
from .registry import Registry, RegistryError
from .sandbox import probe
from .types import SearchResult

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


def _cmd_plugin_enable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, True)
    print(f"enabled {args.slug}")
    return 0


def _cmd_plugin_disable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, False)
    print(f"disabled {args.slug}")
    return 0


def _cmd_search(args) -> int:
    plugins = Registry(default_root()).installed()
    searchable = [p for p in plugins if p.enabled and "search" in p.manifest.capabilities]
    if not searchable:
        print("no plugins available for search — install one with 'rom-hub plugin install'")
        return 0

    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            plugins,
            fetcher=fetcher,
            query=args.query,
            platform=args.platform,
            limit=args.limit,
            allow_unsandboxed=allow_unsandboxed(),
        )
    finally:
        fetcher.close()

    for r in outcome.results:
        size = f"{r.size_bytes / 1_048_576:.1f} MB" if r.size_bytes else "-"
        flag = " [stream-only]" if r.extra.get("stream_only") == "true" else ""
        print(f"{r.plugin:<14} {r.platform or '-':<12} {size:>10}  {r.title}{flag}")

    print()
    print(f"{outcome.responded} of {outcome.total} sources responded, {len(outcome.results)} results")
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
            ) as proc,
        ):
            outcome = run_import(
                _PlanOverrides(proc, args.platform, args.collection),
                result,
                backend=backend,
                queue=queue,
                download_dir=downloads_dir(root),
            )
    finally:
        fetcher.close()
        backend.close()

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
    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
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


def _cmd_stream(args) -> int:
    """Resolve one item to a stream target and print it.

    Deliberately the whole command. `romm-stream` is a separate service and
    integrating it is not this capability's job: the contract is "a plugin
    resolves an item, the host validates the answer", and printing the
    validated answer is exactly as far as the Hub goes.
    """
    plugin = Registry(default_root()).get(args.plugin)
    refusal = _require_capability(plugin, "stream")
    if refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return EXIT_ERROR

    result = SearchResult(
        source_id=args.source_id,
        # The identifier is all the CLI knows; the plugin looks the rest up.
        title=args.source_id,
        plugin=plugin.slug,
    )

    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
        ) as proc:
            target = proc.resolve_stream(result)
    finally:
        fetcher.close()

    print(f"{target.kind}\t{target.target}")
    if target.title:
        print(f"title\t{target.title}")
    if target.mime_type:
        print(f"type\t{target.mime_type}")
    for key in sorted(target.extra):
        print(f"{key}\t{target.extra[key]}")
    return EXIT_OK


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

    fetcher = HttpxFetcher()
    try:
        with PluginProcess(
            plugin_dir=plugin.path,
            manifest=plugin.manifest,
            config=plugin.config,
            fetcher=fetcher,
            allow_unsandboxed=allow_unsandboxed(),
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

    search = sub.add_parser("search", help="search across enabled plugins")
    search.add_argument("query")
    search.add_argument("--platform", default=None)
    search.add_argument("--limit", type=int, default=25)
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

    stream = sub.add_parser(
        "stream", help="resolve one item to a stream target and print it"
    )
    stream.add_argument("plugin", help="slug of an installed stream plugin")
    stream.add_argument("source_id", help="the plugin's id for the item")
    stream.set_defaults(func=_cmd_stream)

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
        EnrichError,
        BackendError,
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
        # BackendNotConfigured and CapabilityUnsupported. None of these
        # deserve a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
