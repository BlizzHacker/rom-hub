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
