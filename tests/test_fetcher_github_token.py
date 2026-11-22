"""An operator's GitHub token goes to api.github.com on GET, and nowhere else."""
import httpx

from rom_hub.broker.fetcher import HttpxFetcher


def _fetcher(seen):
    def handler(request):
        seen.append((request.method, request.url.host, request.headers.get("authorization")))
        return httpx.Response(200, text="{}")
    return HttpxFetcher(transport=httpx.MockTransport(handler))


def test_token_only_on_get_to_the_github_api(monkeypatch):
    monkeypatch.setenv("ROM_HUB_GITHUB_TOKEN", "ghp_test")
    seen = []
    f = _fetcher(seen)
    f.get("https://api.github.com/repos/a/b/releases/latest")
    f.get("https://github.com/a/b")
    f.get("https://raw.githubusercontent.com/a/b/main/x")
    f.post("https://api.github.com/repos/a/b/issues", json={})
    assert seen == [("GET", "api.github.com", "Bearer ghp_test"),
                    ("GET", "github.com", None),
                    ("GET", "raw.githubusercontent.com", None),
                    ("POST", "api.github.com", None)]


def test_no_token_means_anonymous(monkeypatch):
    monkeypatch.delenv("ROM_HUB_GITHUB_TOKEN", raising=False)
    seen = []
    _fetcher(seen).get("https://api.github.com/repos/a/b")
    assert seen == [("GET", "api.github.com", None)]
