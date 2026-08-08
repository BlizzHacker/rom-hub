"""`rom-hub webhook` -- the operator's side of the receiver.

No socket is bound through `main()`: the server is covered in
tests/test_webhook_server.py and binding it again here would test argparse
twice. What is covered here is everything that decides *whether* it may
start, the URL it tells the operator to paste, the two commands that make
a recorded outcome actionable, and -- the part worth the setup -- the
wiring that hands `webhook.fulfil` the same plugin fan-out `rom-hub
search` uses.

**No test here reaches a library server.** `cli.open_backend` is replaced
by a fake with the capabilities an import needs, and `cli.run_import` by a
recorder. The plugin subprocess and the search fan-out are real.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from rom_hub.backends.base import IMPORT, SCAN
from rom_hub.cli import main, webhook_settings
from rom_hub.jobs import JobState
from rom_hub.webhook import (
    RequestEvent,
    RequestLog,
    RequestState,
    WebhookConfigError,
)

TOKEN = "a-token-long-enough-to-be-a-secret"

MANIFEST = """
[plugin]
slug = "demo"
name = "Demo"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "demo:Search"
importer = "demo:Importer"

[permissions]
network = ["demo.example"]
romm_api = []
"""

# Answers "Chrono Trigger" exactly for a matching query, and something
# adjacent otherwise -- so a test can drive both the match and the refusal
# through the same installed plugin.
PLUGIN = """
from rom_hub_sdk import (
    FetchFile,
    FetchPlan,
    ImportProvider,
    SearchProvider,
    SearchResult,
)


class Search(SearchProvider):
    def search(self, query, platform, limit):
        return [
            SearchResult(
                source_id="ct-1",
                title="Chrono Trigger",
                platform=platform or "snes",
            ),
            SearchResult(
                source_id="ct-2",
                title="Chrono Trigger 2: Not The Same Game",
                platform=platform or "snes",
            ),
        ]


class Importer(ImportProvider):
    def plan(self, result):
        return FetchPlan(
            files=[FetchFile(url="https://demo.example/g.zip", filename="g.zip")],
            platform="snes",
        )
"""


def _install_demo(tmp_path: Path) -> None:
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
    assert main(["plugin", "install", str(repo)]) == 0


class FakeBackend:
    """A `LibraryBackend` with only what `webhook serve` reaches: the
    capability set it refuses on, and a close it can call."""

    name = "fake"

    def __init__(self, capabilities=frozenset({IMPORT, SCAN})):
        self._capabilities = frozenset(capabilities)
        self.closed = False

    def capabilities(self):
        return self._capabilities

    def close(self):
        self.closed = True


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    monkeypatch.setenv("ROM_HUB_HOME", str(root))
    monkeypatch.delenv("ROM_HUB_WEBHOOK_TOKEN", raising=False)
    monkeypatch.delenv("ROM_HUB_WEBHOOK_HOST", raising=False)
    monkeypatch.delenv("ROM_HUB_WEBHOOK_PORT", raising=False)
    monkeypatch.delenv("ROM_HUB_WEBHOOK_PATH", raising=False)
    monkeypatch.delenv("ROM_HUB_WEBHOOK_TYPES", raising=False)
    return root


def log_for(root):
    from rom_hub.cli import requests_db_path

    return RequestLog(requests_db_path(root))


def event(request_id="req-1", **kwargs):
    base = {
        "request_id": request_id,
        "game_title": "Chrono Trigger",
        "igdb_id": "1234",
        "platforms": ["Super Nintendo"],
        "request_type": "game",
        "user_id": "12",
    }
    return RequestEvent(**{**base, **kwargs})


# --- settings -----------------------------------------------------------


def test_the_defaults_are_loopback_and_one_path(home, monkeypatch):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    settings = webhook_settings()
    assert settings.host == "127.0.0.1"
    assert settings.path == "/requests"
    assert settings.token == TOKEN
    assert settings.fulfil_types == ("game",)


def test_every_setting_can_be_overridden(home, monkeypatch):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_HOST", "0.0.0.0")
    monkeypatch.setenv("ROM_HUB_WEBHOOK_PORT", "9999")
    monkeypatch.setenv("ROM_HUB_WEBHOOK_PATH", "gg")
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TYPES", "game, update ,fix")
    settings = webhook_settings()
    assert (settings.host, settings.port) == ("0.0.0.0", 9999)
    # A path is normalised to one leading slash and no trailing one, so
    # `gg`, `/gg` and `/gg/` cannot be three different endpoints.
    assert settings.path == "/gg"
    assert settings.fulfil_types == ("game", "update", "fix")


def test_a_nonsense_port_is_refused_with_a_sentence(home, monkeypatch):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_PORT", "not-a-number")
    with pytest.raises(WebhookConfigError) as exc:
        webhook_settings()
    assert "not-a-number" in str(exc.value)


def test_a_port_outside_the_legal_range_is_refused(home, monkeypatch):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_PORT", "70000")
    with pytest.raises(WebhookConfigError):
        webhook_settings()


def test_an_unknown_request_type_is_refused(home, monkeypatch):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TYPES", "game,everything")
    with pytest.raises(WebhookConfigError) as exc:
        webhook_settings()
    assert "everything" in str(exc.value)


def test_a_misconfigured_receiver_reaches_the_operator_as_one_line(
    home, monkeypatch, capsys
):
    """Not a traceback: `main` has a catch list for exactly this shape of
    refusal, and a receiver misconfigured by one character is the likeliest
    way this feature goes wrong."""
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_PORT", "not-a-number")
    assert main(["webhook", "url"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "Traceback" not in err


# --- serve refusals -----------------------------------------------------


def test_serve_refuses_without_a_token(home, capsys):
    assert main(["webhook", "serve"]) == 1
    err = capsys.readouterr().err
    assert "ROM_HUB_WEBHOOK_TOKEN" in err


def test_serve_refuses_a_token_that_is_too_short(home, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", "short")
    assert main(["webhook", "serve"]) == 1
    err = capsys.readouterr().err
    assert "token" in err.lower()


def test_serve_refuses_before_binding_when_the_backend_is_unconfigured(
    home, monkeypatch, capsys
):
    """A receiver that accepts requests it can never fulfil is worse than
    one that refuses to start: GG Requestz would log five seconds of
    success and the library would stay empty."""
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_BACKEND", "romm")
    for name in ("ROMM_URL", "ROM_HUB_BACKEND_URL"):
        monkeypatch.delenv(name, raising=False)
    assert main(["webhook", "serve"]) == 1
    err = capsys.readouterr().err
    assert "backend" in err.lower() or "romm" in err.lower()


# --- url ----------------------------------------------------------------


def test_url_prints_the_endpoint_to_paste_into_gg_requestz(home, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_PORT", "8770")
    assert main(["webhook", "url"]) == 0
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:8770/requests/{TOKEN}" in out
    assert "REQUEST_WEBHOOK_URL" in out


def test_url_says_the_token_is_not_authentication(home, monkeypatch, capsys):
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    main(["webhook", "url"])
    out = capsys.readouterr().out
    assert "not authentication" in out.lower()


def test_url_refuses_without_a_token(home, capsys):
    assert main(["webhook", "url"]) == 1
    assert "ROM_HUB_WEBHOOK_TOKEN" in capsys.readouterr().err


# --- log and forget -----------------------------------------------------


def test_log_says_so_when_nothing_has_arrived(home, capsys):
    assert main(["webhook", "log"]) == 0
    assert "no requests" in capsys.readouterr().out.lower()


def test_log_lists_what_arrived(home, capsys):
    with log_for(home) as log:
        log.claim(event())
        log.finish("req-1", RequestState.FULFILLED, "imported as rom id 7", job_id=7)
        log.claim(event("req-2", game_title="Earthbound"))
        log.finish("req-2", RequestState.NO_MATCH, "no exact title match")

    assert main(["webhook", "log"]) == 0
    out = capsys.readouterr().out
    assert "Chrono Trigger" in out
    assert "FULFILLED" in out
    assert "Earthbound" in out
    assert "NO_MATCH" in out


def test_log_can_be_narrowed_to_one_state(home, capsys):
    with log_for(home) as log:
        log.claim(event())
        log.finish("req-1", RequestState.FULFILLED, "done")
        log.claim(event("req-2", game_title="Earthbound"))
        log.finish("req-2", RequestState.NO_MATCH, "nope")

    assert main(["webhook", "log", "--state", "NO_MATCH"]) == 0
    out = capsys.readouterr().out
    assert "Earthbound" in out
    assert "Chrono Trigger" not in out


def test_log_can_print_json_for_a_script(home, capsys):
    with log_for(home) as log:
        log.claim(event())
        log.finish("req-1", RequestState.NO_MATCH, "no exact title match")

    assert main(["webhook", "log", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["request_id"] == "req-1"
    assert rows[0]["state"] == "NO_MATCH"
    assert rows[0]["platforms"] == ["Super Nintendo"]


def test_log_refuses_a_state_that_does_not_exist(home, capsys):
    assert main(["webhook", "log", "--state", "SLEEPY"]) == 1
    assert "SLEEPY" in capsys.readouterr().err


def test_forget_removes_the_row_so_a_re_approval_is_acted_on(home, capsys):
    with log_for(home) as log:
        log.claim(event())
        log.finish("req-1", RequestState.NO_MATCH, "no exact title match")

    assert main(["webhook", "forget", "req-1"]) == 0
    assert "req-1" in capsys.readouterr().out
    with log_for(home) as log:
        assert log.get("req-1") is None


def test_forget_reports_an_unknown_request_rather_than_pretending(home, capsys):
    assert main(["webhook", "forget", "never-seen"]) == 1
    assert "never-seen" in capsys.readouterr().err


# --- serve, and the wiring behind it ------------------------------------


@pytest.fixture
def fake_backend(monkeypatch):
    """Replace the library backend everywhere `webhook` reaches for one.

    The live RomM on this network is production. Nothing in this file may
    open a connection to a library server, and the way to guarantee that is
    for `open_backend` never to build a real one.
    """
    from rom_hub import cli

    backends = []

    def opened(name=None):
        backend = FakeBackend()
        backends.append(backend)
        return backend

    monkeypatch.setattr(cli, "open_backend", opened)
    return backends


def test_serve_prints_the_endpoint_and_then_serves(
    home, monkeypatch, fake_backend, capsys
):
    from rom_hub.webhook_server import WebhookServer

    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    served = []
    monkeypatch.setattr(
        WebhookServer, "serve_forever", lambda self: served.append(self.url())
    )

    assert main(["webhook", "serve"]) == 0
    out = capsys.readouterr()
    assert f"/requests/{TOKEN}" in out.out
    assert "REQUEST_WEBHOOK_URL" in out.out
    assert served and TOKEN in served[0]
    # The pre-flight backend check closes what it opened rather than
    # leaving a connection open for the life of the process.
    assert fake_backend[0].closed is True


def test_serve_refuses_a_backend_that_cannot_import(home, monkeypatch, capsys):
    """A receiver that answers 202 and can never import is worse than one
    that will not start: GG Requestz would log success per request and the
    library would stay empty."""
    from rom_hub import cli
    from rom_hub.webhook_server import WebhookServer

    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(cli, "open_backend", lambda name=None: FakeBackend(frozenset()))
    monkeypatch.setattr(
        WebhookServer, "serve_forever", lambda self: pytest.fail("bound anyway")
    )

    assert main(["webhook", "serve"]) == 1
    assert "import" in capsys.readouterr().err.lower()


def test_serve_warns_when_no_plugin_can_search(
    home, monkeypatch, fake_backend, capsys
):
    from rom_hub.webhook_server import WebhookServer

    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(WebhookServer, "serve_forever", lambda self: None)
    assert main(["webhook", "serve"]) == 0
    assert "search" in capsys.readouterr().err


def test_serve_does_not_warn_once_a_search_plugin_is_installed(
    tmp_path, home, monkeypatch, fake_backend, capsys
):
    from rom_hub.webhook_server import WebhookServer

    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    _install_demo(tmp_path)
    capsys.readouterr()

    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(WebhookServer, "serve_forever", lambda self: None)
    assert main(["webhook", "serve"]) == 0
    assert "no enabled plugin" not in capsys.readouterr().err


def test_the_wiring_searches_the_installed_plugins_and_imports_the_match(
    tmp_path, home, monkeypatch, fake_backend
):
    """The one test that runs the real thing end to end: a real plugin
    subprocess answers a real fan-out, the real match runs, and only the
    library write is a fake."""
    from rom_hub import cli

    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    _install_demo(tmp_path)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)

    imported = []

    def fake_import(plugin, result, **kwargs):
        imported.append(result)
        return SimpleNamespace(
            job_id=7,
            state=JobState.DONE,
            message="imported as rom id 99",
            rom_id=99,
            warnings=(),
        )

    monkeypatch.setattr(cli, "run_import", fake_import)

    event = RequestEvent(
        request_id="req-live",
        game_title="Chrono Trigger",
        platforms=["Super Nintendo"],
        request_type="game",
    )
    with RequestLog(cli.requests_db_path(home)) as log:
        log.claim(event)
        done = cli._fulfil_request(event, log, webhook_settings())
        row = log.get("req-live")

    assert done.state is RequestState.FULFILLED
    assert done.job_id == 7
    # The near-miss title the same plugin also returned was not imported.
    assert [r.source_id for r in imported] == ["ct-1"]
    assert row.plugin == "demo"
    assert row.source_id == "ct-1"


def test_the_wiring_records_a_near_miss_without_importing_anything(
    tmp_path, home, monkeypatch, fake_backend
):
    from rom_hub import cli

    monkeypatch.setenv("ROM_HUB_ALLOW_UNSANDBOXED", "1")
    _install_demo(tmp_path)
    monkeypatch.setenv("ROM_HUB_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(
        cli, "run_import", lambda *a, **k: pytest.fail("imported a near miss")
    )

    event = RequestEvent(
        request_id="req-nope",
        game_title="A Game Nobody Indexed",
        platforms=["Super Nintendo"],
        request_type="game",
    )
    with RequestLog(cli.requests_db_path(home)) as log:
        log.claim(event)
        done = cli._fulfil_request(event, log, webhook_settings())

    assert done.state is RequestState.NO_MATCH
    assert "A Game Nobody Indexed" in done.detail
