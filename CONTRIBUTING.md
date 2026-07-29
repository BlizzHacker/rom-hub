# Contributing

Two kinds of contribution: **writing a plugin**, which lives in your own
repository and needs nothing from this one but a catalog entry; and **changing
the Hub**, which is the rest of this file.

---

# Writing a plugin

A plugin is a small Python package in its own git repository. The Hub clones it
at a tag and runs it as a subprocess.

**The one idea to hold on to:** a plugin never performs a privileged action. It
*describes* one and the host performs it. You return "fetch this URL and call
it `game.zip`"; the host checks the URL against your allowlist, fetches it,
hashes it, dedups it and uploads it — holding the library credential you never
see. That is why a plugin works against RomM, Gaseous and Retrom without
knowing which is there, and why an untrusted plugin is a bounded problem.

## Layout

    your-plugin/
      manifest.toml
      LICENSE
      README.md
      your_plugin/
        __init__.py
        search.py

The package directory name is yours; `manifest.toml` is what names the entry
points, so it does not have to match the slug (`itch-io` ships `itch_io/`).

## The manifest

`manifest.toml` is the plugin's declaration of what it is and what it needs.
It is validated on install and **everything unknown is rejected** — a
permissive parser here would quietly weaken the security model, so an
unrecognised capability, config type or key is an error rather than an
ignored line.

```toml
[plugin]
slug        = "your-plugin"     # lowercase, digits, hyphens; max 64 chars
name        = "Your Plugin"     # what a human sees
version     = "0.1.0"
rpp_version = "1"               # the string "1". Not the integer 1.
license     = "MIT"

[capabilities]
# capability = "module:Class". Declare only what you implement.
search = "your_plugin.search:Search"

[permissions]
network  = ["example.org", "*.example.org"]
romm_api = []

[config]
# Operator-settable. Types: str, int, bool, list[str].
max_pages = { type = "int", default = 3 }
```

Notes that have each cost somebody time:

- **`rpp_version` must be the string `"1"`.** The parser does not coerce, so
  `rpp_version = 1` is rejected.
- **`license` is yours, not this repository's.** Your plugin is a separate
  work; put a `LICENSE` file beside the manifest. The seven listed plugins are
  MIT, and so is the Hub, but nothing requires you to match.
- **`secret` is not a usable config type.** RPP v1 reserves it and *this host
  rejects it* — a field declaring `type = "secret"` fails to install with
  "reserved in RPP v1 but not implemented in Phase 1". So a plugin needing an
  API key stores it as a plain `str`, **in clear text in the Hub's config**.
  If yours needs a credential, say so in your README in those words, and set
  `key_required` in your catalog entry. `retroachievements` is the worked
  example.
- **`romm_api` is reserved.** It parses; nothing grants a plugin library access
  in RPP v1.

## The five capabilities

All five are implemented by the host. Import the interfaces from
`rom_hub_sdk`; each is an ABC taking `ctx` and implementing one or two methods.

| Capability | Interface | You return | The host then |
|---|---|---|---|
| `search` | `SearchProvider.search(query, platform, limit)` | `list[SearchResult]` | prints/merges them; no privileged action |
| `importer` | `ImportProvider.plan(result)` | `FetchPlan` | fetches, hashes, dedups, uploads, files into a collection |
| `metadata` | `MetadataProvider.enrich(rom)` | `MetadataPatch` | fetches the artwork and writes the fields |
| `stream` | `StreamProvider.resolve(result)` | `StreamTarget` | validates the target and hands it on |
| `cores` | `CoreProvider.list()` / `.plan(core)` | `list[CoreArtifact]` / `FetchPlan` | downloads the core to the operator's cores directory |

```python
from rom_hub_sdk import SearchProvider, SearchResult


class Search(SearchProvider):
    def search(self, query: str, platform: str | None, limit: int) -> list[SearchResult]:
        response = self.ctx.http.get(
            "https://example.org/api/search", params={"q": query}
        )
        return [
            SearchResult(
                source_id=item["id"],      # YOUR id for it; import takes this back
                title=item["title"],
                platform="dos",
                url=item.get("page"),      # shown to a human, never fetched
                extra={},                  # str -> str only
            )
            for item in response.json()["items"][:limit]
        ]
```

Three rules that apply to every capability:

1. **Raise for a hard failure.** An empty list, or an empty `MetadataPatch()`,
   means "I looked and there is nothing", and the host leaves the library
   alone. Those are different answers and the Hub reports them differently.
2. **Refuse rather than guess.** If you cannot map a platform, or cannot tell
   which of your items a rom is, say so by name. `archive-org` fails an
   unmapped emulator as "needs mapping" instead of filing a ROM under a guess;
   `itch-io`'s importer refuses *every* import and names the reason. A refusal
   a reader can act on is worth more than a wrong success.
3. **Never fabricate a URL.** A plugin that invents a download target is one
   whose refusals cannot be believed either.

## `ctx.http` and the network allowlist

`ctx` is a `PluginContext` with exactly two attributes: `ctx.config` (your
`[config]` schema merged with the operator's settings) and `ctx.http`.

```python
response = self.ctx.http.get(url, params={"q": query})
response.status_code   # int
response.text          # str
response.json()        # parsed
```

`ctx.http.get` is the only route out. It is **an RPC back to the host**, not a
socket: the host checks the URL against your manifest's `network` list and only
then opens a connection. `GET` is the only verb.

The allowlist is host patterns. `example.org` matches that host exactly;
`*.example.org` matches one or more leading labels but **not** the bare domain
— list both if you need both. `https` only. Userinfo tricks
(`https://example.org@evil.test/`) are stripped before matching, and every hop
of a redirect is re-checked.

**Every URL you return is checked too**, not just the ones you fetch: a
`FetchPlan` file URL, a `MetadataPatch.artwork_url`, a `StreamTarget` of kind
`url`, and a core download. So declare the host the *host* will fetch from —
`itch-io` declares `img.itch.zone` purely because the Hub fetches the cover
that plugin names, and would otherwise refuse every enrich.

Declare the hosts you actually use and no more. The allowlist is the one thing
a reader can judge you on before installing, and a search plugin asking for
hosts unrelated to its source is the thing people are told to be suspicious of.

## What a plugin does not get

No library token. No filesystem mount. No sockets. And **almost no
environment**: the subprocess is built from an empty environment upward with
only `PATH`, a handful of OS essentials and `PYTHONIOENCODING`, so a secret in
the operator's shell is not visible to you. Write for that.

On Linux the subprocess also installs a seccomp filter on itself before
importing your code, so `import socket` raises `PermissionError` and `execve`
is denied. **File reads are not confined** and cannot be by seccomp. Windows
cannot confine at all. Both facts are stated plainly in the README and in
`docs/PLUGINS.md`, and they are why "install only plugins you trust" is said as
strongly as it is.

## Testing yours

Run the capability class directly against a fake `ctx` — that is what the
seven plugins' suites do, with recorded fixtures rather than live requests, so
the whole suite runs offline. Then install it for real:

    rom-hub plugin install /path/to/your-plugin     # needs to be a git repo
    rom-hub search "something"

## Getting listed in the catalog

[`docs/PLUGINS.md`](docs/PLUGINS.md) is generated from
[`catalog/plugins.json`](catalog/plugins.json). Open a pull request adding an
entry, then run `python scripts/render_directory.py` — **never hand-edit the
generated page**; a test fails if it is stale.

**The catalog grants nothing.** Listing does not give your plugin any
permission: the network allowlist that is enforced is the one in your
`manifest.toml`, read at install time. The catalog's `network` field is a copy
for a human to read before installing, and
`test_catalog_cannot_widen_permissions` pins that the broker never consults the
catalog at all. That is deliberate — otherwise whoever hosts the directory
could silently widen every installed plugin's reach.

An entry must:

- point `repository`, `install` and `download` at **https** URLs that resolve.
  `install` is what git clones; `download` is a link a reader follows;
- **pin `download` to the exact tag named in `ref`**, and tag your release. A
  moving branch means a later install silently ships different code, and the
  catalog loader rejects a `download` that does not contain its `ref`;
- declare `rpp_version` `"1"`;
- list the `network` hosts your manifest actually requests, and the
  `capabilities` it actually declares. Both are checked against the manifest;
- carry a one-line `description` and a **`terms`** paragraph stating **your
  source's** licensing position in plain language — not your plugin's own
  licence, which is its `LICENSE` file. A directory that says where to get ROMs
  and stays quiet about whether they may lawfully be got is doing half the job.
  Both must be non-empty: a blank cell reads like "nothing to declare" rather
  than "nobody filled this in";
- set `search_only` if your importer cannot complete (it renders as "cannot
  import", because a plugin can implement `metadata` and still never fetch a
  file) and `key_required` if the plugin is useless without a credential. Both
  are things a reader needs before installing, not after filing a bug;
- set `status`: `ok`, `caveat` (works, read the comments) or `broken`;
- leave `in_tree` `false` unless your plugin has no published repository at all.

---

# Changing the Hub

    python -m pip install -e ".[dev]"
    python -m pytest -q          # offline; live tests deselected
    python -m pytest -m live -q  # also hits the real network

On Windows and macOS the live tests need `ROM_HUB_ALLOW_UNSANDBOXED=1`, for the
same reason the CLI does.

Every push and pull request runs the same suite on Linux and Windows, on Python
3.12 and 3.13 — see [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
Two things there will fail a build that pytest itself would call green, and
both are deliberate:

- **On Linux the seccomp tests must pass, not skip.** `scripts/ci_gate.py`
  requires each of them by name against the junit report. If you rename or move
  one, update the workflow in the same commit; the gate failing loudly is the
  intended behaviour, not an obstacle.
- **A new skip is a failure.** Each platform declares which skip *reasons* are
  legitimate there. If you add a skip, add its reason to the matching job with
  a note saying why, rather than widening the pattern until it matches
  anything.

A few standing rules, each of which exists because breaking it broke something:

- **No test may reach the network unless it is marked `live`.** The default
  suite is offline, which is why the plugins have a development copy in
  `plugins-dev/` — see [`plugins-dev/README.md`](plugins-dev/README.md). CI
  proves the deselection still holds rather than trusting `addopts`.
- **`docs/PLUGINS.md` is generated.** Change `catalog/plugins.json` and re-run
  `scripts/render_directory.py`.
- **Do not weaken `sandbox.py`'s denylist**, and never call `sandbox.install()`
  in the pytest process — it confines the test runner itself.
- **Do not put a real hostname, IP, or container id in this repository.** Use
  `your-server.example`, `<ctid>`, `<repo>`.
- **Be exact about security in prose.** The claims in the README are scoped the
  way they are on purpose: the broker enforces the allowlist, seccomp confines
  the plugin on Linux, file reads are *not* confined, and Windows cannot
  sandbox at all. Softening any of those for a friendlier read is the one
  change that will be rejected outright.

This project is MIT licensed ([LICENSE](LICENSE)); contributions are accepted
on those terms.
