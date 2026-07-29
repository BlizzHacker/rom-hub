"""Plugin subprocess entrypoint.

Started by the host as `python -m romm_hub_sdk.runner`. Reads the plugin
directory and entrypoints from the handshake, then serves capability calls
until stdin closes.
"""

import importlib
import sys
import traceback
from typing import Any

from romm_hub.protocol import read_message, write_message
from romm_hub.sandbox import SandboxUnavailable, install as install_sandbox
from romm_hub.types import CoreArtifact, RomRef, SearchResult

from .context import HttpClient, PluginContext


class StdioChannel:
    """Duplex channel over the process's own stdin/stdout."""

    def __init__(self, stdin, stdout):
        self._stdin = stdin
        self._stdout = stdout

    def send(self, msg: dict) -> None:
        write_message(self._stdout, msg)

    def await_result(self, call_id: str) -> Any:
        while True:
            msg = read_message(self._stdin)
            if msg is None:
                raise RuntimeError("host closed the connection mid-call")
            if msg.get("id") != call_id:
                continue
            if msg["kind"] == "error":
                raise RuntimeError(msg["error"]["message"])
            return msg["result"]


def _load(entrypoint: str, ctx: PluginContext):
    module_name, _, class_name = entrypoint.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)(ctx)


def _sandbox_state() -> dict:
    """Install confinement if possible; describe the outcome either way.

    Called during init, before any plugin module is imported. Never raises:
    the host decides whether an unsandboxed plugin may proceed.
    """
    try:
        install_sandbox()
    except SandboxUnavailable as exc:
        return {"sandboxed": False, "sandbox_reason": str(exc)}
    return {"sandboxed": True, "sandbox_reason": "seccomp filter installed"}


def run_plugin(stdin, stdout) -> None:
    channel = StdioChannel(stdin, stdout)
    instances: dict[str, Any] = {}
    ctx: PluginContext | None = None
    entrypoints: dict[str, str] = {}

    while True:
        msg = read_message(stdin)
        if msg is None:
            return
        if msg["kind"] != "call":
            continue

        call_id = msg["id"]
        method = msg["method"]
        params = msg.get("params") or {}

        try:
            if method == "init":
                sys.path.insert(0, params["plugin_dir"])
                entrypoints = params["entrypoints"]
                ctx = PluginContext(
                    config=params.get("config") or {}, http=HttpClient(channel)
                )
                # Confine BEFORE any plugin code can be imported by _load().
                result: Any = {"ok": True, **_sandbox_state()}
            elif method == "search":
                if ctx is None:
                    raise RuntimeError("init must be called before search")
                if "search" not in instances:
                    instances["search"] = _load(entrypoints["search"], ctx)
                results = instances["search"].search(
                    params["query"], params.get("platform"), params.get("limit", 50)
                )
                result = [r.model_dump() for r in results]
            elif method == "plan":
                if ctx is None:
                    raise RuntimeError("init must be called before plan")
                if "importer" not in instances:
                    instances["importer"] = _load(entrypoints["importer"], ctx)
                plan = instances["importer"].plan(SearchResult(**params["result"]))
                # Whatever shape this dump has, the host re-validates it and
                # re-checks every URL against the allowlist. Nothing decided
                # on this side of the pipe is load-bearing.
                result = plan.model_dump()
            elif method == "list_cores":
                if ctx is None:
                    raise RuntimeError("init must be called before list_cores")
                if "cores" not in instances:
                    instances["cores"] = _load(entrypoints["cores"], ctx)
                result = [c.model_dump() for c in instances["cores"].list()]
            elif method == "plan_core":
                if ctx is None:
                    raise RuntimeError("init must be called before plan_core")
                if "cores" not in instances:
                    instances["cores"] = _load(entrypoints["cores"], ctx)
                core_plan = instances["cores"].plan(CoreArtifact(**params["core"]))
                # Re-validated and re-gated host-side by the same code that
                # gates an import plan.
                result = core_plan.model_dump()
            elif method == "resolve":
                if ctx is None:
                    raise RuntimeError("init must be called before resolve")
                if "stream" not in instances:
                    instances["stream"] = _load(entrypoints["stream"], ctx)
                target = instances["stream"].resolve(
                    SearchResult(**params["result"])
                )
                # Re-validated host-side like every other capability's
                # return value; a `url` target is allowlist-checked there.
                result = target.model_dump()
            elif method == "enrich":
                if ctx is None:
                    raise RuntimeError("init must be called before enrich")
                if "metadata" not in instances:
                    instances["metadata"] = _load(entrypoints["metadata"], ctx)
                patch = instances["metadata"].enrich(RomRef(**params["rom"]))
                # Same as `plan`: the host re-validates the whole patch and
                # re-checks the artwork URL against the allowlist.
                result = patch.model_dump()
            else:
                raise RuntimeError(f"unknown method {method!r}")

            write_message(stdout, {"kind": "result", "id": call_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - surfaced to the host verbatim
            write_message(
                stdout,
                {
                    "kind": "error",
                    "id": call_id,
                    "error": {
                        "message": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    },
                },
            )


def main() -> None:
    run_plugin(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
