"""Supervises one plugin subprocess and brokers everything privileged.

The plugin gets no RomM token, no filesystem mount, and no sockets. Every
path that leads out is gated by the same manifest allowlist before any
socket is opened:

  * `http.get`, which the plugin calls, enforced in _serve_plugin_call()
  * the `FetchPlan` returned by plan(), whose URLs the *host* fetches later,
    enforced in plan()
  * the `artwork_url` on the `MetadataPatch` returned by enrich(), which the
    host fetches later, enforced in enrich()
  * the `StreamTarget` returned by resolve_stream(), whose `url` kind is
    something a player will fetch, enforced in resolve_stream()
  * the `FetchPlan` returned by core_plan(), which is the import gate reused
    verbatim -- a core is a binary landing on disk, like a ROM

Adding another path without a check_url() on it would make the manifest's
`network` declaration decorative.

There is also a path that leads *in*: the subprocess environment. Popen
copies the parent's by default, so every secret the operator's shell
happens to hold would arrive inside the plugin for free. That one is
default-deny too — see `SAFE_ENV_VARS`.

And one path that leads in *by design*: `data_assets`. A plugin whose
source is a file rather than a service declares it in the manifest, and
the caller (`rom_hub.assets.ensure_assets`) fetches, hash-verifies and
caches it *before* this process is started. What arrives here is a
`{name: path}` mapping for bytes that already match a declared sha256 —
this class never fetches one, and never accepts a path the plugin named.
"""

import collections
import os
import subprocess
import sys
import threading
from pathlib import Path

from pydantic import ValidationError

from rom_hub.manifest import Manifest
from rom_hub.netpolicy import PolicyViolation, check_url
from rom_hub.protocol import ProtocolError, read_message, write_message
from rom_hub.types import (
    MAX_CORES_PER_PLUGIN,
    CoreArtifact,
    FetchPlan,
    MetadataPatch,
    RomRef,
    SearchResult,
    StreamTarget,
)

from .fetcher import Fetcher

# Enough stderr to diagnose a crash, bounded so a chatty plugin cannot make
# the host's own memory its problem.
STDERR_TAIL_LINES = 100
STDERR_TAIL_CHARS = 4000

# The environment a plugin subprocess is allowed to see. An ALLOWLIST: the
# child's environment is built from `{}` upward, never from `os.environ`
# downward.
#
# Why it has to be this shape. Popen hands the parent's whole environment to
# the child by default, and Phase 2's `import` reads the RomM password from
# exactly there -- so `os.environ["ROMM_PASSWORD"]` inside plugin code needed
# no socket, no file, and no syscall the seccomp filter can even see. The
# first fix was a denylist of the three ROMM_* names. It stopped those three
# and passed through 92 other variables, including the operator's real GitHub
# token and DeepSeek API key. The next secret is always the one nobody
# listed, and unlike a socket or a path there is no second line of defence
# behind the environment.
#
# So this is default-deny, matching what the codebase already does twice:
# `manifest.py` rejects every unknown key, `netpolicy` refuses every
# undeclared host. This is the third instance of the same rule.
#
# Nothing here is user-defined or secret-shaped; each entry is something a
# Python interpreter needs to start and import its own code:
_COMMON_ENV_VARS = (
    # Locating the interpreter's own helpers and anything it shells to.
    "PATH",
)
_WINDOWS_ENV_VARS = (
    # Windows needs these to load DLLs and resolve the system directory;
    # without SYSTEMROOT the interpreter fails to import parts of the
    # standard library at all.
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    # tempfile has no usable default on Windows without one of these.
    "TEMP",
    "TMP",
    # Windows resolves the PER-USER site-packages through APPDATA, and
    # `pip install --user` is the default outside a venv. Without it the
    # child cannot import rom_hub_sdk at all and every plugin dies at
    # startup with a bare ModuleNotFoundError. POSIX gets the equivalent
    # from HOME, which is why this only bites here. It is a path, not a
    # secret. Verified: allowlist alone -> ModuleNotFoundError;
    # + APPDATA -> imports fine; + LOCALAPPDATA -> still fails.
    "APPDATA",
)
_POSIX_ENV_VARS = (
    "HOME",
    "TMPDIR",
)

# Deliberately NOT inherited, beyond the obvious secrets:
#
#   PYTHONPATH   -- a plugin's import path must come from the interpreter
#                   the host chose, not from the shell that launched it.
#   PYTHONHOME   -- same reasoning, more forcefully.
#   LANG/LC_ALL  -- would only matter for stdio encoding, and FORCED_ENV
#                   pins that directly, so there is nothing left for them
#                   to decide.
#
# If a plugin ever legitimately needs a variable, that is a manifest
# declaration to be designed -- an explicit, reviewable grant like
# `permissions.network` -- not a hole reopened here.

SAFE_ENV_VARS = _COMMON_ENV_VARS + (
    _WINDOWS_ENV_VARS if sys.platform == "win32" else _POSIX_ENV_VARS
)

# Set by the host rather than inherited. The protocol is UTF-8 JSON over
# pipes and the host opens them with encoding="utf-8", so the child's stdio
# must agree regardless of the ambient locale -- which is also why LANG and
# LC_ALL do not need to be inherited.
FORCED_ENV = {"PYTHONIOENCODING": "utf-8"}


def plugin_environment(base: dict | None = None) -> dict:
    """Build a plugin subprocess's environment from nothing.

    Starts empty and adds only `SAFE_ENV_VARS` that are actually present,
    plus `FORCED_ENV`. A variable absent from the parent stays absent; a
    variable the host has never heard of never arrives.
    """
    source = os.environ if base is None else base
    env = {name: source[name] for name in SAFE_ENV_VARS if name in source}
    env.update(FORCED_ENV)
    return env


class PluginCallError(Exception):
    """A plugin call failed: it raised, timed out, or violated policy."""


class SandboxRefused(PluginCallError):
    """The plugin could not be confined and the policy does not permit that."""


class PluginProcess:
    def __init__(
        self,
        plugin_dir: Path,
        manifest: Manifest,
        config: dict,
        fetcher: Fetcher,
        timeout: float = 30.0,
        allow_unsandboxed: bool = False,
        data_assets: dict[str, str] | None = None,
    ):
        self.plugin_dir = Path(plugin_dir)
        self.manifest = manifest
        self.config = config
        self.fetcher = fetcher
        self.timeout = timeout
        self.allow_unsandboxed = allow_unsandboxed
        # Resolved and verified by the caller. Absent is legal and means
        # "this plugin gets no assets" -- a capability that needs one then
        # refuses with its own message, which is better than this class
        # guessing that a fetch would have been wanted here.
        self.data_assets = dict(data_assets or {})
        self.sandboxed = False
        self.sandbox_reason = "not started"
        self._proc: subprocess.Popen | None = None
        self._counter = 0
        self._timed_out = False
        self._dead = False
        self._stderr_tail: collections.deque[str] = collections.deque(
            maxlen=STDERR_TAIL_LINES
        )
        self._stderr_thread: threading.Thread | None = None

    def __enter__(self) -> "PluginProcess":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _next_id(self) -> str:
        self._counter += 1
        return f"h{self._counter}"

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "rom_hub_sdk.runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(self.plugin_dir),
            env=plugin_environment(),
        )
        # stderr must be drained for as long as the process lives. The host
        # blocks reading stdout, so if nobody reads stderr the plugin's first
        # ~64 KB of ordinary logging fills that pipe, its write blocks, it
        # never answers, and the host reports a timeout that points nowhere
        # near the cause. DEVNULL would also fix the deadlock, at the price of
        # the only debugging signal a plugin author has.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self._proc.stderr,), daemon=True
        )
        self._stderr_thread.start()
        reply = self._call(
            "init",
            {
                "plugin_dir": str(self.plugin_dir),
                "entrypoints": self.manifest.capabilities,
                "config": self.config,
                "data_assets": self.data_assets,
            },
        )
        self.sandboxed = bool(reply.get("sandboxed", False))
        self.sandbox_reason = reply.get("sandbox_reason", "no reason reported")
        if not self.sandboxed and not self.allow_unsandboxed:
            self.close()
            raise SandboxRefused(
                f"refusing to run plugin {self.manifest.slug} unsandboxed: "
                f"{self.sandbox_reason}. Its declared network allowlist cannot "
                f"be enforced against a hostile plugin here. Set "
                f"ROM_HUB_ALLOW_UNSANDBOXED=1 to override for development."
            )

    def _drain_stderr(self, stream) -> None:
        """Consume stderr forever, keeping only the tail. Never raises."""
        try:
            for line in stream:
                self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass  # the pipe went away with the process; nothing left to drain

    def _stderr_snapshot(self) -> str:
        """The tail of stderr, after giving the drain a moment to catch up."""
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
        return "".join(self._stderr_tail)[-STDERR_TAIL_CHARS:].strip()

    def _mark_dead(self) -> None:
        """This process can serve no further calls; its pipes are unusable.

        close() still reaps it -- being dead is not being reaped.
        """
        self._dead = True
        if self._proc is not None:
            try:
                self._proc.kill()
            except (OSError, ValueError):
                pass

    def _kill_for_timeout(self) -> None:
        """Watchdog. Killing the process unblocks the host's pending read."""
        self._timed_out = True
        self._mark_dead()

    def _call(self, method: str, params: dict):
        if (
            self._dead
            or self._proc is None
            or self._proc.stdin is None
            or self._proc.stdout is None
        ):
            raise PluginCallError("plugin process is not running")

        # A verdict from an earlier deadline is not evidence about this call.
        self._timed_out = False
        call_id = self._next_id()
        try:
            write_message(
                self._proc.stdin,
                {"kind": "call", "id": call_id, "method": method, "params": params},
            )
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise PluginCallError(
                f"plugin {self.manifest.slug}: cannot send {method!r}: {exc}"
            ) from exc

        # A blocking read on a subprocess pipe cannot be given a deadline
        # portably, so the deadline is enforced by killing the peer: the
        # read then returns EOF and we report the timeout.
        watchdog = threading.Timer(self.timeout, self._kill_for_timeout)
        watchdog.daemon = True
        watchdog.start()
        try:
            while True:
                try:
                    msg = read_message(self._proc.stdout)
                except (ProtocolError, ValueError) as exc:
                    if self._timed_out:
                        break
                    # A rejected or unparseable frame can leave the stream
                    # mid-message, and there is no resyncing it -- see the
                    # size cap in protocol.py. The process is finished.
                    self._mark_dead()
                    raise PluginCallError(
                        f"plugin {self.manifest.slug}: {exc}"
                    ) from exc

                if msg is None:
                    break

                if msg["kind"] == "call":
                    if not self._serve_plugin_call(msg):
                        # The reply pipe is gone. Leaving the loop lands on
                        # the timeout/exited reporting below, which is the
                        # accurate diagnosis; re-raising the OSError here
                        # would not be.
                        break
                    continue

                if msg.get("id") != call_id:
                    continue

                if msg["kind"] == "error":
                    raise PluginCallError(
                        f"plugin {self.manifest.slug}: {msg['error']['message']}"
                    )
                return msg["result"]
        finally:
            watchdog.cancel()
            # Read the verdict once, here, rather than letting a watchdog that
            # fires during teardown change the answer underneath the caller.
            timed_out = self._timed_out

        # Every path out of the loop is terminal for this process.
        self._mark_dead()
        if timed_out:
            raise PluginCallError(
                f"plugin {self.manifest.slug} timed out after {self.timeout}s "
                f"during {method!r} and was killed"
            )
        raise PluginCallError(
            f"plugin {self.manifest.slug} exited during {method!r}: "
            f"{self._stderr_snapshot() or 'no stderr'}"
        )

    def _serve_plugin_call(self, msg: dict) -> bool:
        """Answer one plugin-initiated call. False if the reply could not be sent.

        This runs synchronously inside _call's read loop, so a plugin that
        issues host-bound calls without consuming the replies fills the reply
        pipe, blocks the host here, and then blocks itself on its own stdout.
        The watchdog's kill is what unblocks this write -- correctly -- but it
        unblocks it by breaking the pipe, and the resulting OSError is not the
        PluginCallError the caller was promised.
        """
        assert self._proc is not None and self._proc.stdin is not None
        # read_message has already guaranteed id/method/params, but this whole
        # block indexes peer-controlled data, so it stays inside the try:
        # defence in depth costs nothing here.
        call_id = msg.get("id")
        try:
            if msg["method"] != "http.get":
                raise PluginCallError(f"unsupported host method {msg['method']!r}")
            params = msg.get("params") or {}
            url = params["url"]
            # The enforcement point. Nothing below runs for a blocked URL.
            check_url(url, self.manifest.network)
            status, text = self.fetcher.get(url, params.get("params") or {})
            reply = {
                "kind": "result",
                "id": call_id,
                "result": {"status_code": status, "text": text},
            }
        except (PolicyViolation, PluginCallError) as exc:
            reply = {"kind": "error", "id": call_id, "error": {"message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            reply = {
                "kind": "error",
                "id": call_id,
                "error": {"message": f"{type(exc).__name__}: {exc}"},
            }
        try:
            write_message(self._proc.stdin, reply)
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        raw = self._call(
            "search", {"query": query, "platform": platform, "limit": limit}
        )
        if not isinstance(raw, list):
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned {type(raw).__name__}, "
                "expected a list"
            )
        results = []
        for item in raw[:limit]:
            try:
                result = SearchResult(**item)
            except (ValidationError, TypeError) as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} returned an invalid result: {exc}"
                ) from exc
            result.plugin = self.manifest.slug
            results.append(result)
        return results

    def plan(self, result: SearchResult) -> FetchPlan:
        """Ask the plugin what to fetch. The host, not the plugin, fetches it.

        A FetchPlan is the second channel by which a plugin can make the host
        reach a host: ctx.http asks directly, this hands over URLs and asks
        the host to go get them. Both are gated by the same allowlist, or the
        manifest's `network` declaration means nothing for imports.
        """
        return self._gated_plan(self._call("plan", {"result": result.model_dump()}))

    def _gated_plan(self, raw) -> FetchPlan:
        """Re-establish a FetchPlan host-side and allowlist every URL in it.

        Shared by `plan()` (a ROM) and `core_plan()` (an emulator core),
        because those are the same privileged act with a different
        destination: a plugin naming URLs the host will fetch. One
        implementation, so a gate added to one is a gate on both.
        """
        # The plugin is under no obligation to have used FetchPlan to build
        # this; the runner only calls model_dump() on whatever it returned.
        # So the shape is re-established here, on the trusted side.
        if not isinstance(raw, dict):
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid FetchPlan: "
                f"expected an object, got {type(raw).__name__}"
            )
        try:
            plan = FetchPlan(**raw)
        except (ValidationError, TypeError) as exc:
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid FetchPlan: {exc}"
            ) from exc
        # Every file, not just the first: a plan whose opening entry is
        # legitimate must not be able to carry an undeclared host in behind
        # it. One violation refuses the whole plan, so no import can start on
        # the valid half of a plan whose other half was rejected.
        for index, f in enumerate(plan.files):
            try:
                check_url(f.url, self.manifest.network)
            except PolicyViolation as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} FetchPlan rejected "
                    f"(file {index}, {f.filename!r}): {exc}"
                ) from exc
        return plan

    def cores(self) -> list[CoreArtifact]:
        """The emulator cores this plugin offers. A catalogue, nothing more.

        Nothing here is fetched: `core_plan()` is what turns one of these
        into URLs, and that goes through the same gate a ROM import does.
        """
        raw = self._call("list_cores", {})
        if not isinstance(raw, list):
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned {type(raw).__name__}, "
                "expected a list of cores"
            )
        if len(raw) > MAX_CORES_PER_PLUGIN:
            raise PluginCallError(
                f"plugin {self.manifest.slug} offered {len(raw)} cores, over the "
                f"{MAX_CORES_PER_PLUGIN} limit"
            )
        cores = []
        for item in raw:
            try:
                cores.append(CoreArtifact(**item))
            except (ValidationError, TypeError) as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} returned an invalid core: {exc}"
                ) from exc
        return cores

    def core_plan(self, core: CoreArtifact) -> FetchPlan:
        """Ask the plugin what to fetch for one core. The host fetches it.

        Same type and same gate as an import plan, deliberately: a core is
        a binary from the internet landing on the operator's disk, which is
        every bit as privileged as a ROM is.
        """
        return self._gated_plan(self._call("plan_core", {"core": core.model_dump()}))

    def enrich(self, rom: RomRef) -> MetadataPatch:
        """Ask the plugin what to change about a rom. The host changes it.

        Third channel of the same kind as `plan()`: a MetadataPatch can
        carry an `artwork_url` that the *host* will fetch, so it gets the
        same allowlist gate. Everything else in the patch is re-validated
        here too -- the runner only calls model_dump() on whatever the
        plugin returned, so the field allowlists in MetadataPatch are only
        real on this side of the pipe.
        """
        raw = self._call("enrich", {"rom": rom.model_dump()})
        if not isinstance(raw, dict):
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid MetadataPatch: "
                f"expected an object, got {type(raw).__name__}"
            )
        try:
            patch = MetadataPatch(**raw)
        except (ValidationError, TypeError) as exc:
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid MetadataPatch: "
                f"{exc}"
            ) from exc
        if patch.artwork_url is not None:
            try:
                check_url(patch.artwork_url, self.manifest.network)
            except PolicyViolation as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} MetadataPatch rejected "
                    f"(artwork_url): {exc}"
                ) from exc
        return patch

    def resolve_stream(self, result: SearchResult) -> StreamTarget:
        """Ask the plugin where an item can be played. The host only checks.

        Thin on purpose: streaming is `romm-stream`'s job, so the host
        validates the answer and returns it rather than opening anything.
        The check is still not optional -- a `url` target is something that
        will be fetched by whatever is pointed at it, so it goes through
        the same allowlist as every other URL a plugin hands over.
        """
        raw = self._call("resolve", {"result": result.model_dump()})
        if not isinstance(raw, dict):
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid StreamTarget: "
                f"expected an object, got {type(raw).__name__}"
            )
        try:
            target = StreamTarget(**raw)
        except (ValidationError, TypeError) as exc:
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned an invalid StreamTarget: "
                f"{exc}"
            ) from exc
        if target.kind == "url":
            try:
                check_url(target.target, self.manifest.network)
            except PolicyViolation as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} StreamTarget rejected: {exc}"
                ) from exc
        return target

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except (OSError, ValueError):
            # Already killed by the watchdog; the pipe is gone.
            pass
        finally:
            # The drain sees EOF once the process is gone. Join before
            # closing so the thread is not reading a closed pipe.
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=5)
                self._stderr_thread = None
            for stream in (self._proc.stdout, self._proc.stderr):
                if stream:
                    stream.close()
            self._proc = None
