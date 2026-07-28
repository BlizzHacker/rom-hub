# Sub-projects C & D — Federation and Multiplayer

**Status:** design pass only. **Neither is built in this phase.**
**Date:** 2026-07-28
**Companion to:** [DESIGN.md](DESIGN.md)

---

## Why this pass exists

C and D are deferred, but deferring them blindly risks freezing an RPP v1 that
cannot express what they need — forcing a breaking contract revision later, at
exactly the point when third-party plugins exist and a break is expensive.

So the question this document answers is narrow and deliberate:

> **What do federation and multiplayer demand of the plugin contract, and must
> RPP v1 change to accommodate them?**

**Answer: one addition and two reservations.** RPP v1 is otherwise sound. Both
are already folded into [DESIGN.md](DESIGN.md).

| Change | Driven by | Status |
|---|---|---|
| `secret` config type | C — per-peer credentials | specified in v1 |
| Reserve capability name `peer` | C | reserved, unimplemented |
| Reserve capability name `netplay` | D | reserved, unimplemented |

---

## Ground truth (verified 2026-07-28)

Assumptions were checked against the running estate rather than inferred.

### RomM 4.9.2

- **`/api/sync/*` is not server-to-server.** An earlier draft of DESIGN.md
  claimed these endpoints were a federation seam. They are not. `POST
  /api/sync/negotiate` is documented as: *"Negotiate sync operations between a
  client device and the server. The client sends its current save state, and
  the server returns a list of operations (upload, download, conflict, no_op)."*
  That is **save-state sync to a handheld or client**, and gives federation
  nothing directly.
- **`/api/netplay/list` → `Get Rooms`** — a netplay room concept already exists.
- **`/api/client-tokens/{id}/pair`** + **`/pair/{code}/status`** — a real
  pairing-code flow, usable for authorising a peer.

### `romm-stream` on an LXC container — ~960 lines

Not a stub, and not the architecture assumed. It is **server-side emulation
with pixel streaming**:

```
server.py (658)  handle_start / handle_stop / handle_input / handle_mouse
                 handle_text / handle_remote / handle_rtc_signal
                 romm_autoplay  ← drives headless Chrome over CDP, logs into
                                  RomM and launches a ROM in EmulatorJS
                 start_ffmpeg_hls
webrtc.py  (93)  run_peer, _media_players
sessions.py(28)  class Allocator  ← X display-number allocation per session
saves.py   (26)   tiers.py (68)   runner_retroarch.py (91)
```

Emulation runs **on the server**. The X display is captured and shipped out by
ffmpeg/HLS or WebRTC; input is forwarded back. Both `emulatorjs/` and
`retroarch/` backends are present.

### Infrastructure gap: coturn is running but unused

`coturn` has been up on 104 for 44 hours. `webrtc.py` line 15:

```python
ICE_SERVERS = [{'urls': 'stun:stun.l.google.com:19302'}]
```

Public Google STUN, **no TURN relay, and not the local coturn.** STUN alone
discovers a reflexive address but cannot relay, so WebRTC currently fails
behind symmetric NAT or CGNAT — precisely the networks a remote friend is
likely to be on.

**This is the single highest-value small fix on the board.** Both C and D
depend on peer connectivity across hostile NAT, the relay is already deployed
and idle, and wiring it in is a config change plus credentials. It should be
done regardless of whether C or D is ever built, because it also fixes remote
streaming for the *existing* single-player use case.

---

## Sub-project C — Federation

### The main use case is already a plugin

A friend's RomM server is just another RomM API. Therefore:

> A `romm-peer` plugin declaring `network = ["friend.example.com"]` and
> implementing `search` + `importer` **is** browsing and fetching from a
> friend's library.

Everything downstream already works: normalisation, hash dedup against your own
library, platform mapping, collection grouping, the job queue, resumable
downloads. The broker confines the plugin to exactly that one host, so a peer
plugin cannot phone anywhere else.

**No new capability is required for outbound federation.** This is the strongest
validation the RPP contract received: its first genuinely unforeseen use case
fit without modification.

What it *does* require is credential storage — hence the `secret` config type.

### The inbound half is a Hub feature, not a plugin

Letting a friend browse **your** library is a served API, not a called one.
Plugins are invoked; they do not receive traffic. So the inbound side is Hub
surface area:

- an authenticated peer endpoint exposing a filtered view of your library
- per-peer scoping (which platforms/collections a given friend may see)
- pairing via RomM's existing `/api/client-tokens/pair` code flow
- revocation

Because this is Hub-side, it does not touch RPP at all. The `peer` capability
name is reserved only in case a future plugin wants to *implement* a peer
protocol variant (a non-RomM peer, say). v1 does not need it.

### What makes C genuinely hard

None of this is the difficult part. The difficult part is unchanged:

- **Identity and trust** — what a paired peer is allowed to see and do, and how
  that is revoked when a friendship ends.
- **Partial availability** — peers are laptops and home servers. Search must
  degrade to "3 of 5 peers responded" rather than hang or lie. The existing
  per-plugin partial-result design already handles the shape of this.
- **NAT traversal** — see the coturn gap above.
- **Legal exposure** — outbound federation means serving files to other people.
  This is a materially different posture from a private library, and it is a
  decision to make deliberately rather than discover.

### Recommended C phasing (when it happens)

1. Wire coturn (do this now regardless — it is independently useful).
2. `romm-peer` plugin, **outbound read-only**: search a friend's library.
3. Import from a peer. Still no inbound exposure.
4. Inbound peer endpoint, one paired friend, one explicitly shared collection.
5. Per-peer scoping and revocation UI.

Steps 2–3 need **no Hub changes beyond `secret` config**. Real value lands
early, and the risky inbound work is isolated behind steps 4–5.

---

## Sub-project D — Multiplayer

### Two incompatible models

This is the fork that must be decided before any D work, and the existing
architecture leans hard toward one of them.

| | **Shared-session co-op** | **True netplay** |
|---|---|---|
| Emulation runs | one server-side session | each player's own emulator |
| Sync model | none — one machine, inputs multiplexed | rollback or lockstep determinism |
| Fit with `romm-stream` | **native** — it already streams a session and forwards input | unrelated to the current stack |
| Uses `/api/netplay/list` rooms | as a lobby only | as designed |
| Latency profile | every player pays streaming RTT | only sync traffic |
| Player count | bounded by server CPU/GPU per session | bounded by sync tolerance |
| Build cost | **small** | **large** |

**Shared-session co-op is close to free.** `romm-stream` already starts a
session, allocates a display, streams it, and forwards input events. Two-player
local co-op on a SNES title is approximately: accept a second input channel,
tag events with a player index, and map them to the emulator's port 2. The
session, transport, and allocator all exist.

**True netplay is a different project.** It needs deterministic lockstep or
rollback, save-state exchange at join, drift detection, and per-core
correctness work. RetroArch's netplay exists and `retroarch/` is present in the
tree, which lowers but does not remove the cost.

**Recommendation:** shared-session co-op first. It is the natural extension of
what already runs, it is demonstrable in days rather than months, and it
delivers the actual want — *playing a game with a friend* — without a
determinism project. True netplay stays open afterwards; it is not foreclosed.

### RPP impact: none in v1

`stream.resolve(result) → StreamTarget` returns an **opaque target**. A target
can be a single-player HLS URL, a WebRTC offer, or a co-op room handle — the
contract does not care, because the plugin's involvement ends when it hands the
target back. Both models survive it unchanged.

The one thing D might eventually need is a plugin that **hosts** a long-running
session rather than answering a call. That is the *service model* rejected for
v1 in DESIGN.md, and the rejection noted it could be adopted for a single
capability without disturbing the plugin API. The `netplay` name is reserved
against that day.

---

## Conclusions

1. **RPP v1 is sound.** Designing C and D first validated the contract instead
   of breaking it — the cost was one config type and two reserved names.
2. **Federation's headline feature is a plugin**, not a subsystem. Browsing a
   friend's library needs no new capability.
3. **Wire coturn now.** It is deployed, idle, independently useful, and blocks
   both deferred sub-projects.
4. **Pick shared-session co-op for D**, not true netplay, unless there is a
   specific reason to want client-side emulation.
5. **The `/api/sync/*` endpoints are save-state device sync.** Do not build a
   federation plan on them.
