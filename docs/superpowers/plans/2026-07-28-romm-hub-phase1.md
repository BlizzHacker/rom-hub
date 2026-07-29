# RomM Hub Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI that searches Archive.org through an installed plugin, over the brokered `ctx.http` path — proving the RPP contract and the broker security model before any UI or import work is built on top of them. Phase 1 does **not** sandbox the plugin subprocess; see Global Constraints.

**Architecture:** A host process spawns each plugin as a subprocess and speaks newline-delimited JSON-RPC over stdin/stdout. Plugins are handed no RomM token and no library mount, and the plugin API offers no socket — they call `ctx.http`, which is an RPC back to the host that enforces the plugin's declared allowlist before performing the request. (The subprocess itself is unsandboxed in Phase 1, so that is the supported path, not the only one.) Search fans out across plugins in parallel and returns partial results with per-plugin status.

**Tech Stack:** Python 3.12, pydantic v2 (validating untrusted plugin output), httpx (host-side fetching only), pytest, argparse, stdlib `tomllib`.

## Global Constraints

- **Python 3.12+.** `tomllib` is used from the stdlib; do not add a TOML dependency.
- **RomM core is never modified.** Phase 1 does not talk to RomM at all.
- **`rpp_version` must be exactly `"1"`.** Reject any other value.
- **Plugins are never *handed*:** a RomM token, a filesystem mount, or network access. The plugin API offers none of the three, and the `ctx.http` broker enforces the declared allowlist on every request it serves — `check_url` is unavoidable en route to the only socket in the process. **But Phase 1 does not sandbox the plugin subprocess**, so this is a constraint on the API surface, not a containment boundary: a hostile plugin can `import socket` and bypass the broker entirely. Only install plugins you trust. Real isolation (bubblewrap/nsjail `--unshare-net --ro-bind`, or the container boundary) is a blocking prerequisite for Phase 2, which is where a RomM admin token first exists to steal.
- **Plugin HTTP is https-only** in Phase 1. Reject any other scheme.
- **Heavy runtime data stays off `C:`** — `plugins/`, `var/` are gitignored. Phase 1 writes only small state.
- **`secret` config type is reserved but NOT implemented in Phase 1.** It is specified in RPP v1 for sub-project C. A manifest declaring it must be rejected with a clear "not implemented in Phase 1" message rather than silently accepted.
- **Capability names `peer` and `netplay` are reserved.** Reject manifests declaring them.
- Source layout is `src/`-based. Run tests with `python -m pytest` from the repo root.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest config |
| `src/romm_hub/types.py` | RPP v1 wire types (`SearchResult`) |
| `src/romm_hub/manifest.py` | `manifest.toml` parsing + validation |
| `src/romm_hub/netpolicy.py` | URL allowlist matching — the security core |
| `src/romm_hub/protocol.py` | Newline-delimited JSON framing |
| `src/romm_hub/broker/fetcher.py` | Host-side HTTP (the only thing that opens sockets) |
| `src/romm_hub/broker/host.py` | Subprocess spawn + duplex RPC loop + policy enforcement |
| `src/romm_hub/registry.py` | Plugin install (git), discovery, enable/disable, config |
| `src/romm_hub/dispatcher.py` | Parallel search fan-out, partial results |
| `src/romm_hub/cli.py` | `romm-hub` entrypoint |
| `src/romm_hub_sdk/` | What a plugin imports: capability ABCs, `ctx.http`, runner loop |
| `plugins-dev/archive-org/` | The Archive.org plugin (its own git repo) |

**Id spaces:** host-initiated calls use ids `h1, h2, …`; plugin-initiated calls use `p1, p2, …`. This is why the duplex loop never needs collision handling.

---

### Task 1: Scaffold and RPP types

**Files:**
- Create: `pyproject.toml`
- Create: `src/romm_hub/__init__.py`
- Create: `src/romm_hub/types.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SearchResult` pydantic model with fields `source_id: str`, `title: str`, `platform: str | None`, `size_bytes: int | None`, `url: str | None`, `extra: dict[str, str]`, `plugin: str`. Validation rejects empty `source_id` or `title`. Every later task imports this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_types.py`:

```python
import pytest
from pydantic import ValidationError

from romm_hub.types import SearchResult


def test_minimal_result_gets_defaults():
    r = SearchResult(source_id="msdos_Oregon_Trail_The_1990", title="The Oregon Trail")
    assert r.source_id == "msdos_Oregon_Trail_The_1990"
    assert r.title == "The Oregon Trail"
    assert r.platform is None
    assert r.size_bytes is None
    assert r.url is None
    assert r.extra == {}
    assert r.plugin == ""


def test_empty_source_id_rejected():
    with pytest.raises(ValidationError):
        SearchResult(source_id="", title="The Oregon Trail")


def test_empty_title_rejected():
    with pytest.raises(ValidationError):
        SearchResult(source_id="abc", title="")


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        SearchResult(source_id="abc", title="x", size_bytes=-1)


def test_extra_survives_roundtrip():
    r = SearchResult(source_id="abc", title="x", extra={"stream_only": "true"})
    assert SearchResult(**r.model_dump()).extra["stream_only"] == "true"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub'`

- [ ] **Step 3: Write the scaffold and implementation**

Create `pyproject.toml`:

```toml
[project]
name = "romm-hub"
version = "0.1.0"
description = "Plugin host for RomM (RPP v1)"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
romm-hub = "romm_hub.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: hits the real network; deselected by default"]
addopts = "-m 'not live'"
```

Create `src/romm_hub/__init__.py` (empty file).

Create `src/romm_hub/types.py`:

```python
"""RPP v1 wire types.

These validate data coming back from untrusted plugin subprocesses, so
constraints here are load-bearing rather than cosmetic.
"""

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    platform: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    url: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)
    # Set by the host after the plugin returns; plugins cannot forge it.
    plugin: str = ""
```

- [ ] **Step 4: Install and run tests**

Run:

```bash
python -m pip install -e ".[dev]"
```

Run: `python -m pytest tests/test_types.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/romm_hub tests/test_types.py
git commit -m "feat: project scaffold and RPP v1 SearchResult type"
```

---

### Task 2: Manifest parsing and validation

**Files:**
- Create: `src/romm_hub/manifest.py`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Manifest` dataclass with `slug, name, version, rpp_version, license, capabilities: dict[str,str], network: list[str], romm_api: list[str], config_schema: dict`; `ManifestError(Exception)`; `parse_manifest(text: str) -> Manifest`; `load_manifest(path: Path) -> Manifest`. `KNOWN_CAPABILITIES` and `RESERVED_CAPABILITIES` are module constants.

- [ ] **Step 1: Write the failing test**

Create `tests/test_manifest.py`:

```python
import pytest

from romm_hub.manifest import ManifestError, parse_manifest

GOOD = """
[plugin]
slug = "archive-org"
name = "Archive.org"
version = "1.0.0"
rpp_version = "1"
license = "MIT"

[capabilities]
search = "archive_org.search:Search"

[permissions]
network = ["archive.org", "*.archive.org"]
romm_api = []

[config]
collections = { type = "list[str]", default = ["softwarelibrary"] }
"""


def test_parses_good_manifest():
    m = parse_manifest(GOOD)
    assert m.slug == "archive-org"
    assert m.rpp_version == "1"
    assert m.capabilities == {"search": "archive_org.search:Search"}
    assert m.network == ["archive.org", "*.archive.org"]
    assert m.config_schema["collections"]["default"] == ["softwarelibrary"]


def test_wrong_rpp_version_rejected():
    with pytest.raises(ManifestError, match="rpp_version"):
        parse_manifest(GOOD.replace('rpp_version = "1"', 'rpp_version = "2"'))


def test_unknown_capability_rejected():
    bad = GOOD.replace("search =", "teleport =")
    with pytest.raises(ManifestError, match="teleport"):
        parse_manifest(bad)


def test_reserved_capability_rejected():
    bad = GOOD.replace("search =", "peer =")
    with pytest.raises(ManifestError, match="reserved"):
        parse_manifest(bad)


def test_secret_config_rejected_in_phase1():
    bad = GOOD + '\napi_key = { type = "secret" }\n'
    with pytest.raises(ManifestError, match="not implemented"):
        parse_manifest(bad)


def test_bad_entrypoint_rejected():
    bad = GOOD.replace("archive_org.search:Search", "archive_org.search")
    with pytest.raises(ManifestError, match="module:Class"):
        parse_manifest(bad)


def test_bad_slug_rejected():
    with pytest.raises(ManifestError, match="slug"):
        parse_manifest(GOOD.replace('slug = "archive-org"', 'slug = "Archive Org!"'))


def test_missing_capabilities_rejected():
    bad = GOOD.replace('search = "archive_org.search:Search"', "")
    with pytest.raises(ManifestError, match="at least one capability"):
        parse_manifest(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.manifest'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/manifest.py`:

```python
"""Parsing and validation of a plugin's manifest.toml.

A manifest is the plugin's declaration of what it needs. Because the broker
enforces those declarations, a permissive parser here would quietly weaken
the whole security model — so everything unknown is rejected.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KNOWN_CAPABILITIES = frozenset({"search", "importer", "metadata", "stream", "cores"})
RESERVED_CAPABILITIES = frozenset({"peer", "netplay"})
SUPPORTED_CONFIG_TYPES = frozenset({"str", "int", "bool", "list[str]"})
RESERVED_CONFIG_TYPES = frozenset({"secret"})

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class ManifestError(Exception):
    """Raised when a manifest is malformed, unsupported, or unsafe."""


@dataclass(frozen=True)
class Manifest:
    slug: str
    name: str
    version: str
    rpp_version: str
    license: str | None
    capabilities: dict[str, str]
    network: list[str]
    romm_api: list[str]
    config_schema: dict = field(default_factory=dict)


def parse_manifest(text: str) -> Manifest:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"manifest is not valid TOML: {exc}") from exc

    plugin = raw.get("plugin")
    if not isinstance(plugin, dict):
        raise ManifestError("manifest is missing a [plugin] table")

    slug = plugin.get("slug", "")
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ManifestError(
            f"invalid slug {slug!r}: must be lowercase alphanumeric with hyphens"
        )

    rpp_version = str(plugin.get("rpp_version", ""))
    if rpp_version != "1":
        raise ManifestError(
            f"unsupported rpp_version {rpp_version!r}: this host implements RPP v1"
        )

    for required in ("name", "version"):
        if not plugin.get(required):
            raise ManifestError(f"[plugin] is missing {required}")

    capabilities = raw.get("capabilities") or {}
    if not isinstance(capabilities, dict) or not capabilities:
        raise ManifestError("manifest must declare at least one capability")

    for cap, entrypoint in capabilities.items():
        if cap in RESERVED_CAPABILITIES:
            raise ManifestError(
                f"capability {cap!r} is reserved for a future RPP version"
            )
        if cap not in KNOWN_CAPABILITIES:
            raise ManifestError(f"unknown capability {cap!r}")
        if not isinstance(entrypoint, str) or not _ENTRYPOINT_RE.match(entrypoint):
            raise ManifestError(
                f"capability {cap!r} entrypoint {entrypoint!r} must be 'module:Class'"
            )

    permissions = raw.get("permissions") or {}
    network = permissions.get("network") or []
    romm_api = permissions.get("romm_api") or []
    if not isinstance(network, list) or not all(isinstance(h, str) for h in network):
        raise ManifestError("permissions.network must be a list of host patterns")
    if not isinstance(romm_api, list) or not all(isinstance(s, str) for s in romm_api):
        raise ManifestError("permissions.romm_api must be a list of scope strings")

    config_schema = raw.get("config") or {}
    if not isinstance(config_schema, dict):
        raise ManifestError("[config] must be a table")
    for key, spec in config_schema.items():
        if not isinstance(spec, dict) or "type" not in spec:
            raise ManifestError(f"config field {key!r} must declare a type")
        declared = spec["type"]
        if declared in RESERVED_CONFIG_TYPES:
            raise ManifestError(
                f"config field {key!r} uses type {declared!r}, which is reserved "
                "in RPP v1 but not implemented in Phase 1"
            )
        if declared not in SUPPORTED_CONFIG_TYPES:
            raise ManifestError(f"config field {key!r} has unknown type {declared!r}")

    return Manifest(
        slug=slug,
        name=plugin["name"],
        version=str(plugin["version"]),
        rpp_version=rpp_version,
        license=plugin.get("license"),
        capabilities=dict(capabilities),
        network=list(network),
        romm_api=list(romm_api),
        config_schema=config_schema,
    )


def load_manifest(path: Path) -> Manifest:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest at {path}: {exc}") from exc
    return parse_manifest(text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_manifest.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/manifest.py tests/test_manifest.py
git commit -m "feat: manifest parsing with reserved-name and secret-type rejection"
```

---

### Task 3: Network policy — the security core

**Files:**
- Create: `src/romm_hub/netpolicy.py`
- Test: `tests/test_netpolicy.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PolicyViolation(Exception)`; `host_matches(host: str, pattern: str) -> bool`; `url_allowed(url: str, patterns: list[str]) -> bool`; `check_url(url: str, patterns: list[str]) -> None` which raises `PolicyViolation`. Task 6 calls `check_url` before every fetch.

This task is deliberately isolated because it is the single point where the "declared permissions are real" claim either holds or fails. The tests below are adversarial on purpose.

- [ ] **Step 1: Write the failing test**

Create `tests/test_netpolicy.py`:

```python
import pytest

from romm_hub.netpolicy import PolicyViolation, check_url, host_matches, url_allowed

PATTERNS = ["archive.org", "*.archive.org"]


def test_exact_host_allowed():
    assert url_allowed("https://archive.org/advancedsearch.php", PATTERNS)


def test_subdomain_allowed_by_wildcard():
    assert url_allowed("https://ia801504.us.archive.org/file.zip", PATTERNS)


def test_unrelated_host_denied():
    assert not url_allowed("https://evil.com/steal", PATTERNS)


def test_suffix_confusion_denied():
    # The classic bug: naive endswith() lets this through.
    assert not url_allowed("https://archive.org.evil.com/", PATTERNS)


def test_userinfo_confusion_denied():
    # Real host here is evil.com, not archive.org.
    assert not url_allowed("https://archive.org@evil.com/", PATTERNS)


def test_query_string_confusion_denied():
    assert not url_allowed("https://evil.com/?x=archive.org", PATTERNS)


def test_host_is_case_insensitive():
    assert url_allowed("https://ARCHIVE.ORG/x", PATTERNS)


def test_http_scheme_denied():
    assert not url_allowed("http://archive.org/x", PATTERNS)


def test_file_scheme_denied():
    assert not url_allowed("file:///etc/passwd", PATTERNS)


def test_empty_patterns_deny_everything():
    assert not url_allowed("https://archive.org/x", [])


def test_wildcard_does_not_match_bare_domain():
    assert not host_matches("archive.org", "*.archive.org")


def test_wildcard_does_not_span_dots():
    assert not host_matches("a.b.archive.org.evil.com", "*.archive.org")


def test_check_url_raises_with_useful_message():
    with pytest.raises(PolicyViolation, match="evil.com"):
        check_url("https://evil.com/x", PATTERNS)


def test_malformed_url_denied():
    assert not url_allowed("not a url", PATTERNS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_netpolicy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.netpolicy'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/netpolicy.py`:

```python
"""Allowlist enforcement for plugin-initiated HTTP.

Every plugin request passes through check_url() before a socket is opened.
If this module is wrong, the manifest's `network` declaration is decoration.
"""

from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"https"})


class PolicyViolation(Exception):
    """A plugin asked for a URL its manifest does not permit."""


def host_matches(host: str, pattern: str) -> bool:
    host = host.lower().strip(".")
    pattern = pattern.lower().strip(".")
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[2:]
        if not suffix:
            return False
        # A wildcard covers exactly one or more leading labels, never the
        # bare domain, and never a domain that merely contains the suffix.
        return host.endswith("." + suffix)
    return host == pattern


def url_allowed(url: str, patterns: list[str]) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False
    # .hostname strips userinfo and port, which is what defeats
    # https://archive.org@evil.com/
    host = parts.hostname
    if not host:
        return False
    return any(host_matches(host, p) for p in patterns)


def check_url(url: str, patterns: list[str]) -> None:
    if not url_allowed(url, patterns):
        raise PolicyViolation(
            f"blocked request to {url!r}: not permitted by manifest "
            f"network allowlist {patterns!r}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_netpolicy.py -v`
Expected: PASS — 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/netpolicy.py tests/test_netpolicy.py
git commit -m "feat: network allowlist with suffix/userinfo confusion defences"
```

---

### Task 4: Wire protocol framing

**Files:**
- Create: `src/romm_hub/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ProtocolError(Exception)`; `MAX_MESSAGE_BYTES = 8 * 1024 * 1024`; `write_message(stream, msg: dict) -> None`; `read_message(stream) -> dict | None` (returns `None` at clean EOF). Messages are dicts with `kind` in `{"call", "result", "error"}` and an `id`. Tasks 5 and 6 both use these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_protocol.py`:

```python
import io

import pytest

from romm_hub.protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    read_message,
    write_message,
)


def test_roundtrip():
    buf = io.StringIO()
    write_message(buf, {"kind": "call", "id": "h1", "method": "ping", "params": {}})
    buf.seek(0)
    msg = read_message(buf)
    assert msg == {"kind": "call", "id": "h1", "method": "ping", "params": {}}


def test_eof_returns_none():
    assert read_message(io.StringIO("")) is None


def test_blank_lines_skipped():
    buf = io.StringIO('\n\n{"kind": "result", "id": "p1", "result": 3}\n')
    assert read_message(buf)["result"] == 3


def test_invalid_json_raises():
    with pytest.raises(ProtocolError, match="invalid JSON"):
        read_message(io.StringIO("{not json}\n"))


def test_non_object_raises():
    with pytest.raises(ProtocolError, match="object"):
        read_message(io.StringIO("[1, 2, 3]\n"))


def test_missing_kind_raises():
    with pytest.raises(ProtocolError, match="kind"):
        read_message(io.StringIO('{"id": "h1"}\n'))


def test_oversize_line_raises():
    huge = '{"kind": "result", "id": "p1", "result": "' + "x" * MAX_MESSAGE_BYTES + '"}\n'
    with pytest.raises(ProtocolError, match="too large"):
        read_message(io.StringIO(huge))


def test_embedded_newlines_do_not_break_framing():
    buf = io.StringIO()
    write_message(buf, {"kind": "result", "id": "p1", "result": "a\nb"})
    buf.seek(0)
    assert read_message(buf)["result"] == "a\nb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.protocol'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/protocol.py`:

```python
"""Newline-delimited JSON framing for host <-> plugin RPC.

One JSON object per line. json.dumps escapes embedded newlines, so the
framing holds for arbitrary payloads. The size cap stops a misbehaving
plugin from exhausting host memory with a single line.
"""

import json
from typing import IO

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
VALID_KINDS = frozenset({"call", "result", "error"})


class ProtocolError(Exception):
    """The peer sent something that is not a well-formed RPP message."""


def write_message(stream: IO[str], msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    stream.write(line + "\n")
    stream.flush()


def read_message(stream: IO[str]) -> dict | None:
    while True:
        line = stream.readline()
        if line == "":
            return None  # clean EOF
        if len(line) > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                f"message too large: {len(line)} bytes exceeds {MAX_MESSAGE_BYTES}"
            )
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON on the wire: {exc}") from exc
        if not isinstance(msg, dict):
            raise ProtocolError("each message must be a JSON object")
        if msg.get("kind") not in VALID_KINDS:
            raise ProtocolError(f"message has missing or invalid kind: {msg.get('kind')!r}")
        return msg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_protocol.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/protocol.py tests/test_protocol.py
git commit -m "feat: newline-delimited JSON wire protocol with size cap"
```

---

### Task 5: Plugin SDK

**Files:**
- Create: `src/romm_hub_sdk/__init__.py`
- Create: `src/romm_hub_sdk/context.py`
- Create: `src/romm_hub_sdk/capabilities.py`
- Create: `src/romm_hub_sdk/runner.py`
- Test: `tests/test_sdk_context.py`

**Interfaces:**
- Consumes: `romm_hub.protocol.read_message/write_message`, `romm_hub.types.SearchResult`.
- Produces: `SearchProvider` ABC with `search(self, query: str, platform: str | None, limit: int) -> list[SearchResult]`; `PluginContext` with `.config: dict` and `.http`; `HttpResponse` with `.status_code`, `.text`, `.json()`; `HttpClient.get(url, params=None) -> HttpResponse`; `run_plugin(entrypoints: dict[str, str], stdin, stdout) -> None`. Task 6 spawns `python -m romm_hub_sdk.runner`.

The `requests`-shaped surface here is the mitigation for plugins having no sockets: `ctx.http.get(url).json()` is the idiom authors already know.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sdk_context.py`:

```python
from romm_hub_sdk.context import HttpClient, PluginContext


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sdk_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub_sdk'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub_sdk/__init__.py`:

```python
from romm_hub.types import SearchResult

from .capabilities import SearchProvider
from .context import HttpResponse, PluginContext

__all__ = ["SearchResult", "SearchProvider", "PluginContext", "HttpResponse"]
```

Create `src/romm_hub_sdk/context.py`:

```python
"""The plugin's view of the world.

This API offers no socket. A plugin calls ctx.http, which is an RPC back to
the host; the host checks the manifest allowlist before fetching anything.
The shape deliberately mirrors `requests` so the idiom is familiar.

Phase 1 does not sandbox the plugin subprocess, so this is the supported path
rather than the only possible one — a hostile plugin can still `import socket`
and skip the broker. See "Security: the broker model" in docs/DESIGN.md.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol


class Channel(Protocol):
    def send(self, msg: dict) -> None: ...
    def await_result(self, call_id: str) -> Any: ...


@dataclass
class HttpResponse:
    status_code: int
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient:
    def __init__(self, channel: Channel):
        self._channel = channel
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"p{self._counter}"

    def get(self, url: str, params: dict | None = None) -> HttpResponse:
        call_id = self._next_id()
        self._channel.send(
            {
                "kind": "call",
                "id": call_id,
                "method": "http.get",
                "params": {"url": url, "params": params or {}},
            }
        )
        result = self._channel.await_result(call_id)
        return HttpResponse(status_code=result["status_code"], text=result["text"])


@dataclass
class PluginContext:
    config: dict
    http: HttpClient | None
```

Create `src/romm_hub_sdk/capabilities.py`:

```python
"""Capability interfaces a plugin may implement.

Declare only what you support in manifest.toml [capabilities]. Phase 1
implements `search`; the others are defined for RPP v1 completeness and
land in later phases.
"""

from abc import ABC, abstractmethod

from romm_hub.types import SearchResult

from .context import PluginContext


class Capability(ABC):
    def __init__(self, ctx: PluginContext):
        self.ctx = ctx


class SearchProvider(Capability):
    @abstractmethod
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        """Return results for a query. Raise for a hard failure."""
```

Create `src/romm_hub_sdk/runner.py`:

```python
"""Plugin subprocess entrypoint.

Started by the host as `python -m romm_hub_sdk.runner`. Reads the plugin
directory and entrypoints from the handshake, then serves capability calls
until stdin closes.
"""

import importlib
import sys
import traceback
from typing import Any

from romm_hub.protocol import read_message, write_message

from .context import HttpClient, PluginContext


class StdioChannel:
    """Duplex channel over the process's own stdin/stdout."""

    def __init__(self, stdin, stdout):
        self._stdin = stdin
        self._stdout = stdout

    def send(self, msg: dict) -> None:
        write_message(self._stdout, msg)

    def await_result(self, call_id: str) -> Any:
        while True:
            msg = read_message(self._stdin)
            if msg is None:
                raise RuntimeError("host closed the connection mid-call")
            if msg.get("id") != call_id:
                continue
            if msg["kind"] == "error":
                raise RuntimeError(msg["error"]["message"])
            return msg["result"]


def _load(entrypoint: str, ctx: PluginContext):
    module_name, _, class_name = entrypoint.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)(ctx)


def run_plugin(stdin, stdout) -> None:
    channel = StdioChannel(stdin, stdout)
    instances: dict[str, Any] = {}
    ctx: PluginContext | None = None
    entrypoints: dict[str, str] = {}

    while True:
        msg = read_message(stdin)
        if msg is None:
            return
        if msg["kind"] != "call":
            continue

        call_id = msg["id"]
        method = msg["method"]
        params = msg.get("params") or {}

        try:
            if method == "init":
                sys.path.insert(0, params["plugin_dir"])
                entrypoints = params["entrypoints"]
                ctx = PluginContext(
                    config=params.get("config") or {}, http=HttpClient(channel)
                )
                result: Any = {"ok": True}
            elif method == "search":
                if ctx is None:
                    raise RuntimeError("init must be called before search")
                if "search" not in instances:
                    instances["search"] = _load(entrypoints["search"], ctx)
                results = instances["search"].search(
                    params["query"], params.get("platform"), params.get("limit", 50)
                )
                result = [r.model_dump() for r in results]
            else:
                raise RuntimeError(f"unknown method {method!r}")

            write_message(stdout, {"kind": "result", "id": call_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - surfaced to the host verbatim
            write_message(
                stdout,
                {
                    "kind": "error",
                    "id": call_id,
                    "error": {
                        "message": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    },
                },
            )


def main() -> None:
    run_plugin(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sdk_context.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub_sdk tests/test_sdk_context.py
git commit -m "feat: plugin SDK with requests-shaped ctx.http over RPC"
```

---

### Task 6: Broker host

**Files:**
- Create: `src/romm_hub/broker/__init__.py`
- Create: `src/romm_hub/broker/fetcher.py`
- Create: `src/romm_hub/broker/host.py`
- Test: `tests/test_broker_host.py`

**Interfaces:**
- Consumes: `protocol`, `netpolicy.check_url`, `manifest.Manifest`, `types.SearchResult`.
- Produces: `Fetcher` protocol with `get(url: str, params: dict) -> tuple[int, str]`; `HttpxFetcher`; `PluginProcess(plugin_dir, manifest, config, fetcher, timeout=30.0)` with `.start()`, `.search(query, platform, limit) -> list[SearchResult]`, `.close()`, and context-manager support; `PluginCallError(Exception)`.

This is where policy is actually enforced: the plugin's `http.get` arrives here, `check_url` runs, and only then does the fetcher open a socket.

- [ ] **Step 1: Write the failing test**

Create `tests/test_broker_host.py`:

```python
import textwrap
import time
from pathlib import Path

import pytest

from romm_hub.broker.host import PluginCallError, PluginProcess
from romm_hub.manifest import parse_manifest

MANIFEST = """
[plugin]
slug = "fake"
name = "Fake"
version = "0.1.0"
rpp_version = "1"

[capabilities]
search = "fake_plugin:Search"

[permissions]
network = ["allowed.example"]
romm_api = []
"""

PLUGIN_SRC = textwrap.dedent(
    '''
    import time

    from romm_hub_sdk import SearchProvider, SearchResult


    class Search(SearchProvider):
        def search(self, query, platform, limit):
            mode = self.ctx.config.get("mode", "static")
            if mode == "boom":
                raise ValueError("plugin exploded")
            if mode == "hang":
                time.sleep(600)
            if mode == "fetch":
                resp = self.ctx.http.get("https://allowed.example/data")
                return [SearchResult(source_id="fetched", title=resp.text)]
            if mode == "exfiltrate":
                resp = self.ctx.http.get("https://evil.example/steal")
                return [SearchResult(source_id="leaked", title=resp.text)]
            return [SearchResult(source_id="a", title=f"result for {query}")]
    '''
)


class RecordingFetcher:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url: str, params: dict) -> tuple[int, str]:
        self.calls.append(url)
        return 200, "payload"


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    (tmp_path / "fake_plugin.py").write_text(PLUGIN_SRC, encoding="utf-8")
    return tmp_path


def _proc(plugin_dir, fetcher, config=None, timeout=30.0):
    return PluginProcess(
        plugin_dir=plugin_dir,
        manifest=parse_manifest(MANIFEST),
        config=config or {},
        fetcher=fetcher,
        timeout=timeout,
    )


def test_search_returns_validated_results(plugin_dir):
    with _proc(plugin_dir, RecordingFetcher()) as proc:
        results = proc.search("oregon", None, 10)
    assert len(results) == 1
    assert results[0].title == "result for oregon"
    assert results[0].plugin == "fake"


def test_allowed_fetch_reaches_the_fetcher(plugin_dir):
    fetcher = RecordingFetcher()
    with _proc(plugin_dir, fetcher, {"mode": "fetch"}) as proc:
        results = proc.search("q", None, 10)
    assert fetcher.calls == ["https://allowed.example/data"]
    assert results[0].title == "payload"


def test_disallowed_fetch_never_reaches_the_fetcher(plugin_dir):
    fetcher = RecordingFetcher()
    with _proc(plugin_dir, fetcher, {"mode": "exfiltrate"}) as proc:
        with pytest.raises(PluginCallError, match="evil.example"):
            proc.search("q", None, 10)
    assert fetcher.calls == []


def test_plugin_exception_becomes_plugin_call_error(plugin_dir):
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "boom"}) as proc:
        with pytest.raises(PluginCallError, match="plugin exploded"):
            proc.search("q", None, 10)


def test_hung_plugin_times_out_and_is_killed(plugin_dir):
    started = time.monotonic()
    with _proc(plugin_dir, RecordingFetcher(), {"mode": "hang"}, timeout=2.0) as proc:
        with pytest.raises(PluginCallError, match="timed out"):
            proc.search("q", None, 10)
    # The watchdog must actually fire, not wait out the plugin's 600s sleep.
    assert time.monotonic() - started < 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_broker_host.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.broker'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/broker/__init__.py` (empty file).

Create `src/romm_hub/broker/fetcher.py`:

```python
"""Host-side HTTP. The only component in the system that opens a socket
on a plugin's behalf, and it is called only after netpolicy has approved
the URL.
"""

from typing import Protocol

import httpx

USER_AGENT = "romm-hub/0.1 (+https://github.com/rommapp/romm)"


class Fetcher(Protocol):
    def get(self, url: str, params: dict) -> tuple[int, str]: ...


class HttpxFetcher:
    def __init__(self, timeout: float = 30.0):
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,  # a redirect could escape the allowlist
            headers={"User-Agent": USER_AGENT},
        )

    def get(self, url: str, params: dict) -> tuple[int, str]:
        resp = self._client.get(url, params=params or None)
        return resp.status_code, resp.text

    def close(self) -> None:
        self._client.close()
```

Create `src/romm_hub/broker/host.py`:

```python
"""Supervises one plugin subprocess and brokers everything privileged.

The plugin gets no RomM token, no filesystem mount, and no sockets. Its
only way out is an `http.get` call that lands in _serve_plugin_call(),
where the manifest allowlist is enforced before any fetch happens.
"""

import subprocess
import sys
import threading
from pathlib import Path

from pydantic import ValidationError

from romm_hub.manifest import Manifest
from romm_hub.netpolicy import PolicyViolation, check_url
from romm_hub.protocol import ProtocolError, read_message, write_message
from romm_hub.types import SearchResult

from .fetcher import Fetcher


class PluginCallError(Exception):
    """A plugin call failed: it raised, timed out, or violated policy."""


class PluginProcess:
    def __init__(
        self,
        plugin_dir: Path,
        manifest: Manifest,
        config: dict,
        fetcher: Fetcher,
        timeout: float = 30.0,
    ):
        self.plugin_dir = Path(plugin_dir)
        self.manifest = manifest
        self.config = config
        self.fetcher = fetcher
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._counter = 0
        self._timed_out = False

    def __enter__(self) -> "PluginProcess":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _next_id(self) -> str:
        self._counter += 1
        return f"h{self._counter}"

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "romm_hub_sdk.runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(self.plugin_dir),
        )
        self._call(
            "init",
            {
                "plugin_dir": str(self.plugin_dir),
                "entrypoints": self.manifest.capabilities,
                "config": self.config,
            },
        )

    def _kill_for_timeout(self) -> None:
        """Watchdog. Killing the process unblocks the host's pending read."""
        self._timed_out = True
        if self._proc is not None:
            self._proc.kill()

    def _call(self, method: str, params: dict):
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise PluginCallError("plugin process is not running")

        call_id = self._next_id()
        write_message(
            self._proc.stdin,
            {"kind": "call", "id": call_id, "method": method, "params": params},
        )

        # A blocking read on a subprocess pipe cannot be given a deadline
        # portably, so the deadline is enforced by killing the peer: the
        # read then returns EOF and we report the timeout.
        watchdog = threading.Timer(self.timeout, self._kill_for_timeout)
        watchdog.daemon = True
        watchdog.start()
        try:
            while True:
                try:
                    msg = read_message(self._proc.stdout)
                except (ProtocolError, ValueError) as exc:
                    if self._timed_out:
                        break
                    raise PluginCallError(
                        f"plugin {self.manifest.slug}: {exc}"
                    ) from exc

                if msg is None:
                    break

                if msg["kind"] == "call":
                    self._serve_plugin_call(msg)
                    continue

                if msg.get("id") != call_id:
                    continue

                if msg["kind"] == "error":
                    raise PluginCallError(
                        f"plugin {self.manifest.slug}: {msg['error']['message']}"
                    )
                return msg["result"]
        finally:
            watchdog.cancel()

        if self._timed_out:
            raise PluginCallError(
                f"plugin {self.manifest.slug} timed out after {self.timeout}s "
                f"during {method!r} and was killed"
            )
        stderr = ""
        if self._proc is not None and self._proc.stderr:
            stderr = self._proc.stderr.read()
        raise PluginCallError(
            f"plugin {self.manifest.slug} exited during {method!r}: "
            f"{stderr.strip() or 'no stderr'}"
        )

    def _serve_plugin_call(self, msg: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        call_id = msg["id"]
        try:
            if msg["method"] != "http.get":
                raise PluginCallError(f"unsupported host method {msg['method']!r}")
            url = msg["params"]["url"]
            # The enforcement point. Nothing below runs for a blocked URL.
            check_url(url, self.manifest.network)
            status, text = self.fetcher.get(url, msg["params"].get("params") or {})
            reply = {
                "kind": "result",
                "id": call_id,
                "result": {"status_code": status, "text": text},
            }
        except (PolicyViolation, PluginCallError) as exc:
            reply = {"kind": "error", "id": call_id, "error": {"message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            reply = {
                "kind": "error",
                "id": call_id,
                "error": {"message": f"{type(exc).__name__}: {exc}"},
            }
        write_message(self._proc.stdin, reply)

    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        raw = self._call(
            "search", {"query": query, "platform": platform, "limit": limit}
        )
        if not isinstance(raw, list):
            raise PluginCallError(
                f"plugin {self.manifest.slug} returned {type(raw).__name__}, expected a list"
            )
        results = []
        for item in raw[:limit]:
            try:
                result = SearchResult(**item)
            except (ValidationError, TypeError) as exc:
                raise PluginCallError(
                    f"plugin {self.manifest.slug} returned an invalid result: {exc}"
                ) from exc
            result.plugin = self.manifest.slug
            results.append(result)
        return results

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except (OSError, ValueError):
            # Already killed by the watchdog; the pipe is gone.
            pass
        finally:
            for stream in (self._proc.stdout, self._proc.stderr):
                if stream:
                    stream.close()
            self._proc = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_broker_host.py -v`
Expected: PASS — 5 passed. `test_disallowed_fetch_never_reaches_the_fetcher` is the one that proves the security claim; `test_hung_plugin_times_out_and_is_killed` proves a wedged plugin cannot hang the host.

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/broker tests/test_broker_host.py
git commit -m "feat: broker host with enforced allowlist on plugin HTTP"
```

---

### Task 7: Plugin registry

**Files:**
- Create: `src/romm_hub/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `manifest.load_manifest`, `manifest.ManifestError`.
- Produces: `InstalledPlugin` dataclass with `.slug`, `.path: Path`, `.manifest: Manifest`, `.enabled: bool`, `.config: dict`; `Registry(root: Path)` with `.install(source: str, ref: str | None = None) -> InstalledPlugin`, `.installed() -> list[InstalledPlugin]`, `.get(slug) -> InstalledPlugin`, `.set_enabled(slug, enabled)`, `.set_config(slug, config)`; `RegistryError(Exception)`. State lives in `<root>/state.json`.

`install` accepts a git URL or a local path — local paths keep the Archive.org plugin testable without a network round trip.

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry.py`:

```python
import subprocess
from pathlib import Path

import pytest

from romm_hub.registry import Registry, RegistryError

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
depth = { type = "int", default = 3 }
"""


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "src-repo"
    repo.mkdir()
    (repo / "manifest.toml").write_text(MANIFEST, encoding="utf-8")
    (repo / "demo.py").write_text("class Search:\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    return repo


def test_install_from_local_repo(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    plugin = reg.install(str(source_repo))
    assert plugin.slug == "demo"
    assert plugin.manifest.name == "Demo"
    assert (plugin.path / "demo.py").exists()
    assert plugin.enabled is True


def test_installed_lists_plugins(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    reg.install(str(source_repo))
    assert [p.slug for p in reg.installed()] == ["demo"]


def test_config_defaults_come_from_manifest(tmp_path, source_repo):
    reg = Registry(tmp_path / "hub")
    plugin = reg.install(str(source_repo))
    assert plugin.config == {"depth": 3}


def test_set_config_persists_across_instances(tmp_path, source_repo):
    root = tmp_path / "hub"
    Registry(root).install(str(source_repo))
    Registry(root).set_config("demo", {"depth": 9})
    assert Registry(root).get("demo").config == {"depth": 9}


def test_disable_persists(tmp_path, source_repo):
    root = tmp_path / "hub"
    Registry(root).install(str(source_repo))
    Registry(root).set_enabled("demo", False)
    assert Registry(root).get("demo").enabled is False


def test_install_rejects_bad_manifest(tmp_path, source_repo):
    (source_repo / "manifest.toml").write_text(
        MANIFEST.replace('rpp_version = "1"', 'rpp_version = "9"'), encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=source_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bad"],
        cwd=source_repo,
        check=True,
    )
    with pytest.raises(RegistryError, match="rpp_version"):
        Registry(tmp_path / "hub").install(str(source_repo))


def test_get_unknown_slug_raises(tmp_path):
    with pytest.raises(RegistryError, match="not installed"):
        Registry(tmp_path / "hub").get("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.registry'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/registry.py`:

```python
"""Installed-plugin bookkeeping.

A plugin is a git repo, cloned to <root>/plugins/<slug> and pinned. Updates
are never automatic: re-running install with a new ref is an explicit act,
which is what stops a plugin from silently widening its own permissions.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest, ManifestError, load_manifest


class RegistryError(Exception):
    """Install, lookup, or state persistence failed."""


@dataclass
class InstalledPlugin:
    slug: str
    path: Path
    manifest: Manifest
    enabled: bool
    config: dict


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.plugins_dir = self.root / "plugins"
        self.state_path = self.root / "state.json"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    def _read_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot read {self.state_path}: {exc}") from exc

    def _write_state(self, state: dict) -> None:
        self.state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )

    def install(self, source: str, ref: str | None = None) -> InstalledPlugin:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "clone"
            cmd = ["git", "clone", "--quiet", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [source, str(staging)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RegistryError(
                    f"git clone of {source!r} failed: {result.stderr.strip()}"
                )

            try:
                manifest = load_manifest(staging / "manifest.toml")
            except ManifestError as exc:
                raise RegistryError(f"{source}: {exc}") from exc

            target = self.plugins_dir / manifest.slug
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staging, target)

        state = self._read_state()
        entry = state.get(manifest.slug, {})
        defaults = {
            key: spec["default"]
            for key, spec in manifest.config_schema.items()
            if "default" in spec
        }
        state[manifest.slug] = {
            "enabled": entry.get("enabled", True),
            "config": entry.get("config", defaults),
            "source": source,
            "ref": ref,
        }
        self._write_state(state)
        return self.get(manifest.slug)

    def get(self, slug: str) -> InstalledPlugin:
        state = self._read_state()
        if slug not in state:
            raise RegistryError(f"plugin {slug!r} is not installed")
        path = self.plugins_dir / slug
        try:
            manifest = load_manifest(path / "manifest.toml")
        except ManifestError as exc:
            raise RegistryError(f"plugin {slug!r}: {exc}") from exc
        entry = state[slug]
        return InstalledPlugin(
            slug=slug,
            path=path,
            manifest=manifest,
            enabled=entry.get("enabled", True),
            config=entry.get("config", {}),
        )

    def installed(self) -> list[InstalledPlugin]:
        return [self.get(slug) for slug in sorted(self._read_state())]

    def set_enabled(self, slug: str, enabled: bool) -> None:
        state = self._read_state()
        if slug not in state:
            raise RegistryError(f"plugin {slug!r} is not installed")
        state[slug]["enabled"] = enabled
        self._write_state(state)

    def set_config(self, slug: str, config: dict) -> None:
        state = self._read_state()
        if slug not in state:
            raise RegistryError(f"plugin {slug!r} is not installed")
        state[slug]["config"] = config
        self._write_state(state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_registry.py -v`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/registry.py tests/test_registry.py
git commit -m "feat: plugin registry with git install and persisted state"
```

---

### Task 8: Dispatcher with partial results

**Files:**
- Create: `src/romm_hub/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `registry.InstalledPlugin`, `broker.host.PluginProcess`, `broker.fetcher.Fetcher`, `types.SearchResult`.
- Produces: `PluginStatus` dataclass with `.slug`, `.ok: bool`, `.count: int`, `.error: str | None`; `SearchOutcome` dataclass with `.results: list[SearchResult]`, `.statuses: list[PluginStatus]`, and properties `.responded: int` and `.total: int`; `search_all(plugins, fetcher, query, platform=None, limit=50, timeout=30.0, process_factory=None) -> SearchOutcome`.

A search where one plugin dies must return the others' results **and** say so. Silently returning three sources as though it were four is the failure mode this task exists to prevent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatcher.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.dispatcher'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/dispatcher.py`:

```python
"""Fans a search out across enabled plugins, in parallel, in isolation.

A crashed or hung plugin costs its own results and nothing else. The caller
always learns how many sources actually answered.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .broker.host import PluginProcess
from .types import SearchResult

MAX_PARALLEL = 8


@dataclass
class PluginStatus:
    slug: str
    ok: bool
    count: int = 0
    error: str | None = None


@dataclass
class SearchOutcome:
    results: list[SearchResult] = field(default_factory=list)
    statuses: list[PluginStatus] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.statuses)

    @property
    def responded(self) -> int:
        return sum(1 for s in self.statuses if s.ok)

    @property
    def complete(self) -> bool:
        return self.responded == self.total


def _default_factory(plugin, fetcher, timeout) -> PluginProcess:
    return PluginProcess(
        plugin_dir=plugin.path,
        manifest=plugin.manifest,
        config=plugin.config,
        fetcher=fetcher,
        timeout=timeout,
    )


def search_all(
    plugins,
    fetcher,
    query: str,
    platform: str | None = None,
    limit: int = 50,
    timeout: float = 30.0,
    process_factory=None,
) -> SearchOutcome:
    factory = process_factory or _default_factory
    candidates = [
        p for p in plugins if p.enabled and "search" in p.manifest.capabilities
    ]

    def run(plugin) -> tuple[PluginStatus, list[SearchResult]]:
        try:
            with factory(plugin, fetcher, timeout) as proc:
                results = proc.search(query, platform, limit)
            return PluginStatus(plugin.slug, True, len(results)), results
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            return PluginStatus(plugin.slug, False, 0, f"{type(exc).__name__}: {exc}"), []

    outcome = SearchOutcome()
    if not candidates:
        return outcome

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(candidates))) as pool:
        for status, results in pool.map(run, candidates):
            outcome.statuses.append(status)
            outcome.results.extend(results)

    outcome.statuses.sort(key=lambda s: s.slug)
    return outcome
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatcher.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: parallel search dispatch with per-plugin partial results"
```

---

### Task 9: CLI

**Files:**
- Create: `src/romm_hub/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Registry`, `search_all`, `HttpxFetcher`.
- Produces: `main(argv: list[str] | None = None) -> int`; `default_root() -> Path` honouring `$ROMM_HUB_HOME` and defaulting to `~/.romm-hub`. Commands: `plugin install <source> [--ref]`, `plugin list`, `plugin enable|disable <slug>`, `search <query> [--platform] [--limit]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import subprocess
from pathlib import Path

import pytest

from romm_hub.cli import main

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
"""

PLUGIN = """
from romm_hub_sdk import SearchProvider, SearchResult


class Search(SearchProvider):
    def search(self, query, platform, limit):
        return [SearchResult(source_id="1", title=f"hit: {query}")]
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


def test_install_then_list(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["plugin", "install", str(source_repo)]) == 0
    assert main(["plugin", "list"]) == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "enabled" in out


def test_search_end_to_end(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    assert main(["search", "oregon trail"]) == 0
    out = capsys.readouterr().out
    assert "hit: oregon trail" in out
    assert "1 of 1 source" in out


def test_search_with_no_plugins_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    assert main(["search", "anything"]) == 0
    assert "no plugins" in capsys.readouterr().out.lower()


def test_disable_removes_plugin_from_search(tmp_path, source_repo, monkeypatch, capsys):
    monkeypatch.setenv("ROMM_HUB_HOME", str(tmp_path / "home"))
    main(["plugin", "install", str(source_repo)])
    main(["plugin", "disable", "demo"])
    main(["search", "oregon"])
    assert "hit:" not in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'romm_hub.cli'`

- [ ] **Step 3: Write the implementation**

Create `src/romm_hub/cli.py`:

```python
"""romm-hub command line.

Phase 1 surface: install plugins, list them, and search across them.
"""

import argparse
import os
import sys
from pathlib import Path

from .broker.fetcher import HttpxFetcher
from .dispatcher import search_all
from .registry import Registry, RegistryError


def default_root() -> Path:
    return Path(os.environ.get("ROMM_HUB_HOME", Path.home() / ".romm-hub"))


def _cmd_plugin_install(args) -> int:
    reg = Registry(default_root())
    plugin = reg.install(args.source, args.ref)
    caps = ", ".join(sorted(plugin.manifest.capabilities))
    print(f"installed {plugin.slug} {plugin.manifest.version} (capabilities: {caps})")
    print(f"  network allowlist: {plugin.manifest.network or '(none)'}")
    return 0


def _cmd_plugin_list(args) -> int:
    plugins = Registry(default_root()).installed()
    if not plugins:
        print("no plugins installed")
        return 0
    for p in plugins:
        state = "enabled" if p.enabled else "disabled"
        caps = ",".join(sorted(p.manifest.capabilities))
        print(f"{p.slug:<20} {p.manifest.version:<10} {state:<9} [{caps}]")
    return 0


def _cmd_plugin_enable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, True)
    print(f"enabled {args.slug}")
    return 0


def _cmd_plugin_disable(args) -> int:
    Registry(default_root()).set_enabled(args.slug, False)
    print(f"disabled {args.slug}")
    return 0


def _cmd_search(args) -> int:
    plugins = Registry(default_root()).installed()
    searchable = [p for p in plugins if p.enabled and "search" in p.manifest.capabilities]
    if not searchable:
        print("no plugins available for search — install one with 'romm-hub plugin install'")
        return 0

    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            plugins,
            fetcher=fetcher,
            query=args.query,
            platform=args.platform,
            limit=args.limit,
        )
    finally:
        fetcher.close()

    for r in outcome.results:
        size = f"{r.size_bytes / 1_048_576:.1f} MB" if r.size_bytes else "-"
        flag = " [stream-only]" if r.extra.get("stream_only") == "true" else ""
        print(f"{r.plugin:<14} {r.platform or '-':<12} {size:>10}  {r.title}{flag}")

    print()
    print(f"{outcome.responded} of {outcome.total} sources responded, {len(outcome.results)} results")
    for status in outcome.statuses:
        if not status.ok:
            print(f"  ! {status.slug}: {status.error}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="romm-hub", description="RomM plugin host")
    sub = parser.add_subparsers(dest="command", required=True)

    plugin = sub.add_parser("plugin", help="manage plugins")
    psub = plugin.add_subparsers(dest="plugin_command", required=True)

    install = psub.add_parser("install", help="install a plugin from a git repo or path")
    install.add_argument("source")
    install.add_argument("--ref", default=None, help="tag or branch to pin")
    install.set_defaults(func=_cmd_plugin_install)

    listing = psub.add_parser("list", help="list installed plugins")
    listing.set_defaults(func=_cmd_plugin_list)

    enable = psub.add_parser("enable")
    enable.add_argument("slug")
    enable.set_defaults(func=_cmd_plugin_enable)

    disable = psub.add_parser("disable")
    disable.add_argument("slug")
    disable.set_defaults(func=_cmd_plugin_disable)

    search = sub.add_parser("search", help="search across enabled plugins")
    search.add_argument("query")
    search.add_argument("--platform", default=None)
    search.add_argument("--limit", type=int, default=25)
    search.set_defaults(func=_cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/romm_hub/cli.py tests/test_cli.py
git commit -m "feat: romm-hub CLI with plugin management and search"
```

---

### Task 10: Archive.org plugin

**Files:**
- Create: `plugins-dev/archive-org/manifest.toml`
- Create: `plugins-dev/archive-org/archive_org/__init__.py`
- Create: `plugins-dev/archive-org/archive_org/search.py`
- Create: `plugins-dev/archive-org/README.md`
- Test: `tests/test_archive_org.py`

**Interfaces:**
- Consumes: `romm_hub_sdk.SearchProvider`, `romm_hub_sdk.SearchResult`, `ctx.http`.
- Produces: `archive_org.search:Search`, a `SearchProvider`. Config keys: `collections: list[str]` (default `["softwarelibrary"]`).

Uses the endpoint shape verified during design. `collection` is requested in `fl[]` so `stream_only` can be flagged **without a second HTTP call**, and `emulator` gives the platform hint.

- [ ] **Step 1: Write the failing test**

Create `tests/test_archive_org.py`:

```python
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"
sys.path.insert(0, str(PLUGIN_ROOT))

from archive_org.search import Search  # noqa: E402

from romm_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

# Trimmed from a real advancedsearch.php response captured during design.
FIXTURE = {
    "response": {
        "numFound": 8903,
        "docs": [
            {
                "identifier": "msdos_Oregon_Trail_The_1990",
                "title": "The Oregon Trail",
                "collection": ["softwarelibrary_msdos_games", "stream_only", "emulation"],
                "item_size": 359527,
            },
            {
                "identifier": "msdos_Old_Gold_1995",
                "title": "Old Gold",
                "collection": ["softwarelibrary_msdos_games"],
                "item_size": 12345,
            },
            {"identifier": "no_title_item", "collection": []},
        ],
    }
}


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return HttpResponse(status_code=200, text=json.dumps(self.payload))


def make_search(payload=FIXTURE, config=None):
    http = FakeHttp(payload)
    ctx = PluginContext(config=config or {}, http=http)
    return Search(ctx), http


def test_returns_results_for_each_valid_doc():
    search, _ = make_search()
    results = search.search("oregon", None, 25)
    assert [r.source_id for r in results] == [
        "msdos_Oregon_Trail_The_1990",
        "msdos_Old_Gold_1995",
    ]


def test_stream_only_is_flagged_without_a_second_request():
    search, http = make_search()
    results = search.search("oregon", None, 25)
    assert results[0].extra["stream_only"] == "true"
    assert results[1].extra["stream_only"] == "false"
    assert len(http.calls) == 1


def test_docs_without_a_title_are_skipped():
    search, _ = make_search()
    assert all(r.source_id != "no_title_item" for r in search.search("x", None, 25))


def test_size_is_carried_through():
    search, _ = make_search()
    assert search.search("x", None, 25)[0].size_bytes == 359527


def test_url_points_at_the_item_details_page():
    search, _ = make_search()
    result = search.search("x", None, 25)[0]
    assert result.url == "https://archive.org/details/msdos_Oregon_Trail_The_1990"


def test_query_is_scoped_to_configured_collections():
    search, http = make_search(config={"collections": ["softwarelibrary_msdos_games"]})
    search.search("oregon", None, 25)
    _, params = http.calls[0]
    assert "softwarelibrary_msdos_games" in params["q"]
    assert "oregon" in params["q"]


def test_limit_is_passed_as_rows():
    search, http = make_search()
    search.search("oregon", None, 7)
    assert http.calls[0][1]["rows"] == 7


def test_empty_response_returns_no_results():
    search, _ = make_search(payload={"response": {"numFound": 0, "docs": []}})
    assert search.search("nothing", None, 25) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_archive_org.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'archive_org'`

- [ ] **Step 3: Write the implementation**

Create `plugins-dev/archive-org/manifest.toml`:

```toml
[plugin]
slug        = "archive-org"
name        = "Archive.org"
version     = "0.1.0"
rpp_version = "1"
license     = "MIT"

[capabilities]
search = "archive_org.search:Search"

[permissions]
network  = ["archive.org", "*.archive.org"]
romm_api = []

[config]
collections = { type = "list[str]", default = ["softwarelibrary"] }
```

Create `plugins-dev/archive-org/archive_org/__init__.py` (empty file).

Create `plugins-dev/archive-org/archive_org/search.py`:

```python
"""Archive.org search over the advancedsearch.php scraping API.

`collection` is requested up front so stream-only items can be flagged
without a second round trip: Archive.org marks non-downloadable items by
putting them in the `stream_only` collection, and that flag is what decides
whether a later phase offers import or streaming.
"""

from romm_hub_sdk import SearchProvider, SearchResult

ENDPOINT = "https://archive.org/advancedsearch.php"
DETAILS = "https://archive.org/details/"
DEFAULT_COLLECTIONS = ["softwarelibrary"]
FIELDS = ["identifier", "title", "collection", "item_size", "emulator"]


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        collections = self.ctx.config.get("collections") or DEFAULT_COLLECTIONS
        scope = " OR ".join(collections)
        q = f"({query}) AND collection:({scope})"

        response = self.ctx.http.get(
            ENDPOINT,
            params={
                "q": q,
                "fl[]": FIELDS,
                "rows": limit,
                "page": 1,
                "output": "json",
            },
        )
        docs = response.json().get("response", {}).get("docs", [])

        results: list[SearchResult] = []
        for doc in docs:
            identifier = doc.get("identifier")
            title = doc.get("title")
            if not identifier or not title:
                # Items without a title are unusable downstream; skip rather
                # than invent one.
                continue
            collection = doc.get("collection") or []
            if isinstance(collection, str):
                collection = [collection]
            results.append(
                SearchResult(
                    source_id=identifier,
                    title=title if isinstance(title, str) else str(title),
                    platform=doc.get("emulator"),
                    size_bytes=doc.get("item_size"),
                    url=f"{DETAILS}{identifier}",
                    extra={
                        "stream_only": "true" if "stream_only" in collection else "false",
                        "collections": ",".join(collection),
                    },
                )
            )
        return results
```

Create `plugins-dev/archive-org/README.md`:

```markdown
# Archive.org plugin for RomM Hub

Implements the RPP v1 `search` capability against Archive.org's
`advancedsearch.php` API.

## Install

    romm-hub plugin install https://github.com/<you>/romm-hub-archive-org --ref v0.1.0

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `collections` | `list[str]` | `["softwarelibrary"]` | Archive.org collections to scope searches to |

## Notes

Results carry `extra.stream_only`. Archive.org marks items that may only be
played in-browser by placing them in the `stream_only` collection; later
phases route those to streaming rather than import.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_archive_org.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add plugins-dev/archive-org tests/test_archive_org.py
git commit -m "feat: Archive.org search plugin with stream_only flagging"
```

---

### Task 11: Live end-to-end verification

**Files:**
- Create: `tests/test_live_e2e.py`
- Modify: `README.md` (create if absent)

**Interfaces:**
- Consumes: everything.
- Produces: a `@pytest.mark.live` test, deselected by default via the `addopts` set in Task 1. Run explicitly with `-m live`.

This is the task that proves Phase 1's actual goal: a real search, through a real subprocess, against the real Archive.org, with the allowlist enforced the whole way.

- [ ] **Step 1: Write the failing test**

Create `tests/test_live_e2e.py`:

```python
"""End-to-end against the real Archive.org. Deselected unless -m live."""

import subprocess
from pathlib import Path

import pytest

from romm_hub.broker.fetcher import HttpxFetcher
from romm_hub.dispatcher import search_all
from romm_hub.registry import Registry

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "archive-org"


@pytest.fixture
def installed_registry(tmp_path):
    # The plugin dir must be a git repo for install() to clone it.
    if not (PLUGIN_ROOT / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=PLUGIN_ROOT, check=True)
        subprocess.run(["git", "add", "-A"], cwd=PLUGIN_ROOT, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "wip"],
            cwd=PLUGIN_ROOT,
            check=True,
        )
    reg = Registry(tmp_path / "hub")
    reg.install(str(PLUGIN_ROOT))
    return reg


@pytest.mark.live
def test_real_search_returns_results(installed_registry):
    fetcher = HttpxFetcher()
    try:
        outcome = search_all(
            installed_registry.installed(),
            fetcher=fetcher,
            query="oregon trail",
            limit=5,
        )
    finally:
        fetcher.close()

    assert outcome.complete, [s.error for s in outcome.statuses if not s.ok]
    assert outcome.results, "expected at least one result from Archive.org"
    assert all(r.plugin == "archive-org" for r in outcome.results)
    assert all(r.title for r in outcome.results)
    assert all(
        r.extra["stream_only"] in {"true", "false"} for r in outcome.results
    )
```

- [ ] **Step 2: Run the full suite plus the live test**

Run:

```bash
python -m pytest -v
```

Expected: PASS — all offline tests pass, the live test shows as deselected.

Run:

```bash
python -m pytest -m live -v
```

Expected: PASS — 1 passed, having really queried Archive.org.

- [ ] **Step 3: Verify the CLI goal by hand**

Run:

```bash
python -m romm_hub.cli plugin install ./plugins-dev/archive-org
```

Expected output includes `installed archive-org 0.1.0 (capabilities: search)` and the network allowlist line.

Run:

```bash
python -m romm_hub.cli search "oregon trail" --limit 5
```

Expected: a table of Archive.org results, some flagged `[stream-only]`, ending with `1 of 1 sources responded, N results`.

- [ ] **Step 4: Write the README**

Create `README.md`:

```markdown
# RomM Hub

A plugin standard, and a host that runs it, for
[RomM](https://github.com/rommapp/romm). It runs as a sidecar: RomM itself is
never modified.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and
[docs/DESIGN-federation-netplay.md](docs/DESIGN-federation-netplay.md) for the
deferred federation and multiplayer work.

## Status

**Phase 1** — plugin engine, broker, and search. No import, no web UI yet.

## Quick start

    python -m pip install -e ".[dev]"
    python -m romm_hub.cli plugin install ./plugins-dev/archive-org
    python -m romm_hub.cli search "oregon trail" --limit 5

## Tests

    python -m pytest          # offline; live tests deselected
    python -m pytest -m live  # also hits the real Archive.org

## Security model

Plugins run as subprocesses and are given **no RomM token and no filesystem
mount**, and the plugin API offers no way to open a socket. A plugin calls
`ctx.http`, which is an RPC back to the host; the host checks the URL against
the plugin's declared `network` allowlist before opening any connection.

That check is genuinely enforced **on the broker path**. `check_url` is
unavoidable en route to the only code that opens a socket, and the matcher is
adversarially tested. `tests/test_netpolicy.py` and
`test_disallowed_fetch_never_reaches_the_fetcher` in
`tests/test_broker_host.py` are the tests that hold it up; if either regresses,
the allowlist stops meaning anything at all.

> ### ⚠️ Phase 1 does not sandbox plugins
>
> The plugin subprocess is a plain `Popen` of the Python interpreter — no
> namespace, no seccomp filter, no job object, no separate uid. Plugin code
> inherits everything the host process can do, so a **hostile** plugin can
> ignore `ctx.http`, open its own socket to an undeclared host, read files
> outside its directory, and spawn processes. None of that crosses the broker,
> so none of it is checked.
>
> In Phase 1 the allowlist therefore constrains *cooperative* plugins and
> documents intent. It is not a containment boundary. **Only install plugins
> you trust.**
>
> Real isolation (bubblewrap/nsjail `--unshare-net --ro-bind`, or the container
> boundary) is a blocking prerequisite for Phase 2, which is where the Hub
> first holds a RomM admin token. See
> [docs/DESIGN.md](docs/DESIGN.md#security-the-broker-model).
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_live_e2e.py README.md
git commit -m "test: live end-to-end Archive.org search through the broker"
```

---

## Phase 1 Done Criteria

- [ ] `python -m pytest` passes with no live network access.
- [ ] `python -m pytest -m live` really queries Archive.org and passes.
- [ ] `romm-hub search "oregon trail"` prints results from a plugin running in its own subprocess.
- [ ] A plugin requesting a non-allowlisted host is blocked before a socket opens, proven by test.
- [ ] A crashing plugin does not lose other plugins' results, proven by test.
- [ ] A hung plugin is killed at its deadline rather than blocking the host, proven by test.
- [ ] RomM has not been modified and is not contacted.

## Explicitly Not in Phase 1

Import, RomM adapter, job queue, web UI, `metadata`/`stream`/`cores`, federation, netplay. Phase 2 begins at the RomM adapter and the chunked upload API.

Three things from DESIGN.md are deliberately deferred rather than missed:

- **`secret` config storage.** RPP v1 specifies the type; Phase 1 *rejects* it with a clear message (Task 2) rather than half-implementing encryption. It lands with sub-project C, which is the only thing that needs it.
- **The plugin update flow** (`git fetch` + re-pin, with the manifest diff shown before acceptance). Phase 1 installs and pins; re-running `install` replaces. The diff-and-confirm step is a Phase 3 UI concern, and until a plugin catalog exists there is nothing to update from.
- **Per-plugin memory caps.** `resource.setrlimit` is POSIX-only and development is on Windows, so Phase 1 ships the output-size cap (`MAX_MESSAGE_BYTES`, Task 4) and the wall-clock timeout (Task 6) and leaves memory limiting to the container boundary in Phase 3. Stated here so the gap is known rather than assumed covered.
