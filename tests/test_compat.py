"""The rename's compatibility surface: old module names, old env names.

`romm-hub` became `rom-hub`, and two groups of people are already
depending on the old spelling: plugin authors who wrote
`from romm_hub_sdk import ...`, and the operator whose shell profile on
the deployment target exports `ROMM_HUB_HOME`. Neither is ours to break
silently, so both keep working and both say so.

The identity assertions below are the ones that matter. An alias that
returned a *copy* of the SDK would look fine in a smoke test and then
fail every `isinstance` check the host makes against its own
`FetchPlan`, which is a far worse failure than an ImportError.
"""

import importlib
import warnings

import pytest

import rom_hub.types
import rom_hub_sdk
from rom_hub import env

# -- module aliases -------------------------------------------------------


def test_the_old_sdk_name_still_imports():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        old = importlib.import_module("romm_hub_sdk")
    assert old.FetchPlan is rom_hub_sdk.FetchPlan


def test_the_old_sdk_name_warns_that_it_is_deprecated():
    # Re-imported deliberately: the warning fires on the shim's execution,
    # so a module already in sys.modules would report nothing.
    import sys

    sys.modules.pop("romm_hub_sdk", None)
    with pytest.warns(DeprecationWarning, match="rom_hub_sdk"):
        importlib.import_module("romm_hub_sdk")


def test_the_alias_yields_the_identical_class_not_a_copy():
    """The whole point. A second FetchPlan class would fail isinstance."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        old = importlib.import_module("romm_hub_sdk")
    plan = old.FetchPlan(
        files=[old.FetchFile(url="https://x.example/a.zip", filename="a.zip")],
        platform="dos",
    )
    assert isinstance(plan, rom_hub_sdk.FetchPlan)


def test_an_old_host_submodule_resolves_to_the_same_module():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        old = importlib.import_module("romm_hub.types")
    assert old is rom_hub.types


def test_a_nested_old_submodule_resolves():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        old = importlib.import_module("romm_hub.broker.host")
    import rom_hub.broker.host

    assert old is rom_hub.broker.host


def test_a_module_that_never_existed_still_fails():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with pytest.raises(ImportError):
            importlib.import_module("romm_hub.no_such_module")


# -- environment aliases ---------------------------------------------------


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    env.reset_announcements()
    for name in (
        "ROM_HUB_HOME",
        "ROMM_HUB_HOME",
        "ROM_HUB_CORES_DIR",
        "ROMM_HUB_CORES_DIR",
        "ROM_HUB_ALLOW_UNSANDBOXED",
        "ROMM_HUB_ALLOW_UNSANDBOXED",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    env.reset_announcements()


def test_the_new_name_is_read(monkeypatch):
    monkeypatch.setenv("ROM_HUB_HOME", "/new")
    assert env.get("ROM_HUB_HOME") == "/new"


def test_the_old_name_still_works(monkeypatch):
    monkeypatch.setenv("ROMM_HUB_HOME", "/old")
    with pytest.warns(DeprecationWarning):
        assert env.get("ROM_HUB_HOME") == "/old"


def test_the_new_name_wins_when_both_are_set(monkeypatch):
    """A host part-way through migrating must never be ambiguous."""
    monkeypatch.setenv("ROM_HUB_HOME", "/new")
    monkeypatch.setenv("ROMM_HUB_HOME", "/old")
    assert env.get("ROM_HUB_HOME") == "/new"


def test_an_empty_new_name_falls_through_to_the_old_one(monkeypatch):
    monkeypatch.setenv("ROM_HUB_HOME", "")
    monkeypatch.setenv("ROMM_HUB_HOME", "/old")
    with pytest.warns(DeprecationWarning):
        assert env.get("ROM_HUB_HOME") == "/old"


def test_the_deprecation_notice_reaches_stderr_once(monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", "/old")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        env.get("ROM_HUB_HOME")
        env.get("ROM_HUB_HOME")
    err = capsys.readouterr().err
    assert err.count("ROMM_HUB_HOME") == 1
    assert "ROM_HUB_HOME" in err


def test_nothing_is_printed_when_only_the_new_name_is_used(monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_HOME", "/new")
    env.get("ROM_HUB_HOME")
    assert capsys.readouterr().err == ""


def test_a_variable_with_no_old_spelling_is_left_alone(monkeypatch):
    monkeypatch.setenv("ROMM_URL", "http://romm.example")
    # RomM's own connection settings are not the Hub's name and were not
    # renamed; env.get must not invent a ROM_HUB_ prefix for them.
    assert env.deprecated_name("ROMM_URL") is None


# -- the settings that read through it -------------------------------------


def test_default_root_honours_the_old_home_variable(monkeypatch, tmp_path):
    from rom_hub.cli import default_root

    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "legacy"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert default_root() == tmp_path / "legacy"


def test_default_root_prefers_an_existing_pre_rename_home(monkeypatch, tmp_path):
    """An install whose plugins are in ~/.romm-hub keeps finding them."""
    from rom_hub import cli

    (tmp_path / ".romm-hub").mkdir()
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.default_root() == tmp_path / ".romm-hub"


def test_default_root_uses_the_new_home_when_it_exists(monkeypatch, tmp_path):
    from rom_hub import cli

    (tmp_path / ".romm-hub").mkdir()
    (tmp_path / ".rom-hub").mkdir()
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.default_root() == tmp_path / ".rom-hub"


def test_allow_unsandboxed_honours_the_old_variable(monkeypatch):
    from rom_hub.cli import allow_unsandboxed

    monkeypatch.setenv("ROMM_HUB_ALLOW_UNSANDBOXED", "1")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert allow_unsandboxed() is True


def test_cores_dir_honours_the_old_variable(monkeypatch, tmp_path):
    from rom_hub.cli import cores_dir

    monkeypatch.setenv("ROMM_HUB_CORES_DIR", str(tmp_path / "cores"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert cores_dir() == tmp_path / "cores"
