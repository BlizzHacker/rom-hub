"""Supervises one plugin subprocess and brokers everything privileged.

The plugin gets no RomM token, no filesystem mount, and no sockets. Its
only way out is an `http.get` call that lands in _serve_plugin_call(),
where the manifest allowlist is enforced before any fetch happens.
"""

import collections
import subprocess
import sys
import threading
from pathlib import Path

from pydantic import ValidationError

from romm_hub.manifest import Manifest
from romm_hub.netpolicy import PolicyViolation, check_url
from romm_hub.protocol import ProtocolError, read_message, write_message
from romm_hub.types import SearchResult

from .fetcher import Fetcher

# Enough stderr to diagnose a crash, bounded so a chatty plugin cannot make
# the host's own memory its problem.
STDERR_TAIL_LINES = 100
STDERR_TAIL_CHARS = 4000


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
    ):
        self.plugin_dir = Path(plugin_dir)
        self.manifest = manifest
        self.config = config
        self.fetcher = fetcher
        self.timeout = timeout
        self.allow_unsandboxed = allow_unsandboxed
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
            [sys.executable, "-m", "romm_hub_sdk.runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(self.plugin_dir),
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
                f"ROMM_HUB_ALLOW_UNSANDBOXED=1 to override for development."
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
