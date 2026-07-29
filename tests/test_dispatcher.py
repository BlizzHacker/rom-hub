from romm_hub.dispatcher import search_all
from romm_hub.types import SearchResult


class FakePlugin:
    def __init__(self, slug, enabled=True):
        self.slug = slug
        self.enabled = enabled
        self.manifest = type("M", (), {"slug": slug, "capabilities": {"search": "x:Y"}})()
        self.path = "/nowhere"
        self.config = {}


class FakeProcess:
    def __init__(self, slug, results=None, error=None):
        self.slug = slug
        self._results = results or []
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def search(self, query, platform, limit):
        if self._error:
            raise RuntimeError(self._error)
        return self._results


def make_factory(behaviour):
    def factory(plugin, fetcher, timeout):
        return behaviour[plugin.slug]

    return factory


def test_merges_results_from_all_plugins():
    plugins = [FakePlugin("a"), FakePlugin("b")]
    factory = make_factory(
        {
            "a": FakeProcess("a", [SearchResult(source_id="1", title="A", plugin="a")]),
            "b": FakeProcess("b", [SearchResult(source_id="2", title="B", plugin="b")]),
        }
    )
    outcome = search_all(plugins, fetcher=None, query="q", process_factory=factory)
    assert sorted(r.title for r in outcome.results) == ["A", "B"]
    assert outcome.responded == 2
    assert outcome.total == 2


def test_one_failing_plugin_does_not_lose_the_others():
    plugins = [FakePlugin("good"), FakePlugin("bad")]
    factory = make_factory(
        {
            "good": FakeProcess(
                "good", [SearchResult(source_id="1", title="OK", plugin="good")]
            ),
            "bad": FakeProcess("bad", error="kaboom"),
        }
    )
    outcome = search_all(plugins, fetcher=None, query="q", process_factory=factory)
    assert [r.title for r in outcome.results] == ["OK"]
    assert outcome.responded == 1
    assert outcome.total == 2
    bad = next(s for s in outcome.statuses if s.slug == "bad")
    assert bad.ok is False
    assert "kaboom" in bad.error


def test_disabled_plugins_are_skipped_entirely():
    plugins = [FakePlugin("on"), FakePlugin("off", enabled=False)]
    factory = make_factory(
        {"on": FakeProcess("on", [SearchResult(source_id="1", title="On", plugin="on")])}
    )
    outcome = search_all(plugins, fetcher=None, query="q", process_factory=factory)
    assert outcome.total == 1
    assert [s.slug for s in outcome.statuses] == ["on"]


def test_plugins_without_search_capability_are_skipped():
    plugin = FakePlugin("nosearch")
    plugin.manifest.capabilities = {"metadata": "x:Y"}
    outcome = search_all([plugin], fetcher=None, query="q", process_factory=lambda *a: None)
    assert outcome.total == 0
    assert outcome.results == []
