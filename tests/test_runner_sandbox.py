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
