from rom_hub_sdk.context import HttpClient, PluginContext


class FakeChannel:
    """Stands in for the host: answers one http.get with a canned response."""

    def __init__(self, response: dict):
        self.response = response
        self.sent: list[dict] = []

    def send(self, msg: dict) -> None:
        self.sent.append(msg)

    def await_result(self, call_id: str) -> dict:
        return self.response


def test_http_get_sends_a_call_and_returns_response():
    chan = FakeChannel({"status_code": 200, "text": '{"ok": true}'})
    client = HttpClient(chan)
    resp = client.get("https://archive.org/x", params={"a": "b"})

    assert chan.sent[0]["kind"] == "call"
    assert chan.sent[0]["method"] == "http.get"
    assert chan.sent[0]["params"]["url"] == "https://archive.org/x"
    assert chan.sent[0]["params"]["params"] == {"a": "b"}
    assert chan.sent[0]["id"].startswith("p")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_call_ids_increment():
    chan = FakeChannel({"status_code": 200, "text": "{}"})
    client = HttpClient(chan)
    client.get("https://archive.org/1")
    client.get("https://archive.org/2")
    assert chan.sent[0]["id"] != chan.sent[1]["id"]


def test_context_exposes_config():
    ctx = PluginContext(config={"collections": ["softwarelibrary"]}, http=None)
    assert ctx.config["collections"] == ["softwarelibrary"]
