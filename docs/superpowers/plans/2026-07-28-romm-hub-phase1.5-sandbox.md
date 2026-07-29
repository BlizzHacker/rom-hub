# RomM Hub Phase 1.5 — Plugin Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the manifest's `network` allowlist a genuine containment boundary by having each plugin subprocess install a restrictive seccomp filter on itself before any plugin code is imported — so the claim retracted in finding C1 can be truthfully re-asserted for network egress.

**Architecture:** `pyseccomp` filter installed by the runner after `PR_SET_NO_NEW_PRIVS` and after the SDK imports, but *before* `importlib.import_module` loads the plugin. The child reports its sandbox state back in the `init` result; the host refuses to proceed unsandboxed unless explicitly opted out.

**Tech Stack:** Python 3.12, `pyseccomp` (Linux only), pytest.

## Verified facts this plan rests on

Measured on an LXC container during design, not assumed:

- Inside **default Docker** (no `--security-opt`, no added caps): `prctl(PR_SET_NO_NEW_PRIVS)` succeeds, a `pyseccomp` filter loads, and `socket.socket()` then raises `PermissionError`. Output: `NNP_OK → FILTER_LOADED → BLOCKED: PermissionError`.
- Namespace-based sandboxing (bubblewrap) is **not** viable in the planned deployment: `docker run debian unshare --user --net` → `unshare failed: Operation not permitted`. This is why the design is seccomp, not bwrap.
- seccomp **cannot** filter by path (it cannot dereference pointer arguments), so arbitrary file read is explicitly out of scope and must stay documented as an exposure.

## Global Constraints

- **Do not block `clone`/`fork`.** CPython uses `clone` for threads; blocking it risks breaking the interpreter and anything that threads. Blocking `execve`/`execveat` is sufficient to stop useful process spawn, and seccomp filters are **inherited across fork**, so a forked child is equally confined.
- **Do not block `openat`/`read`/`write`.** The plugin module import and the stdio channel need them. File-read confinement is out of scope for Phase 1.5 — say so, do not imply otherwise.
- **The filter must be installed before the plugin module is imported**, never after.
- **Fail closed.** If the sandbox cannot be installed, the host refuses to run the plugin unless `ROMM_HUB_ALLOW_UNSANDBOXED=1` is set.
- `pyseccomp` is a **Linux-only** dependency: declare it as `pyseccomp; sys_platform == "linux"`.
- Tests that require a real filter must be gated with `@pytest.mark.skipif(sys.platform != "linux", ...)` so the Windows dev suite stays green.
- Python 3.12+, `src/` layout, `python -m pytest` from repo root.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/romm_hub/sandbox.py` | Capability probe + filter installation (new) |
| `src/romm_hub_sdk/runner.py` | Install filter during `init`, report state (modify) |
| `src/romm_hub/broker/host.py` | Enforce fail-closed policy on the `init` reply (modify) |
| `src/romm_hub/cli.py` | Surface sandbox state to the operator (modify) |
| `docs/DESIGN.md`, `README.md` | Re-assert the network claim, now true (modify) |

---

### Task 1: The sandbox module

**Files:**
- Create: `src/romm_hub/sandbox.py`
- Modify: `pyproject.toml` (add the Linux-only dep)
- Test: `tests/test_sandbox.py`

**Interfaces:**
- Produces: `DENIED_SYSCALLS: tuple[str, ...]`; `SandboxUnavailable(Exception)`; `probe() -> tuple[bool, str]` returning `(available, reason)`; `install() -> None` raising `SandboxUnavailable`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sandbox.py`:

```python
import socket
import subprocess
import sys
import textwrap

import pytest

from romm_hub.sandbox import DENIED_SYSCALLS, SandboxUnavailable, install, probe

linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="seccomp is Linux-only"
)


def test_denylist_covers_network_and_exec():
    assert "socket" in DENIED_SYSCALLS
    assert "connect" in DENIED_SYSCALLS
    assert "execve" in DENIED_SYSCALLS
    assert "execveat" in DENIED_SYSCALLS


def test_denylist_does_not_break_threads_or_file_io():
    # Blocking these would break CPython itself; see Global Constraints.
    for never in ("clone", "fork", "openat", "read", "write", "futex"):
        assert never not in DENIED_SYSCALLS


def test_probe_returns_reason_when_unavailable():
    available, reason = probe()
    assert isinstance(available, bool)
    assert reason  # always explains itself, available or not


@pytest.mark.skipif(sys.platform == "linux", reason="tests the non-Linux path")
def test_install_raises_off_linux():
    with pytest.raises(SandboxUnavailable, match="linux"):
        install()


@linux_only
def test_probe_reports_available_on_linux():
    available, reason = probe()
    assert available, f"expected a working sandbox on Linux, got: {reason}"


@linux_only
def test_filter_blocks_socket_in_a_child_process():
    # Must run in a child: the filter is irreversible for the process.
    script = textwrap.dedent(
        """
        import socket, sys
        from romm_hub.sandbox import install
        install()
        try:
            socket.socket()
            print("ESCAPED")
        except PermissionError:
            print("BLOCKED")
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert out.stdout.strip() == "BLOCKED", out.stderr


@linux_only
def test_filter_allows_file_reads_so_imports_still_work():
    script = textwrap.dedent(
        """
        from romm_hub.sandbox import install
        install()
        import json, base64          # imports after the filter must still work
        print("IMPORTS_OK", json.dumps({"a": 1}))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert "IMPORTS_OK" in out.stdout, out.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.sandbox'`

- [ ] **Step 3: Write the implementation**

Add to `pyproject.toml` dependencies:

```toml
    "pyseccomp>=0.1.2; sys_platform == 'linux'",
```

Create `src/romm_hub/sandbox.py`:

```python
"""Self-imposed seccomp confinement for plugin subprocesses.

A process may install a *restrictive* seccomp filter on itself without any
privilege, provided it first sets PR_SET_NO_NEW_PRIVS. This works inside an
unmodified Docker container, which is why it is used here instead of a
namespace sandbox: `unshare --user --net` is refused by Docker's default
seccomp profile, so bubblewrap is not an option in this deployment.

Scope, stated plainly:
  * Network egress is closed. This is what makes a manifest's `network`
    allowlist a containment boundary rather than a declaration of intent.
  * Useful process spawn is closed (execve/execveat). fork/clone are NOT
    blocked -- CPython uses clone for threads -- but a forked child inherits
    this filter, so it is equally confined.
  * Arbitrary file read is NOT closed. seccomp cannot filter on a path: it
    cannot dereference pointer arguments. Confining reads needs a mount
    namespace, which Docker denies us. Do not imply otherwise.
"""

import ctypes
import sys

PR_SET_NO_NEW_PRIVS = 38

DENIED_SYSCALLS: tuple[str, ...] = (
    # Network egress.
    "socket",
    "socketcall",   # 32-bit multiplexer
    "connect",
    "sendto",
    "sendmsg",
    # Useful process spawn.
    "execve",
    "execveat",
    # Peeking at other processes.
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
)


class SandboxUnavailable(Exception):
    """The seccomp filter could not be installed on this platform."""


def probe() -> tuple[bool, str]:
    """Report whether a filter can be installed, and why not if it cannot."""
    if sys.platform != "linux":
        return False, f"seccomp requires linux; this is {sys.platform}"
    try:
        import pyseccomp  # noqa: F401
    except ImportError as exc:
        return False, f"pyseccomp is not installed: {exc}"
    return True, "seccomp filter available"


def install() -> None:
    """Irreversibly confine this process. Call before importing plugin code."""
    available, reason = probe()
    if not available:
        raise SandboxUnavailable(reason)

    import pyseccomp

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxUnavailable(
            f"PR_SET_NO_NEW_PRIVS failed: errno {ctypes.get_errno()}"
        )

    flt = pyseccomp.SyscallFilter(defaction=pyseccomp.ALLOW)
    for name in DENIED_SYSCALLS:
        try:
            flt.add_rule(pyseccomp.ERRNO(1), name)  # EPERM
        except (ValueError, RuntimeError):
            # Syscall absent on this architecture (e.g. socketcall on x86_64).
            continue
    try:
        flt.load()
    except Exception as exc:  # noqa: BLE001
        raise SandboxUnavailable(f"seccomp filter load failed: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox.py -v`
Expected on Windows: PASS — 4 passed, 3 skipped (the `linux_only` ones).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/romm_hub/sandbox.py tests/test_sandbox.py
git commit -m "feat(sandbox): self-imposed seccomp filter for plugin subprocesses"
```

---

### Task 2: Runner installs the filter, reports its state

**Files:**
- Modify: `src/romm_hub_sdk/runner.py`
- Test: `tests/test_runner_sandbox.py`

**Interfaces:**
- Consumes: `romm_hub.sandbox.install`, `probe`, `SandboxUnavailable`.
- Produces: the `init` result becomes `{"ok": True, "sandboxed": bool, "sandbox_reason": str}` instead of `{"ok": True}`. Task 3 depends on these exact key names.

The filter goes in during `init` — after the SDK is imported, before `_load()` ever imports plugin code.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runner_sandbox.py`:

```python
import sys

import pytest

from romm_hub.sandbox import probe


def test_init_result_reports_sandbox_state():
    """The init reply must carry the keys the host's policy check reads."""
    from romm_hub_sdk import runner

    assert hasattr(runner, "_sandbox_state")
    state = runner._sandbox_state()
    assert set(state) == {"sandboxed", "sandbox_reason"}
    assert isinstance(state["sandboxed"], bool)
    assert state["sandbox_reason"]


@pytest.mark.skipif(sys.platform != "linux", reason="seccomp is Linux-only")
def test_sandbox_state_is_true_on_linux():
    from romm_hub_sdk import runner

    assert runner._sandbox_state()["sandboxed"] is True


@pytest.mark.skipif(sys.platform == "linux", reason="tests the non-Linux path")
def test_sandbox_state_is_false_off_linux_with_a_reason():
    from romm_hub_sdk import runner

    state = runner._sandbox_state()
    assert state["sandboxed"] is False
    assert "linux" in state["sandbox_reason"].lower()
    assert probe()[0] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runner_sandbox.py -v`
Expected: FAIL — `AssertionError: hasattr(runner, '_sandbox_state')`

- [ ] **Step 3: Modify the runner**

In `src/romm_hub_sdk/runner.py`, add the import near the top:

```python
from romm_hub.sandbox import SandboxUnavailable, install as install_sandbox
```

Add this helper above `run_plugin`:

```python
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
```

In `run_plugin`, replace the `init` branch's result line. The branch currently ends with `result: Any = {"ok": True}`. It must become:

```python
            if method == "init":
                sys.path.insert(0, params["plugin_dir"])
                entrypoints = params["entrypoints"]
                ctx = PluginContext(
                    config=params.get("config") or {}, http=HttpClient(channel)
                )
                # Confine BEFORE any plugin code can be imported by _load().
                result: Any = {"ok": True, **_sandbox_state()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runner_sandbox.py -v`
Expected on Windows: PASS — 2 passed, 1 skipped.

Run: `python -m pytest -q`
Expected: PASS — the existing suite still green.

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub_sdk/runner.py tests/test_runner_sandbox.py
git commit -m "feat(sandbox): confine the plugin subprocess before loading plugin code"
```

---

### Task 3: Host enforces fail-closed policy

**Files:**
- Modify: `src/romm_hub/broker/host.py`
- Test: `tests/test_broker_sandbox_policy.py`

**Interfaces:**
- Consumes: the `init` reply keys from Task 2.
- Produces: `PluginProcess.__init__` gains `allow_unsandboxed: bool = False`; attributes `.sandboxed: bool` and `.sandbox_reason: str` set after `start()`; `SandboxRefused(PluginCallError)` raised by `start()` when unsandboxed and not permitted.

- [ ] **Step 1: Write the failing test**

Create `tests/test_broker_sandbox_policy.py`:

```python
import textwrap
from pathlib import Path

import pytest

from romm_hub.broker.host import PluginProcess, SandboxRefused
from romm_hub.manifest import parse_manifest

MANIFEST = """
[plugin]
slug = "sbx"
name = "Sbx"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "sbx_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN = textwrap.dedent(
    """
    from romm_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            return [SearchResult(source_id="1", title="ok")]
    """
)


class NullFetcher:
    def get(self, url, params):
        return 200, ""


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "sbx_plugin.py").write_text(PLUGIN, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, allow_unsandboxed):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config={},
        fetcher=NullFetcher(),
        timeout=30.0,
        allow_unsandboxed=allow_unsandboxed,
    )


def test_opt_out_allows_an_unsandboxed_plugin_to_run(plugin_dir):
    with _proc(plugin_dir, allow_unsandboxed=True) as proc:
        assert proc.search("q", None, 5)[0].title == "ok"
        assert isinstance(proc.sandboxed, bool)
        assert proc.sandbox_reason


def test_default_is_fail_closed(plugin_dir):
    """Without the opt-out, an unsandboxable platform must refuse to run."""
    proc = _proc(plugin_dir, allow_unsandboxed=False)
    from romm_hub.sandbox import probe

    if probe()[0]:
        # Sandbox works here: start() must succeed and report sandboxed.
        with proc:
            assert proc.sandboxed is True
    else:
        with pytest.raises(SandboxRefused, match="unsandboxed"):
            proc.start()
        proc.close()


def test_refusal_message_names_the_opt_out(plugin_dir):
    from romm_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    proc = _proc(plugin_dir, allow_unsandboxed=False)
    with pytest.raises(SandboxRefused, match="ROMM_HUB_ALLOW_UNSANDBOXED"):
        proc.start()
    proc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_broker_sandbox_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'SandboxRefused'`

- [ ] **Step 3: Modify the host**

In `src/romm_hub/broker/host.py`, add after the `PluginCallError` class:

```python
class SandboxRefused(PluginCallError):
    """The plugin could not be confined and the policy does not permit that."""
```

Add `allow_unsandboxed: bool = False` as the final parameter of `PluginProcess.__init__`, and in the body:

```python
        self.allow_unsandboxed = allow_unsandboxed
        self.sandboxed = False
        self.sandbox_reason = "not started"
```

In `start()`, replace the bare `self._call("init", {...})` with a call that keeps its reply and enforces the policy:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_broker_sandbox_policy.py -v`
Expected: PASS — 3 passed (or 2 passed 1 skipped on Linux).

Run: `python -m pytest -q`
Expected: existing broker tests now fail — they construct `PluginProcess` without the opt-out on Windows. Fix `tests/test_broker_host.py`'s `_proc()` helper and `tests/test_dispatcher.py` as needed by passing `allow_unsandboxed=True`, since those tests exercise broker mechanics rather than sandbox policy. Re-run until green.

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/broker/host.py tests/
git commit -m "feat(sandbox): fail closed unless ROMM_HUB_ALLOW_UNSANDBOXED is set"
```

---

### Task 4: Wire the policy through dispatcher and CLI, and correct the docs

**Files:**
- Modify: `src/romm_hub/dispatcher.py`
- Modify: `src/romm_hub/cli.py`
- Modify: `docs/DESIGN.md`, `README.md`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `PluginProcess(allow_unsandboxed=...)`, `SandboxRefused`.
- Produces: `search_all(..., allow_unsandboxed: bool = False)`; `romm_hub.cli.allow_unsandboxed() -> bool` reading `ROMM_HUB_ALLOW_UNSANDBOXED`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_allow_unsandboxed_reads_the_environment(monkeypatch):
    from romm_hub.cli import allow_unsandboxed

    monkeypatch.delenv("ROMM_HUB_ALLOW_UNSANDBOXED", raising=False)
    assert allow_unsandboxed() is False
    monkeypatch.setenv("ROMM_HUB_ALLOW_UNSANDBOXED", "1")
    assert allow_unsandboxed() is True


def test_search_reports_sandbox_refusal_clearly(
    tmp_path, source_repo, monkeypatch, capsys
):
    """A refusal must explain itself, not surface as a bare traceback."""
    from romm_hub.sandbox import probe

    if probe()[0]:
        pytest.skip("sandbox available; refusal path not reachable")
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROMM_HUB_ALLOW_UNSANDBOXED", raising=False)
    main(["plugin", "install", str(source_repo)])
    main(["search", "anything"])
    combined = capsys.readouterr()
    assert "ROMM_HUB_ALLOW_UNSANDBOXED" in (combined.out + combined.err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v -k sandbox or unsandboxed`
Expected: FAIL — `ImportError: cannot import name 'allow_unsandboxed'`

- [ ] **Step 3: Wire it through**

In `src/romm_hub/dispatcher.py`, add `allow_unsandboxed: bool = False` to `search_all`'s signature, thread it into `_default_factory`:

```python
def _default_factory(plugin, fetcher, timeout, allow_unsandboxed=False) -> PluginProcess:
    return PluginProcess(
        plugin_dir=plugin.path,
        manifest=plugin.manifest,
        config=plugin.config,
        fetcher=fetcher,
        timeout=timeout,
        allow_unsandboxed=allow_unsandboxed,
    )
```

and in `run(plugin)` call `factory(plugin, fetcher, timeout)` as before for injected test factories, but pass `allow_unsandboxed` to the default one. Keep the injected-factory signature `(plugin, fetcher, timeout)` unchanged so existing dispatcher tests keep working — bind the flag with a closure or `functools.partial` when using the default.

In `src/romm_hub/cli.py`, add:

```python
def allow_unsandboxed() -> bool:
    return os.environ.get("ROMM_HUB_ALLOW_UNSANDBOXED", "") == "1"
```

Pass `allow_unsandboxed=allow_unsandboxed()` into `search_all` in `_cmd_search`. The per-plugin failure lines already print via `outcome.statuses`, so a `SandboxRefused` surfaces there — verify the message reaches the user and is not swallowed.

Update the install-time note added by the C1 fix: it currently says Phase 1 does not sandbox. That is no longer true for network egress. It must now say that network egress and process spawn are confined by seccomp on Linux, that file reads are not confined, and — when `probe()` reports unavailable — that this host cannot confine plugins at all.

In `docs/DESIGN.md`, update the "What is enforced today, and what is not" section:
- Network egress moves from **not enforced** to **enforced**, via self-imposed seccomp, with the measured evidence (`NNP_OK → FILTER_LOADED → BLOCKED: PermissionError` inside default Docker).
- Record why bubblewrap was rejected: `docker run debian unshare --user --net` → `Operation not permitted`.
- Arbitrary file read stays listed as **not enforced**, with the reason (seccomp cannot filter by path).
- Keep the "only install plugins you trust" guidance, but scope it to what remains: a plugin can still read files the Hub can read.

Mirror the same correction in `README.md`'s security section.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — everything green.

Run: `python -m pytest -m live -q`
Expected: PASS — 1 passed. On Windows this needs `ROMM_HUB_ALLOW_UNSANDBOXED=1`; confirm that requirement is documented in the README rather than silently assumed.

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/dispatcher.py src/romm_hub/cli.py docs/DESIGN.md README.md tests/test_cli.py
git commit -m "feat(sandbox): enforce policy end to end and re-assert the network claim"
```

---

## Phase 1.5 Done Criteria

- [ ] A plugin process on Linux cannot open a socket, proven by a test that would have caught finding C1.
- [ ] The filter is installed before any plugin module is imported.
- [ ] Without `ROMM_HUB_ALLOW_UNSANDBOXED=1`, an unsandboxable host refuses to run plugins and says why.
- [ ] `python -m pytest` is green on Windows (Linux-only tests skipped, not failed).
- [ ] `docs/DESIGN.md` and `README.md` state exactly what is and is not confined — network and exec yes, file read no.
- [ ] Deployment verification on an LXC container in default Docker (no `--security-opt`), recorded with real output.

## Explicitly Not in Phase 1.5

Filesystem confinement (needs a mount namespace Docker denies), memory caps (I1/I2), and the other open Important findings I3, I4, I6, I7. Phase 2 remains blocked until the Done Criteria above pass on the deployment target.
