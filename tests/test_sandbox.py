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
