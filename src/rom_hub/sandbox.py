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
import functools
import subprocess
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
    """Report whether a filter can be installed, and why not if it cannot.

    Importable is not the same as loadable. A kernel built without
    CONFIG_SECCOMP_FILTER (Synology DSM's 4.4 kernel is one) imports pyseccomp
    fine and then fails every `seccomp_load` with ECANCELED. Reporting that as
    "available" makes hosts trust a sandbox that cannot exist, and every plugin
    is then refused at run time with no fallback offered. So the answer comes
    from actually loading the filter -- in a throwaway child, because loading
    is irreversible and would confine the caller.
    """
    return _probe_cached()


@functools.lru_cache(maxsize=1)
def _probe_cached() -> tuple[bool, str]:
    if sys.platform != "linux":
        return False, f"seccomp requires linux; this is {sys.platform}"
    try:
        import pyseccomp  # noqa: F401
    except ImportError as exc:
        return False, f"pyseccomp is not installed: {exc}"
    try:
        child = subprocess.run(
            [sys.executable, "-c",
             "from rom_hub.sandbox import _load_filter; _load_filter()"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not test-load the seccomp filter: {exc}"
    if child.returncode != 0:
        lines = (child.stderr or "").strip().splitlines()
        detail = lines[-1] if lines else f"exit status {child.returncode}"
        return False, f"seccomp filter cannot be loaded here: {detail}"
    return True, "seccomp filter available"


def install() -> None:
    """Irreversibly confine this process. Call before importing plugin code.

    Loading the filter is irreversible and process-wide: once installed,
    there is no way to remove it or scope it to part of the process. Tests
    must never call this (directly or via `rom_hub_sdk.runner._sandbox_state`)
    in the pytest process itself -- do it in a dedicated `subprocess.run(...)`
    child instead, or every later test in the same session that spawns a
    subprocess will fail with `PermissionError` once `execve` is blocked.
    """
    if sys.platform != "linux":
        raise SandboxUnavailable(f"seccomp requires linux; this is {sys.platform}")
    _load_filter()


def _load_filter() -> None:
    """Set no_new_privs and load the deny filter on this process.

    Kept separate from `install` so `probe`'s throwaway child can run it
    without probing again (which would recurse, one child per level).
    """
    try:
        import pyseccomp
    except ImportError as exc:
        raise SandboxUnavailable(f"pyseccomp is not installed: {exc}") from exc

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
