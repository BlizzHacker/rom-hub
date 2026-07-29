import json
import subprocess
import sys
import textwrap

import pytest

from romm_hub.sandbox import probe


def _sandbox_state_in_subprocess() -> dict:
    """Run `runner._sandbox_state()` in a throwaway child process.

    On Linux, `_sandbox_state()` calls `sandbox.install()`, which loads a
    seccomp filter as a side effect. That filter is irreversible and
    process-wide (see `romm_hub.sandbox.install`'s docstring): loading it in
    the pytest process itself would block `execve` for the rest of the
    session, breaking every later test that spawns a subprocess. It must
    only ever be exercised in a dedicated child process, never in-process.
    """
    script = textwrap.dedent(
        """
        import json
        from romm_hub_sdk import runner
        print(json.dumps(runner._sandbox_state()))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_init_result_reports_sandbox_state():
    """The init reply must carry the keys the host's policy check reads."""
    from romm_hub_sdk import runner

    assert hasattr(runner, "_sandbox_state")
    state = _sandbox_state_in_subprocess()
    assert set(state) == {"sandboxed", "sandbox_reason"}
    assert isinstance(state["sandboxed"], bool)
    assert state["sandbox_reason"]


@pytest.mark.skipif(sys.platform != "linux", reason="seccomp is Linux-only")
def test_sandbox_state_is_true_on_linux():
    state = _sandbox_state_in_subprocess()
    assert state["sandboxed"] is True


@pytest.mark.skipif(sys.platform == "linux", reason="tests the non-Linux path")
def test_sandbox_state_is_false_off_linux_with_a_reason():
    # Safe to call directly here: off Linux, install() raises before it can
    # ever touch pyseccomp, so there is no filter-loading side effect to
    # isolate into a subprocess.
    from romm_hub_sdk import runner

    state = runner._sandbox_state()
    assert state["sandboxed"] is False
    assert "linux" in state["sandbox_reason"].lower()
    assert probe()[0] is False
