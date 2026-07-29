# RomM Hub — Design

**Status:** draft, pending review
**Date:** 2026-07-28
**Scope of this document:** sub-projects **A** and **B** only (see [Scope](#scope)).

---

## Problem

[RomM](https://github.com/rommapp/romm) is a self-hosted ROM library manager. Its
metadata providers (IGDB, MobyGames, ScreenScraper, RAWG, LaunchBox, Hasheous,
Flashpoint, SteamGridDB, TGDB) are **hardcoded** — `backend/handler/metadata/`
contains one module per provider, each wired in by name. Adding a source means
editing core.

We want the qBittorrent model instead: core ships an **engine**, the community
ships **plugins**. A user pastes a URL, gets a new source, and core never changes.

This design covers a **sidecar** that adds that capability to an unmodified RomM,
plus the first plugin (Archive.org) as the proof that the contract is real.

### Why a sidecar and not a fork

The deployed RomM (LXC 104) is a `ghcr.io/rommapp/romm:latest` container that
pulls updates. A fork means either abandoning updates or carrying a permanent
merge burden. The sidecar keeps RomM byte-identical to upstream, which also
keeps the door open to contributing the *protocol* upstream later without
having to disentangle it from a pile of local changes.

**API feasibility is confirmed.** RomM exposes 118 API paths, including
everything the Hub needs:

| Need | Endpoint |
|---|---|
| Push a ROM into the library | `POST /api/roms/upload/start` → `PUT /api/roms/upload/{id}` → `POST /api/roms/upload/{id}/complete` |
| Create missing platforms | `POST /api/platforms` |
| Group imports | `POST /api/collections`, `POST /api/collections/{id}/roms` |
| Auth | `POST /api/token`, `/api/client-tokens` |
| Dedup against existing library | `GET /api/roms`, `GET /api/search/roms` |

Zero core changes required.

---

## Scope

The original request bundles four separable products. Only **A** and **B** are
designed here; **C** and **D** are deliberately deferred but the design does not
foreclose them.

| | Sub-project | Status |
|---|---|---|
| **A** | Plugin engine + host (the qBittorrent-style extension system) | **designed here** |
| **B** | Archive.org plugin (search, import, metadata, stream, cores) | **designed here** |
| **C** | Cross-server federation / friend libraries | deferred |
| **D** | Multiplayer + netplay | deferred |

**Deferred is not discarded.** C and D were given a design pass specifically to
find out what they demand of the RPP contract before v1 is frozen — see
[DESIGN-federation-netplay.md](DESIGN-federation-netplay.md). The outcome:
**one addition and two reservations**, both folded in below.

Relevant existing surfaces, as actually verified:

- `/api/client-tokens/{id}/pair` + `/api/client-tokens/pair/{code}/status` — a
  device-pairing code flow.
- `/api/netplay/list` — `Get Rooms`. RomM 4.9.2 already has a netplay room
  concept.
- `/api/sync/*` — **NOT server-to-server.** Verified: this is save-state sync
  between a *client device* and the server ("the client sends its current save
  state, and the server returns operations to bring both sides in sync"). It is
  handheld/device sync and does **not** give federation a head start.

C remains a distributed-systems problem (identity, trust, partial availability,
NAT traversal) larger than A and B combined, and is not built in this pass.

---

## Two named things

Keeping these separate is deliberate — it is what makes a future upstream
contribution a clean proposal rather than "please merge my application."

- **RomM Provider Protocol (RPP)** — the versioned *contract* between a host
  and a plugin. Portable; potentially upstreamable.
- **RomM Hub** — *our* implementation of an RPP host. Stays ours.

---

## Architecture

```
                      Traefik (LXC 107)
                      ├── romm.moveweight.com ──→ RomM :8080   [UNMODIFIED]
                      │      └─ injects <script src="hub…/nav.js">
                      └── hub.moveweight.com  ──→ Hub  :8090
                                                   │
 LXC 104 ┌─────────────────────────────────────────┼────────────────┐
         │  docker network: romm_default           │                │
         │                                         │                │
         │   RomM :8080  ◄──── RomM Adapter ◄── Hub Core            │
         │   (untouched)       (sole token holder)  │                │
         │                                          │                │
         │                        ┌─────────────────┴──┐             │
         │                        │  Broker + Fetcher  │             │
         │                        └─────────────────┬──┘             │
         │                   JSON-RPC over stdio    │                │
         │             ┌──────────┬─────────────────┴────┐           │
         │       [plugin A]   [plugin B]           [plugin C]        │
         │       subprocess   subprocess           subprocess        │
         │       no token · no mounts · not sandboxed (Phase 1)      │
         │                                                           │
         │   /mnt/usb1/roms     ◄── written ONLY by Hub Core         │
         │   /opt/romm-stream   ◄── cores installed by Hub Core      │
         └───────────────────────────────────────────────────────────┘
```

### Deployment

A **separate Docker Compose stack on LXC 104**, alongside RomM. This is still a
"separate installation" — brought up, torn down and upgraded independently —
but it shares the `romm_default` docker network, so API calls have no extra
network hop, and it already has the `/mnt/usb1` mounts and `/opt/romm-stream`
that the import and stream capabilities need.

Paths and the RomM base URL are config-driven, so relocating to its own LXC
later is a config change, not a rewrite.

### State

The Hub owns a **SQLite database** for plugin registrations, per-plugin config,
install/pin records and job history. **RomM's MariaDB is never touched.** This
is what keeps RomM upgrades from breaking the Hub and vice versa.

### Where things live

Per the estate convention: source is small and lives in git; anything that
grows lives on the estate or the USB4 array, never on `C:`.

| Thing | Location |
|---|---|
| Hub source, this doc | git repo (small — Python + Markdown) |
| Plugin git clones | LXC 104 `/opt/romm-hub/plugins/` |
| In-flight downloads | LXC 104 `/opt/romm-hub/var/downloads/` |
| Imported ROMs | `/mnt/usb1/roms` (RomM's existing library) |
| Harvested cores | `/opt/romm-stream/cores` |

`.gitignore` enforces this.

### Operational risk: watchtower

LXC 104 runs **watchtower**, which auto-updates containers — including RomM
(currently `rommapp/romm:4.9.2`). A sidecar depends on RomM's API shape, so an
unattended major-version jump can break the Hub with no human in the loop.

Two mitigations, both cheap, and they compose:

1. Pin RomM in watchtower (or exclude it via
   `com.centurylinklabs.watchtower.enable=false`) so upgrades are deliberate.
2. The RomM adapter records the API version it negotiated at startup and
   **fails loudly with a clear message** on an unexpected shape, rather than
   failing obscurely mid-import.

This risk is an argument *for* the sidecar, not against it: a fork would have
the same exposure plus a permanent merge burden.

---

## The plugin contract (RPP v1)

### A plugin is a git repository

Install is `git clone` **pinned to a tag or commit**. Update is an explicit
`git fetch` + re-pin — never automatic — and the **manifest diff is shown before
the update is accepted**, so a plugin cannot silently widen its own permissions.

### `manifest.toml`

```toml
[plugin]
slug        = "archive-org"
name        = "Archive.org"
version     = "1.0.0"
rpp_version = "1"
license     = "MIT"

[capabilities]                              # declare only what you implement
search   = "archive_org.search:Search"
importer = "archive_org.importer:Importer"
metadata = "archive_org.metadata:Metadata"
stream   = "archive_org.stream:Stream"
cores    = "archive_org.cores:Cores"

[permissions]
network  = ["archive.org", "*.archive.org"]
romm_api = ["roms:create", "platforms:read", "collections:write"]

[config]                                    # user-editable schema
collections = { type = "list[str]", default = ["softwarelibrary"] }
api_key     = { type = "secret" }           # never logged, never in git
```

### Capabilities

| Capability | Method | Returns |
|---|---|---|
| `search` | `search(query, platform, limit)` | `SearchResult[]` |
| `importer` | `plan(result)` | `FetchPlan` — URLs, target platform, filenames |
| `metadata` | `enrich(rom_ref)` | `MetadataPatch` |
| `stream` | `resolve(result)` | `StreamTarget` |
| `cores` | `list()` / `plan(core)` | `CoreArtifact[]` / `FetchPlan` |

**Reserved, unimplemented in v1:** `peer`, `netplay`. Reserved so that
sub-projects C and D cannot collide with a v1 name later.

A minimal plugin implements `search` + `importer` only — the ~50-line
community contribution that makes this worth building. Archive.org implements
all five.

### `secret` config type

Config fields typed `secret` are stored encrypted in the Hub DB, redacted from
logs and API responses, and passed to the plugin subprocess only at call time.
Required by sub-project C (per-peer credentials); specified in v1 so peer
support does not force a contract revision.

---

## Security: the broker model

Plugins are arbitrary code from the internet. The Hub holds a RomM admin token
and write access to the ROM library. Declared permissions are worth nothing if
they are honour-system, so the design goal is that the runtime enforces them
rather than the plugin's good behaviour:

> **Design goal: a plugin subprocess has no RomM token, no library mount, and
> no direct network access.**

Host and plugin speak **JSON-RPC over stdin/stdout**. A plugin does not fetch;
it calls `ctx.http.get(url)`, which is an RPC back to the host. The host checks
the URL against the manifest `network` allowlist and only then performs the
request. A plugin returns a *description* of privileged work ("import this URL
as platform `psx`"); the host validates and executes it.

### What is enforced today, and what is not

Phase 1 delivered the broker. Phase 1.5 confined the plugin subprocess itself.
Two of the three lines below are now closed; the third is not, and the split
matters enough to state exactly.

**Enforced: the brokered path.** On the brokered path the allowlist is real,
not advisory. `netpolicy.url_allowed` sits in front of the only code that opens
a socket (`broker/fetcher.py`), `check_url` is unavoidable en route to it — one
call site, the same variable, no TOCTOU gap — and the matcher survived a
44-case adversarial corpus (suffix confusion, userinfo, IDN and UTS-46 dot
mapping, percent-encoding, IP literals, embedded CR/LF) with no exploitable
result. Default-deny holds: a manifest with no `[permissions]` table can reach
nothing. Redirects are disabled and response headers are not exposed, so a
plugin cannot launder a `Location` into a second request.

**Enforced: network egress.** New in Phase 1.5, and it is what makes a
manifest's `network` allowlist a containment boundary rather than a declaration
of intent. The plugin subprocess installs a **self-imposed seccomp filter on
itself, before any plugin code is imported** — `PR_SET_NO_NEW_PRIVS` first,
then a filter returning `EPERM` for `socket`, `socketcall`, `connect`,
`sendto`, and `sendmsg`. Restricting *yourself* needs no privilege, which is
why this works where a namespace sandbox does not. A plugin that ignores
`ctx.http` and reaches for `import socket` gets a `PermissionError`, not a
connection. Measured on the deployment target inside **default Docker** — no
`--security-opt`, no added capabilities:

    NNP_OK → FILTER_LOADED → BLOCKED: PermissionError

**Enforced: useful process spawn.** `execve` and `execveat` are denied, so a
plugin cannot shell out to something that would run unconfined. `clone` and
`fork` are deliberately **not** blocked: CPython uses `clone` for threads, so
denying it breaks the interpreter rather than the attacker. That costs nothing
here, because a forked child **inherits the seccomp filter** and is confined
exactly as its parent is. There is no escape by forking.

**Not enforced: arbitrary file read.** seccomp **cannot filter on a path.** A
filter matches on the syscall number and register values only; it cannot
dereference a pointer argument, so the filename handed to `openat` is invisible
to it. Confining reads requires a **mount namespace**, which this deployment
cannot create (see below). A plugin can therefore still read any file the Hub
process can read — including the Hub's own config and database.

> **Consequence, scoped to what is left: only install plugins you trust.** A
> plugin's declared `network` allowlist is now a real boundary: an untrusted
> plugin cannot reach an undeclared host and cannot exec its way out. It still
> runs with the Hub's own file-read reach. Reading a manifest tells you where
> an honest plugin will go on the network; it tells you nothing about which of
> your files a dishonest one will open.

**Why not bubblewrap or nsjail.** They were the preferred candidate, and they
were measured rather than assumed. Inside default Docker:

    $ docker run --rm debian unshare --user --net echo ok
    unshare: unshare failed: Operation not permitted

Docker's own default seccomp profile refuses the `unshare` that a namespace
sandbox is built on, so `bwrap --unshare-net --ro-bind` cannot start at all
without `--security-opt seccomp=unconfined` or `--privileged` — which would
open a larger hole than the one being closed. Recorded here so the option is
not re-litigated: it is not a matter of preference, it is unavailable on this
target. A self-imposed seccomp filter needs no privilege and is what remains,
at the price of the filesystem line above.

**On a host that cannot install the filter.** `sandbox.probe()` reports
availability; the filter is Linux-only and additionally needs `pyseccomp`.
Where it is unavailable — Windows and macOS development hosts, most obviously —
the Hub **fails closed**: `PluginProcess` raises `SandboxRefused`, the plugin
does not run, and the message names the override. Setting
`ROMM_HUB_ALLOW_UNSANDBOXED=1` lifts the refusal and means exactly what it
says: **no confinement at all**. With it set, a hostile plugin can open its own
sockets to undeclared hosts, spawn processes, and read any file the Hub can. It
is a development convenience, never a deployment setting.

Three consequences, all load-bearing:

1. **The permission is real** on the network. A plugin declaring `archive.org`
   genuinely cannot reach anywhere else, cooperative or not, so the worst case
   for a hostile plugin's *traffic* is bad data rather than exfiltration to a
   host of its choosing. What it can still do is read local files.
2. **Plugins are trivially testable.** All *intended* traffic crosses one
   chokepoint, so record-once/replay fixtures come free and the conformance
   suite runs offline. True today.
3. **Rate-limiting and caching live in one place** instead of being
   re-implemented — or forgotten — by every plugin author. True today for
   everything that goes through the broker.

Plus per-call timeouts: one wedged plugin cannot hang a search across the
others. Memory and output-size caps are specified alongside them but are not
yet complete — see the Phase 1 review findings.

### Filesystem confinement is a blocking prerequisite for Phase 2

Phase 1.5 closed the network and exec lines. The file-read line is still open,
and **Phase 2 introduces a RomM admin token**, which the Hub stores. A plugin
that can read the Hub's config or database can read that token — and the
seccomp filter does not make that harmless, because a token only has to *leave*
once to matter. So the Phase 1 sequencing stands, with a narrower and more
accurate reason than before: what blocks Phase 2 is the filesystem, not the
network.

Closing it needs a **mount namespace** — `--ro-bind` the plugin directory and
nothing else — which is exactly what the container denies today (above). The
realistic routes:

- **Run the plugin host in a container that permits `unshare`**, and let
  bubblewrap hold the filesystem while seccomp keeps holding the network. A
  deployment change, not a plugin-contract change.
- **Keep the RomM token where a plugin cannot read it** — a separate process or
  uid holds the credential and performs actions on request, so the plugin host
  never has the secret in a readable file. Removes the reason the file-read
  hole is fatal rather than closing the hole.

Windows and macOS have no equivalent of the seccomp path and fall back to the
container; until then they refuse to run plugins unless
`ROMM_HUB_ALLOW_UNSANDBOXED=1` is set.

When filesystem confinement lands, the file-read caveat in this section, in
`README.md`, and in the `plugin install` output can be dropped. The network and
exec caveats have already been dropped — they were retracted on evidence, so do
not reintroduce them without evidence.

### The cost, stated honestly

The plugin API offers no way to open a socket, so a plugin written against it
cannot use `requests`, `httpx`, or Archive.org's own `internetarchive` SDK.
This is the price of the permission being enforceable rather than advisory, and
it is a genuine tax on the "50-line plugin" premise. (Since Phase 1.5 the
seccomp filter stops it too, so an author who reaches for `httpx` anyway gets a
`PermissionError` rather than traffic the allowlist never saw — see above.)

Mitigation: ship a **`requests`-shaped adapter** over `ctx.http`, so the idiom
plugin authors already know (`ctx.http.get(url).json()`) works unchanged. The
constraint stays; the unfamiliarity does not.

### Rejected alternatives

- **SDK model** (scoped short-lived token + client library). Nicer to write
  against, but enforcement moves into token issuance, and RomM's token scoping
  cannot express "may upload to this one platform" — we would be building that
  ourselves, which is strictly more work for strictly weaker guarantees.
- **Container per plugin.** Strongest isolation, but writing a plugin would mean
  building an image. That kills the 50-line contribution this exists to enable.
  *Note: sub-project C may adopt this for a long-running federation peer without
  disturbing the plugin API.*

---

## Data flow: search → import

1. User searches in the Hub UI.
2. Dispatcher fans out to all enabled `search` plugins **in parallel**,
   each in its own subprocess.
3. Results normalised, merged, and **deduped against the existing RomM library
   by file hash** (RomM stores hashes; matching on name + size is both slower
   and wrong at the margins), so re-imports are visible before they happen.
4. User selects an item; dispatcher calls `importer.plan(result)`.
5. Host validates the returned `FetchPlan` against the plugin's permissions.
6. **Host** downloads (resumable, via range requests) to `var/downloads/`.
7. **Host** uploads: `upload/start` → `PUT` chunks → `complete`.
8. **Host** ensures the target platform exists and adds the ROM to an
   "Archive.org" collection.

Steps 5–8 are entirely host-side. The plugin's involvement ends at step 4.

---

## The Archive.org plugin

### Verified API surface

Both endpoints were tested against live Archive.org during design.

`GET https://archive.org/advancedsearch.php?q=collection:<c>&fl[]=identifier&fl[]=title&output=json`
→ `response.numFound` = **8,903** for `softwarelibrary_msdos_games` alone.

`GET https://archive.org/metadata/<identifier>` →

```
metadata.emulator     = "dosbox"          ← which emulator/core
metadata.emulator_ext = "zip"             ← which of the 14 files is the payload
metadata.collection   = [..., "stream_only", ...]
files[].format        = "ZIP" | "JPEG" | "Emulator Screenshot" | ...
```

### `stream_only` drives routing — not a hardcoded allowlist

This is the most important finding of the design pass. Archive.org **itself**
marks which items are in-browser-stream-only versus genuinely downloadable, via
membership in the `stream_only` collection.

So the plugin routes on IA's own signal rather than on a list we maintain:

- `stream_only` present → offer the **stream** capability, do not attempt import
- `stream_only` absent → offer **import**

This is more accurate than any allowlist we would hardcode, it stays correct as
IA reclassifies items, and it makes the "freely-accessible by default" posture
self-maintaining. A config toggle can widen scope, but the default needs no
curation.

### Capability implementations

| Capability | Implementation |
|---|---|
| `search` | `advancedsearch.php`, scoped to configured collections |
| `importer` | `emulator_ext` selects the payload from the item's file list; refuses `stream_only` items |
| `metadata` | `00_coverscreenshot.jpg` → cover; `files[].format` makes artwork extraction deterministic |
| `stream` | hands `stream_only` items to the existing `romm-stream` service on 104 |
| `cores` | `metadata.emulator` names the core to harvest into `/opt/romm-stream/cores` |

### Platform mapping

`metadata.emulator` (`dosbox`, `vice`, `mame`, …) maps to RomM platform slugs via
a table shipped in the plugin repo. Unmapped emulators surface as "needs mapping"
in the UI rather than importing to a wrong platform — silent misfiling is worse
than a visible gap.

---

## UI

The Hub serves its own web UI at `hub.moveweight.com`. **RomM's UI is not
modified and no nav entry is injected into it.**

An earlier draft specified injecting a `<script>` into RomM's `index.html` via
Traefik. That was investigated and rejected on evidence:

- Traefik has **no native response-body rewriting**. LXC 107 runs 3.6.13 with
  no plugins configured and no plugin storage directory.
- Enabling it means `experimental.plugins`, a third-party body-rewrite plugin,
  and a Traefik restart — which interrupts **every service on the estate** and
  adds a boot-time remote-code fetch to the reverse proxy fronting all of it.
- RomM has no custom-head/CSS hook either: its container exposes no
  `CUSTOM_*`/`HEAD`/`CSS` environment variable.

Estate-wide proxy risk for a nav shortcut is a bad trade. The Hub is reached at
its own hostname.

---

## Error handling

| Failure | Behaviour |
|---|---|
| Plugin crashes | stderr captured, plugin marked unhealthy, **other plugins unaffected**; search returns partial results with per-plugin status |
| Plugin hangs | per-call timeout, subprocess killed |
| Plugin floods output | output-size cap |
| Download interrupted | resumable via HTTP range requests |
| Upload interrupted | retry; `POST /api/roms/upload/{id}/cancel` on abort |
| **Hub restarts mid-import** | **persisted job queue** — a 4 GB in-flight import survives |
| Plugin requests a disallowed host | broker refuses, logged against the plugin |

A partial search that clearly reports "3 of 4 sources responded" is correct
behaviour. Silently returning 3 sources' results as if complete is not.

---

## Testing

- **RPP conformance suite** — every plugin author runs the same harness.
- **Offline fixtures** — because all plugin HTTP crosses the broker, record
  once and replay; no network in unit tests.
- **Integration** — ephemeral RomM container, one real import of a small
  public-domain item, asserted end-to-end through the upload API.
- **Permission tests** — assert a plugin declaring `archive.org` is actually
  refused when it requests anything else. The security model is a claim; it
  needs a test that fails if it regresses.

---

## Upstream contribution (deferred)

The PR is **not** "merge the Hub." It is a small, self-contained change making
RomM's metadata-provider list extensible from config, so an external provider
can register itself. Everything else stays in the sidecar.

RomM's `CONTRIBUTING.md` requires opening an issue and discussing on Discord
**before** a PR, and requires disclosing AI assistance. Both apply.

---

## Phasing

| Phase | Delivers | Proves | Blocked on |
|---|---|---|---|
| 1 | Hub core + RPP v1 + broker + `search` + CLI | the contract is real | — |
| 2 | `importer` + RomM adapter + job queue | it is actually useful | **filesystem confinement** |
| 3 | Web UI + Traefik nav injection | it is pleasant | — |
| 4 | `metadata`, `stream`, `cores` | Archive.org plugin complete | — |
| 5 | sub-projects C and D | *separate design pass* | — |

Phase 1 ends with a plugin that can search Archive.org from a CLI. That is the
smallest thing that validates the riskiest assumption — the plugin contract —
before any UI work is spent on it.

**Phase 2 is blocked on filesystem confinement.** Phase 1.5 closed the plugin
subprocess's network egress and process spawn with a self-imposed seccomp
filter, but seccomp cannot filter on a path, so a plugin can still read any
file the Hub can (see [What is enforced today, and what is
not](#what-is-enforced-today-and-what-is-not)). Phase 2 is where the Hub first
holds a RomM admin token, and a token sitting in a file a plugin can read turns
a hostile plugin from a search-query leak into a full library compromise.
Closing that needs a mount namespace, which the current container denies; it is
a prerequisite, not a parallel workstream, and the file-read caveat above comes
out when it lands.

---

## Open questions

1. **Platform-mapping table ownership** — ships in the Archive.org plugin repo,
   or in the Hub as shared infrastructure? Leaning plugin-local for v1, since a
   second plugin has not yet shown what should be shared.
2. **Multi-file items** — some IA items contain several distinct games. v1
   imports the `emulator_ext` payload only; multi-game items are a v2 question.
3. **Hub authentication** — reuse Authentik (as with other estate services), or
   proxy RomM's own session? Authentik is the estate default and the likely
   answer, but it is not yet decided.
