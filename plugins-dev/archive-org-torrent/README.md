# Archive.org torrents plugin for ROM Hub

Implements one RPP v1 capability:

| Capability | Endpoint | Does |
|---|---|---|
| `torrent` | `metadata/<identifier>` | resolves an item to the `.torrent` the Archive publishes and seeds for it |

```
rom-hub torrent show    archive-org-torrent rubik_202308
rom-hub torrent handoff archive-org-torrent rubik_202308
rom-hub torrent fetch   archive-org-torrent rubik_202308
```

Identifiers come from `rom-hub search archive-org` — this plugin does not
implement `search`, because the one next to it already does and a second
search over the same collections would be a second answer to one question.

## The coverage

The Internet Archive publishes a BitTorrent file for very nearly every item
it holds, and seeds it itself. Measured against the live service on
2026-08-01 by asking `advancedsearch.php` for `format:"Archive BitTorrent"`
inside each collection:

| collection | items | with a torrent | |
|---|---:|---:|---:|
| `softwarelibrary` | 250,398 | **231,663** | 92.5% |
| `consolelivingroom` | 24,746 | 21,956 | 88.7% |
| &nbsp;&nbsp;of which downloadable | 17,930 | **17,908** | **99.9%** |
| &nbsp;&nbsp;of which `stream_only` | 6,816 | 4,048 | 59.4% |
| `softwarelibrary_msdos_games` | 8,899 | 5,922 | 66.5% |

The line that matters is the third: of the Console Living Room items an
operator can actually have, **all but 22 publish a torrent**.

## What it returns, and what the host does with it

The plugin makes exactly **one** request, to `/metadata/<identifier>`, and
decides everything from the reply. It returns a *description* — the
torrent's URL, the `btih` the Archive publishes beside it, and which files
inside are the payload. It never fetches the torrent and could not: a
plugin's only network path is `ctx.http`, which is seccomp-confined and
caps a response at 4 MiB of *text*.

The host then fetches it, re-checking the allowlist on every redirect hop
(a `/download/` URL 302s to whichever node holds the item — a live example
is `dn721909.ca.archive.org`, which is why the manifest declares
`*.archive.org`), computes the info-hash from the bytes that arrived, and
refuses if it disagrees with the `btih` this plugin claimed.

## The three outcomes, all of them ordinary

| what | live example | what happens |
|---|---|---|
| a torrent exists | `rubik_202308` | resolves |
| the item publishes none | `msdos_Oregon_Trail_The_1990` | refused, naming `stream_only` / `access-restricted-item` |
| the Archive darkened it | `nointro.gb` | refused on the `is_dark` stub |

The third is worth spelling out. `nointro.gb`'s `/metadata/` answers **200**
with a stub carrying `is_dark: true`, no `metadata` and no `files`; its
`/download/.../nointro.gb_archive.torrent` answers **403**. The plugin
refuses on the stub, so the host never makes the request that would fail —
and the message says "darkened", not "something broke".

## Why this is separate from the `archive-org` plugin

Not because the code could not live there. It reads the same endpoint, and
its payload selection is deliberately the same idea as that plugin's
importer: key off `metadata.emulator_ext`, the Archive's own statement of
which extension holds the game.

Because what a reader is being asked to trust is different. `archive-org`
downloads a file over https from one origin. This one can produce something
you hand to a **torrent client**, which then announces to trackers and
connects to peers — a different network posture, with different hosts in
it, and one you should be able to decline by simply not installing this
plugin. Folding it in would mean everybody who wanted a ROM download also
got a manifest declaring `bt1.archive.org`.

## Two traps, recorded

**`ia_make_torrent` flattens subdirectories.**
`pac-man-championship-edition-1` keeps its files in `NES/`, `PSP/`,
`Android/` and `iOS/` — `/metadata/` says so — and every one of them is a
single-component path inside the torrent. Selecting files by their metadata
path names entries the torrent does not contain, and the host refuses every
one of them. So this plugin selects by **basename**, and drops a basename
that two subdirectories share rather than guessing which was meant.

**The trackers are `http://`, not `https://`.** These torrents announce to
`http://bt1.archive.org:6969/announce`, and `netpolicy.check_url` permits
https only — correctly, since it guards URLs the host itself fetches. A
tracker is not fetched by anything in the Hub; it is announced to, by
somebody else's client. So the host gates trackers by **hostname** against
the same allowlist instead, which is why `*.archive.org` in the manifest is
what makes them permitted. Widening `check_url` to make them pass would
have weakened the gate for the five capabilities that depend on it.

## Configuration

| key | type | default | meaning |
|---|---|---|---|
| `payload_only` | `bool` | `true` | name the payload file(s) as wanted. `false` names nothing, which the host reads as "the whole torrent" — the right answer for a handoff, since the client fetches every file regardless |

## Licence

MIT. This plugin is code that reads a public endpoint; it makes no claim
about the material the Archive serves. See `terms` in the catalog entry.
