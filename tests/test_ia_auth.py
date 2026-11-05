"""Offline proofs for IA login, secret isolation, renewal and request scope."""
import time

import httpx
import pytest

from rom_hub.ia_auth import ArchiveAuthError, configure, credentials, status
from rom_hub.ia_session import ArchiveOrgSession, ArchiveOrgTransport


@pytest.fixture
def root(tmp_path, monkeypatch):
    for key in ("EMAIL", "PASSWORD", "EMAIL_FILE", "PASSWORD_FILE"):
        for prefix in ("ROMARR_ARCHIVE_ORG_", "ROM_HUB_ARCHIVE_ORG_"):
            monkeypatch.delenv(prefix + key, raising=False)
    # Exercise ROM Hub's actual persistent encrypted file store in CI.
    monkeypatch.setenv("ROM_HUB_SECRET_STORE", "file")
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "test-key-stored-only-in-environment")
    return tmp_path


def _client(root, handler, remote):
    manager = ArchiveOrgSession(root, login_transport=httpx.MockTransport(handler))
    return manager, httpx.Client(transport=ArchiveOrgTransport(
        manager, transport=httpx.MockTransport(remote)))


def _login(request, token="session"):
    assert request.url == "https://archive.org/services/xauthn/?op=login"
    assert request.method == "POST"
    assert b"email=" in request.content and b"password=" in request.content
    return httpx.Response(200, json={"success": True, "values": {"cookies": {
        "logged-in-user": "account%40example.com; Max-Age=3600; domain=.archive.org",
        "logged-in-sig": token + "; Max-Age=3600; domain=.archive.org"}}})


def test_ui_uses_existing_secret_store_and_only_one_encrypted_entry(root):
    assert status(root)["configured"] is False
    assert configure(root, "account@example.com", "secret-password") == {"ok": True}
    assert credentials(root) == ("account@example.com", "secret-password")
    stored = root / "secrets.json"
    assert stored.is_file()
    content = stored.read_text()
    assert "secret-password" not in content and "account@example.com" not in content
    assert not (root / "state.json").exists()
    assert status(root) == {"configured": True, "external": False, "error": ""}
    assert configure(root, clear=True) == {"ok": True}
    assert credentials(root) is None


def test_external_files_override_ui_without_copying_to_store(root, monkeypatch):
    assert configure(root, "ui@example.com", "ui-secret") == {"ok": True}
    email_path, password_path = root / "email", root / "password"
    email_path.write_text("external@example.com\n")
    password_path.write_text("external-secret\n")
    monkeypatch.setenv("ROMARR_ARCHIVE_ORG_EMAIL_FILE", str(email_path))
    monkeypatch.setenv("ROMARR_ARCHIVE_ORG_PASSWORD_FILE", str(password_path))
    assert credentials(root) == ("external@example.com", "external-secret")
    assert configure(root, "second@example.com", "second-secret")["ok"] is False
    assert status(root)["external"] is True
    monkeypatch.delenv("ROMARR_ARCHIVE_ORG_EMAIL_FILE")
    monkeypatch.delenv("ROMARR_ARCHIVE_ORG_PASSWORD_FILE")
    assert credentials(root) == ("ui@example.com", "ui-secret")


def test_login_reuses_cookie_and_never_sends_it_to_unrelated_hosts(root):
    assert configure(root, "account@example.com", "secret-password")["ok"]
    logins, requests = [], []
    def login(req):
        logins.append(1)
        return _login(req)
    def remote(req):
        requests.append((str(req.url), req.headers.get("cookie")))
        return httpx.Response(200)
    manager, client = _client(root, login, remote)
    with client:
        for host in ("archive.org", "ia800408.us.archive.org", "ia600107.ca.archive.org"):
            assert client.get(f"https://{host}/view_archive.php").status_code == 200
        for host in ("www.archive.org", "archive.org.attacker.test", "example.com"):
            assert client.get(f"https://{host}/download").status_code == 200
        assert client.get("http://archive.org/test").status_code == 200
    assert len(logins) == 1
    expected = "logged-in-user=account%40example.com; logged-in-sig=session"
    assert [cookie for _, cookie in requests] == [expected] * 3 + [None] * 4


def test_cookie_expiry_renews_before_request(root):
    assert configure(root, "account@example.com", "secret-password")["ok"]
    logins = []
    def login(req):
        logins.append(1)
        return _login(req, "cookie" + str(len(logins)))
    manager, client = _client(root, login, lambda req: httpx.Response(200))
    with client:
        client.get("https://archive.org/metadata/test")
        manager._refresh_at = time.time() - 1  # simulate an expired renewal deadline
        client.get("https://archive.org/metadata/test")
    assert len(logins) == 2


@pytest.mark.parametrize("status_code", [401, 403])
def test_refusal_reauthenticates_only_once(root, status_code):
    assert configure(root, "account@example.com", "secret-password")["ok"]
    logins, outgoing = [], []
    def login(req):
        logins.append(1)
        return _login(req, "cookie" + str(len(logins)))
    def remote(req):
        outgoing.append(req.headers["cookie"])
        return httpx.Response(status_code)
    _, client = _client(root, login, remote)
    with client:
        response = client.get("https://archive.org/download/restricted.zip/member.chd")
    assert response.status_code == status_code
    assert len(logins) == 2
    assert len(outgoing) == 2 and outgoing[0] != outgoing[1]


def test_bad_login_message_cannot_expose_password(root):
    assert configure(root, "account@example.com", "secret-password")["ok"]
    _, client = _client(root, lambda req: httpx.Response(
        200, json={"success": False, "error": "secret-password"}), lambda req: httpx.Response(200))
    with client, pytest.raises(ArchiveAuthError, match="rejected"):
        client.get("https://archive.org/metadata/restricted")
    # The returned message contains no response body or submitted credentials.


@pytest.mark.parametrize("prefix", ["ROM_HUB_ARCHIVE_ORG_", "ROMARR_ARCHIVE_ORG_"])
def test_partial_external_pair_fails_closed(root, monkeypatch, prefix):
    configure(root, "saved@example.com", "saved-password")
    monkeypatch.setenv(prefix + "EMAIL", "external@example.com")
    with pytest.raises(ArchiveAuthError, match="both"):
        credentials(root)


def test_new_environment_family_does_not_mix_with_legacy(root, monkeypatch):
    monkeypatch.setenv("ROMARR_ARCHIVE_ORG_EMAIL", "old@example.com")
    monkeypatch.setenv("ROMARR_ARCHIVE_ORG_PASSWORD", "old-password")
    monkeypatch.setenv("ROM_HUB_ARCHIVE_ORG_EMAIL", "new@example.com")
    with pytest.raises(ArchiveAuthError, match="both"):
        credentials(root)
    monkeypatch.setenv("ROM_HUB_ARCHIVE_ORG_PASSWORD", "new-password")
    assert credentials(root) == ("new@example.com", "new-password")


def test_session_replaced_when_credentials_change(root, monkeypatch):
    from rom_hub.ia_session import get_session, archive_transport
    configure(root, "saved@example.com", "first-password")
    first = get_session(root)
    configure(root, "saved@example.com", "second-password")
    assert get_session(root) is not first
    assert archive_transport("another-plugin", root) is None
    configure(root, clear=True)
    assert archive_transport("archive-org", root) is None


def test_failed_login_preserves_saved_credentials(root, monkeypatch):
    from rom_hub import ia_auth
    configure(root, "saved@example.com", "saved-password")
    def refused(self):
        raise ArchiveAuthError("Archive.org rejected the configured login")
    monkeypatch.setattr(ArchiveOrgSession, "header", refused)
    assert not ia_auth.login(root, "new@example.com", "new-password")["ok"]
    assert credentials(root) == ("saved@example.com", "saved-password")


def test_concurrent_requests_share_one_login(root):
    from concurrent.futures import ThreadPoolExecutor
    configure(root, "account@example.com", "secret-password")
    calls = []
    def login(req):
        calls.append(1)
        return _login(req)
    manager = ArchiveOrgSession(root, login_transport=httpx.MockTransport(login))
    with ThreadPoolExecutor(max_workers=4) as workers:
        headers = list(workers.map(lambda _: manager.header(), range(8)))
    assert len(set(headers)) == 1
    assert len(calls) == 1


def test_redirect_never_carries_cookie_to_unapproved_host(root):
    configure(root, "account@example.com", "secret-password")
    received = []
    def remote(req):
        received.append(req.headers.get("cookie"))
        if req.url.host == "archive.org":
            return httpx.Response(302, headers={"location": "https://example.com/file",
                "set-cookie": "logged-in-sig=server-cookie; Domain=archive.org"})
        return httpx.Response(200)
    _, client = _client(root, _login, remote)
    with client:
        client.get("https://archive.org/file", follow_redirects=True)
    assert received[0] and received[1] is None


def test_login_size_limit(root):
    configure(root, "account@example.com", "secret-password")
    manager = ArchiveOrgSession(root, login_transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"x" * 131073)))
    with pytest.raises(ArchiveAuthError, match="too large"):
        manager.header()


def test_auth_cli_login_status_logout(root, monkeypatch, capsys):
    from rom_hub import cli, ia_auth
    monkeypatch.setenv("ROM_HUB_HOME", str(root))
    monkeypatch.setattr("builtins.input", lambda prompt: "account@example.com")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "secret-password")
    monkeypatch.setattr(ArchiveOrgSession, "header", lambda self: "verified-cookie")
    assert cli.main(["auth", "login", "archive-org"]) == 0
    assert credentials(root) == ("account@example.com", "secret-password")
    assert cli.main(["auth", "status", "archive-org"]) == 0
    output = capsys.readouterr().out
    assert str(root) in output and "FileStore" in output
    assert "secret-password" not in output and "verified-cookie" not in output
    assert cli.main(["auth", "logout", "archive-org"]) == 0
    assert credentials(root) is None


def test_auth_environment_is_not_given_to_plugin(root, monkeypatch):
    from rom_hub.broker.host import plugin_environment
    monkeypatch.setenv("ROM_HUB_ARCHIVE_ORG_EMAIL", "account@example.com")
    monkeypatch.setenv("ROM_HUB_ARCHIVE_ORG_PASSWORD", "secret-password")
    monkeypatch.setenv("ROMARR_ARCHIVE_ORG_PASSWORD", "legacy-password")
    child = plugin_environment()
    assert all("ARCHIVE_ORG" not in key for key in child)
    assert "ROM_HUB_SECRET_KEY" not in child


def test_existing_romarr_record_needs_no_migration(root):
    from rom_hub.secrets import open_store
    open_store(root).set("archive-org-host-auth", "credentials",
        '{"email": "old@example.com", "password": "old-password"}')
    assert credentials(root) == ("old@example.com", "old-password")


def test_external_file_change_replaces_session(root, monkeypatch):
    from rom_hub.ia_session import get_session
    email_file, password_file = root / "email", root / "password"
    email_file.write_text("account@example.com\n")
    password_file.write_text("first-password\n")
    monkeypatch.setenv("ROM_HUB_ARCHIVE_ORG_EMAIL_FILE", str(email_file))
    monkeypatch.setenv("ROM_HUB_ARCHIVE_ORG_PASSWORD_FILE", str(password_file))
    first = get_session(root)
    password_file.write_text("second-password\n")
    assert get_session(root) is not first
