"""A known secret must not appear in what any command prints.

Every test in the "not in the output" section runs a real command through
`main()` with a known value stored, captures stdout **and** stderr, and
asserts the value is not in either. The list of commands is deliberately
the list from the requirement -- `plugin list`, `plugin browse`, `plugin
config`, `plugin secret list`, `backend info`, `jobs`, `search`, `--help`
-- rather than the ones that happened to be easy.

The plugin here declares `secret` and hands the value straight back as a
search result title, so the tests can also prove the opposite direction:
it really does reach the plugin, and the plugin really can use it.
"""

import subprocess
from pathlib import Path

import pytest

from rom_hub.cli import main

SECRET = "RA-live-0123456789abcdefghijklmnop"

MANIFEST = """
[plugin]
slug = "demo"
name = "Demo"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "demo:Search"

[permissions]
network = ["demo.example"]
romm_api = []

[config]
api_key = { type = "secret" }
depth = { type = "int", default = 3 }
"""

PLUGIN = """
from rom_hub_sdk import SearchProvider, SearchResult


class Search(SearchProvider):
    def search(self, query, platform, limit):
        # Reports the key's *length*, not the key. A plugin that printed
        # its own credential into a result would be disclosing it itself --
        # something the host cannot prevent and does not claim to, since a
        # plugin holding a value can do anything with it. What the tests
        # need is proof the value crossed the boundary, and a length is
        # that proof without becoming the leak it is testing for.
        key = self.ctx.config.get("api_key", "")
        return [SearchResult(source_id="1", title="key-length=%d" % len(key))]
"""


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-plugin"
    repo.mkdir()
    (repo / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (repo / "demo.py").write_text(PLUGIN, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(root))
    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    # Pin the store: `auto` would pick up a real keyring on a developer's
    # workstation and quietly write a test value into it.
    monkeypatch.setenv("ROM_HUB_SECRET_STORE", "file")
    monkeypatch.delenv("ROM_HUB_SECRET_KEY", raising=False)
    monkeypatch.delenv("ROMM_HUB_SECRET_KEY", raising=False)
    return root


@pytest.fixture
def installed(home, source_repo, capsys):
    assert main(["plugin", "install", str(source_repo)]) == 0
    capsys.readouterr()
    return home


def _store_secret(capsys, value=SECRET):
    assert main(["plugin", "secret", "set", "demo", "api_key", "--value", value]) == 0
    return capsys.readouterr()


# -- setting one ---------------------------------------------------------


def test_a_stored_secret_is_not_echoed_back(installed, capsys):
    captured = _store_secret(capsys)
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert "stored api_key for demo" in captured.out


def test_passing_a_value_as_an_argument_warns_about_shell_history(
    installed, capsys
):
    captured = _store_secret(capsys)
    assert "shell history" in captured.err
    assert "rotate" in captured.err


def test_a_value_from_an_environment_variable_needs_no_argument(
    installed, capsys, monkeypatch
):
    monkeypatch.setenv("MY_KEY", SECRET)
    assert (
        main(["plugin", "secret", "set", "demo", "api_key", "--env", "MY_KEY"]) == 0
    )
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert "shell history" not in captured.err, "no warning: it was not an argument"


def test_a_value_from_stdin_needs_no_argument(installed, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(SECRET + "\n"))
    assert main(["plugin", "secret", "set", "demo", "api_key", "--stdin"]) == 0
    assert SECRET not in "".join(capsys.readouterr())
    from rom_hub.cli import secret_store

    assert secret_store().get("demo", "api_key") == SECRET


def test_an_unset_environment_variable_refuses_rather_than_storing_nothing(
    installed, capsys, monkeypatch
):
    monkeypatch.delenv("NOPE", raising=False)
    assert main(["plugin", "secret", "set", "demo", "api_key", "--env", "NOPE"]) == 1
    assert "unset or empty" in capsys.readouterr().err


def test_a_field_the_plugin_never_declared_is_refused(installed, capsys):
    assert (
        main(["plugin", "secret", "set", "demo", "nope", "--value", SECRET]) == 1
    )
    assert "declares no secret named" in capsys.readouterr().err


def test_a_prompt_is_used_on_a_terminal(installed, capsys, monkeypatch):
    """No flag, a TTY: `getpass`, twice, echoing nothing."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return SECRET

    monkeypatch.setattr("rom_hub.cli.getpass.getpass", fake_getpass)
    assert main(["plugin", "secret", "set", "demo", "api_key"]) == 0
    assert len(prompts) == 2, "it asks twice so a typo cannot be stored silently"
    assert SECRET not in "".join(capsys.readouterr())


def test_a_mistyped_confirmation_stores_nothing(installed, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    values = iter([SECRET, SECRET + "typo"])
    monkeypatch.setattr("rom_hub.cli.getpass.getpass", lambda p: next(values))
    assert main(["plugin", "secret", "set", "demo", "api_key"]) == 1
    assert "did not match" in capsys.readouterr().err
    from rom_hub.cli import secret_store

    assert secret_store().get("demo", "api_key") is None


# -- it is not in the plain config ---------------------------------------


def test_the_value_is_not_in_state_json(installed, capsys):
    _store_secret(capsys)
    state = (installed / "state.json").read_text(encoding="utf-8")
    assert SECRET not in state
    assert "api_key" not in state


def test_the_value_is_not_readable_anywhere_under_the_hub_root(installed, capsys):
    """A grep of the whole directory, which is what an operator would do."""
    _store_secret(capsys)
    hits = [
        path
        for path in installed.rglob("*")
        if path.is_file() and SECRET in _safe_read(path)
    ]
    assert hits == [], f"the secret is readable in {hits}"


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# -- it is not in any command's output -----------------------------------


COMMANDS = [
    ["plugin", "list"],
    ["plugin", "browse"],
    ["plugin", "config", "demo"],
    ["plugin", "secret", "list"],
    ["plugin", "secret", "list", "demo"],
    ["plugin", "assets", "demo"],
    ["backend", "info", "--backend", "romm"],
    ["jobs"],
    ["search", "anything"],
]


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a))
def test_no_command_prints_the_secret(installed, capsys, argv):
    _store_secret(capsys)
    main(argv)
    captured = capsys.readouterr()
    assert SECRET not in captured.out, f"leaked on stdout: {' '.join(argv)}"
    assert SECRET not in captured.err, f"leaked on stderr: {' '.join(argv)}"


HELP_ARGV = [
    ["--help"],
    ["plugin", "--help"],
    ["plugin", "secret", "--help"],
    ["plugin", "secret", "set", "--help"],
    ["plugin", "config", "--help"],
]


@pytest.mark.parametrize("argv", HELP_ARGV, ids=lambda a: " ".join(a))
def test_no_help_output_prints_the_secret(installed, capsys, argv):
    _store_secret(capsys)
    with pytest.raises(SystemExit):
        main(argv)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_plugin_config_redacts_the_secret_but_shows_the_rest(installed, capsys):
    _store_secret(capsys)
    assert main(["plugin", "config", "demo"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "***" in out
    assert "api_key" in out, "the field is named, only the value is hidden"
    assert "depth" in out


def test_plugin_config_says_not_set_when_nothing_is_stored(installed, capsys):
    assert main(["plugin", "config", "demo"]) == 0
    out = capsys.readouterr().out
    assert "(not set)" in out
    assert "***" not in out, "'***' for an unset secret would say the opposite"


# -- --set writes the non-secret fields, and refuses the secret ones ------


def test_config_set_writes_a_declared_field(installed, capsys):
    assert main(["plugin", "config", "demo", "--set", "depth=9"]) == 0
    capsys.readouterr()
    assert main(["plugin", "config", "demo"]) == 0
    assert "9" in capsys.readouterr().out


def test_config_set_coerces_to_the_declared_type(installed, capsys):
    assert main(["plugin", "config", "demo", "--set", "depth=9"]) == 0
    capsys.readouterr()
    from rom_hub.registry import Registry

    stored = Registry(installed).get("demo").config["depth"]
    assert stored == 9 and isinstance(stored, int), "an int field must not hold a str"


def test_config_set_refuses_a_value_the_declared_type_cannot_hold(installed, capsys):
    assert main(["plugin", "config", "demo", "--set", "depth=seven"]) != 0
    assert "not one" in capsys.readouterr().err


def test_config_set_refuses_an_undeclared_field(installed, capsys):
    assert main(["plugin", "config", "demo", "--set", "nope=1"]) != 0
    err = capsys.readouterr().err
    assert "nope" in err and "declares" in err


def test_config_set_refuses_a_secret_and_names_the_command_that_takes_it(
    installed, capsys
):
    assert main(["plugin", "config", "demo", "--set", f"api_key={SECRET}"]) != 0
    err = capsys.readouterr().err
    assert "plugin secret set" in err
    assert SECRET not in err, "the refusal must not echo the value back"


def test_config_set_does_not_write_the_secret_anywhere(installed, capsys):
    main(["plugin", "config", "demo", "--set", f"api_key={SECRET}"])
    capsys.readouterr()
    state = (installed / "state.json").read_text(encoding="utf-8")
    assert SECRET not in state


# -- the store describes itself honestly ---------------------------------


def test_secret_list_admits_the_default_store_is_obfuscation(installed, capsys):
    _store_secret(capsys)
    assert main(["plugin", "secret", "list"]) == 0
    out = capsys.readouterr().out
    assert "obfuscation" in out.lower()
    assert "set (" in out, "it says the field is set"
    assert SECRET not in out


def test_secret_list_reports_a_supplied_key_without_the_disclaimer(
    installed, capsys, monkeypatch
):
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "from-a-docker-secret")
    assert main(["plugin", "secret", "list"]) == 0
    out = capsys.readouterr().out
    assert "obfuscation" not in out.lower()
    assert "ROM_HUB_SECRET_KEY" in out


# -- it still reaches the plugin -----------------------------------------


def test_the_plugin_actually_receives_it(installed, capsys):
    """The other half of the contract, through the real CLI path.

    The plugin reports `len(api_key)`, so a passing assertion here means
    the value reached plugin code -- while the same command's output is
    still free of the value itself.
    """
    _store_secret(capsys)
    assert main(["search", "x"]) == 0
    out = capsys.readouterr().out
    assert f"key-length={len(SECRET)}" in out
    assert SECRET not in out


def test_a_plugin_with_no_secret_set_sees_an_empty_string_not_a_keyerror(
    installed, capsys
):
    """An unset secret must not crash plugin code that indexes its config."""
    assert main(["search", "x"]) == 0
    assert "key-length=0" in capsys.readouterr().out


def test_clearing_removes_it(installed, capsys):
    _store_secret(capsys)
    assert main(["plugin", "secret", "clear", "demo", "api_key"]) == 0
    assert "cleared api_key" in capsys.readouterr().out
    from rom_hub.cli import secret_store

    assert secret_store().get("demo", "api_key") is None


def test_clearing_something_that_was_never_set_says_so(installed, capsys):
    assert main(["plugin", "secret", "clear", "demo", "api_key"]) == 0
    assert "was not set" in capsys.readouterr().out


# -- migration off plaintext ---------------------------------------------


def test_a_pre_secret_plaintext_key_is_migrated_on_next_use(installed, capsys):
    """Nobody breaks.

    Simulates the operator who set `api_key` while it was still a plain
    `str`: the value is sitting in `state.json`. The next command that
    starts the plugin moves it into the store, tells them once, and the
    plugin keeps working.
    """
    import json

    from rom_hub.registry import Registry

    Registry(installed).set_config("demo", {"api_key": SECRET, "depth": 3})
    assert SECRET in (installed / "state.json").read_text(encoding="utf-8")

    assert main(["search", "x"]) == 0
    captured = capsys.readouterr()

    state = json.loads((installed / "state.json").read_text(encoding="utf-8"))
    assert SECRET not in json.dumps(state), "it left the plain config"
    assert state["demo"]["config"] == {"depth": 3}, "other settings untouched"

    from rom_hub.cli import secret_store

    assert secret_store().get("demo", "api_key") == SECRET, "and still works"
    assert "moved api_key" in captured.err
    assert "rotate" in captured.err
    assert SECRET not in captured.err, "the notice never carries the value"


def test_un_migrated_plaintext_is_still_redacted_in_the_meantime(
    installed, capsys
):
    """The window before migration runs is the riskiest moment, not a gap."""
    from rom_hub.registry import Registry

    Registry(installed).set_config("demo", {"api_key": SECRET})
    assert main(["plugin", "config", "demo"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "***" in out


def test_secret_list_flags_un_migrated_plaintext(installed, capsys):
    from rom_hub.registry import Registry

    Registry(installed).set_config("demo", {"api_key": SECRET})
    assert main(["plugin", "secret", "list", "demo"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "STILL IN PLAIN CONFIG" in out


# -- every capability command, not just the ones that existed -------------


def test_every_plugin_subprocess_is_started_with_its_secrets():
    """The other half of the scrubber guard, and it caught a real gap.

    A capability added on another branch (`firmware`) built its own
    `PluginProcess(...)` without `secrets=`, so a firmware plugin needing
    an API key would have been handed an empty one and refused for no
    visible reason -- the failure mode is silent, which is why this is
    checked structurally rather than left to whoever adds capability
    number seven to remember.
    """
    import re
    from pathlib import Path

    from rom_hub import cli, dispatcher

    missing = []
    for module in (cli, dispatcher):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for match in re.finditer(r"PluginProcess\(.*?\n\s*\)", source, re.S):
            if "secrets=" not in match.group(0):
                line = source[: match.start()].count("\n") + 1
                missing.append(f"{Path(module.__file__).name}:{line}")
    assert not missing, (
        f"these PluginProcess call sites start a plugin without its secrets, "
        f"so a `secret`-typed config field silently arrives empty there: "
        f"{missing}. Pass `secrets=prepare_secrets(plugin)` (or thread a "
        f"`secrets_for` callable through, as `search_all` does)."
    )


# -- install-time notice -------------------------------------------------


def test_install_says_the_plugin_needs_a_secret_and_how_to_set_it(
    home, source_repo, capsys
):
    assert main(["plugin", "install", str(source_repo)]) == 0
    out = capsys.readouterr().out
    assert "needs 1 secret(s): api_key" in out
    assert "rom-hub plugin secret set demo api_key" in out
