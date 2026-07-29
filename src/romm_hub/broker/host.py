"""Supervises one plugin subprocess and brokers everything privileged.

The plugin gets no RomM token, no filesystem mount, and no sockets. Its
only way out is an `http.get` call that lands in _serve_plugin_call(),
where the manifest allowlist is enforced before any fetch happens.
"""

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

    def _kill_for_timeout(self) -> None:
        """Watchdog. Killing the process unblocks the host's pending read."""
        self._timed_out = True
        if self._proc is not None:
            self._proc.kill()

    def _call(self, method: str, params: dict):
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise PluginCallError("plugin process is not running")

        call_id = self._next_id()
        write_message(
            self._proc.stdin,
            {"kind": "call", "id": call_id, "method": method, "params": params},
        )

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
                    raise PluginCallError(
                        f"plugin {self.manifest.slug}: {exc}"
                    ) from exc

                if msg is None:
                    break

                if msg["kind"] == "call":
                    self._serve_plugin_call(msg)
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

        if self._timed_out:
            raise PluginCallError(
                f"plugin {self.manifest.slug} timed out after {self.timeout}s "
                f"during {method!r} and was killed"
            )
        stderr = ""
        if self._proc is not None and self._proc.stderr:
            stderr = self._proc.stderr.read()
        raise PluginCallError(
            f"plugin {self.manifest.slug} exited during {method!r}: "
            f"{stderr.strip() or 'no stderr'}"
        )

    def _serve_plugin_call(self, msg: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        call_id = msg["id"]
        try:
            if msg["method"] != "http.get":
                raise PluginCallError(f"unsupported host method {msg['method']!r}")
            url = msg["params"]["url"]
            # The enforcement point. Nothing below runs for a blocked URL.
            check_url(url, self.manifest.network)
            status, text = self.fetcher.get(url, msg["params"].get("params") or {})
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
        write_message(self._proc.stdin, reply)

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
            for stream in (self._proc.stdout, self._proc.stderr):
                if stream:
                    stream.close()
            self._proc = None
