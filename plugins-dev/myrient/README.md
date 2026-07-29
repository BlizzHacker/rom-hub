# Myrient-style directory-index plugin for RomM Hub

Implements the RPP v1 `search` and `importer` capabilities against a plain
HTTP **directory index** — no API, just the listing a web server renders for a
directory.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `<base_url><directory>/` | reads the index once, caches it, matches file names |
| `importer` | the same index | confirms the file is still listed, then plans it |

## Read this first: Myrient is gone

**myrient.erista.me shut down on 31 March 2026.** It is not merely offline —
it answers **`200 OK` with a static shutdown notice for every path it ever
served**, including deep file URLs:

    $ curl -sI 'https://myrient.erista.me/files/No-Intro/Nintendo - Game Boy/Tetris (World) (Rev 1).zip'
    HTTP/2 200
    content-type: text/html          # 2,334 bytes of shutdown notice

A plugin that checked status codes would report "no results" forever and never
say why, and an importer that trusted them would download the notice, hash it,
upload it, and report `DONE` with an HTML page filed as a ROM.

So the plugin ships pointed at a live mirror instead, and it **checks that a
page is actually an index** rather than trusting the status code: a `200` from
which no entries can be parsed is an error that says so. There is a test for
exactly that, replaying the real shutdown page
(`tests/fixtures/myrient/myrient_shutdown.html`).

The Myrient *shape* is preserved throughout — `base_url` + directory + a
name-matched listing — and `myrient/platforms.py` still carries Myrient's own
`No-Intro/<Platform>` directory names, so pointing this at any mirror that
reproduces that layout is a config change plus one line in `manifest.toml`.
A replacement that reproduces Myrient's tree was looked for and not found:
MiNERVA Archive is live but `Disallow`s `/browse/` and `/rom/` in its
robots.txt, so it was not used.

## Install

    romm-hub plugin install ./plugins-dev/myrient
    romm-hub search "streets of rage" --platform genesis --limit 5

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `base_url` | `str` | `https://archive.org/download/` | mirror root; must be `https://` and its host must be in the manifest allowlist |
| `collections` | `list[str]` | the twelve `nointro.*` items below | directories to search, **in order** |
| `collection` | `str` | `Myrient` | RomM collection imported ROMs are grouped into |

Default `collections`: `nointro.gg`, `nointro.ms-mkiii`, `nointro.md`,
`nointro.32x`, `nointro.tg-16`, `nointro.sg`, `nointro.atari-2600`,
`nointro.atari-5200`, `nointro.atari-7800`, `nointro.ws`, `nointro.wsc`,
`nointro.gbamultiboot`.

**Repointing `base_url` at another host also needs a `manifest.toml` edit and
a reinstall.** That is deliberate. The allowlist is what the broker enforces;
if config alone could move it, config alone could widen the plugin's network
reach, and installing a plugin would stop being a decision about where it
goes.

## Being a considerate client

A No-Intro platform directory is hundreds of kilobytes of HTML listing
thousands of files, served by someone giving bandwidth away.

- **Each directory is fetched at most once per plugin process** and shared
  between `search` and `importer`, so an import that follows a search costs no
  request at all. The cache is bounded (32 directories, oldest evicted) so a
  long-lived host cannot accumulate everything it has ever seen.
- **The walk stops as soon as `limit` results exist.** A query answered out of
  the first directory never opens the second.
- **`--platform` filters before any request.** A file's platform *is* its
  directory here, so `--platform genesis` is one fetch instead of twelve.
- **No concurrency is added.** The plugin has no sockets; `ctx.http` is an RPC
  the host serves one call at a time, and nothing here tries to work around
  that.

## Parsing

One parser, not one per server. Apache's `<pre>` block, nginx's fancyindex
table, lighttpd's table and Archive.org's petabox table disagree about
everything except that an entry is an `<a href>` with a size somewhere to its
right — so entries are selected by *shape*:

- **Only same-directory relative links count.** Anything absolute, any
  `?C=N&O=A` sort link, any `#anchor`, any `../` is chrome. Filtering by shape
  rather than by a list of known chrome strings is what lets one parser handle
  four servers.
- **A duplicate href is chrome too.** Archive.org prints every file twice —
  `<a href="Game.7z">Game.7z</a> (<a href="Game.7z/">View Contents</a>)` — and
  the twin differs only by a trailing slash, so deduplicating on the
  slash-stripped href drops it without this code knowing the words "View
  Contents".
- **Names are decoded, hrefs are not.** The name is what you search; the href
  is what the server said, and the plan uses it verbatim so the plugin never
  has to guess how a mirror percent-encodes.
- **Sizes are a hint.** `35.9 KiB` (Myrient) and `70.2K` (petabox) both parse,
  both as 1024-based; anything unparseable becomes `None` rather than an
  error, because the host learns the real length from the response and a
  display number must not be able to fail a plan.
- **Metadata files are not payloads.** `*_meta.xml`, `*_files.xml`,
  `*_meta.sqlite`, `*_archive.torrent`, `*_reviews.xml` are Archive.org
  bookkeeping. They never appear in results and are refused by name if asked
  for directly.

Subdirectories are listed but never descended into. Walking a whole mirror is
not a thing a search should do to someone else's bandwidth; naming the
directory in `collections` is.

## Platform mapping

The only thing a directory index says about a ROM's platform is which
directory it is in, so `myrient/platforms.py` maps directory names to RomM
platform slugs. **Exact match, no fallback** — an unmapped directory raises
**"needs mapping"** and names itself.

A prefix rule over `nointro.*` would look free and be wrong exactly where it
matters, because the suffixes are abbreviations chosen by whoever uploaded the
set:

| Directory | Is | Not |
|---|---|---|
| `nointro.sg` | PC Engine **SuperGrafx** (`supergrafx`) | Sega Game Gear |
| `nointro.ca` | Commodore **Amiga** (`amiga`) | anything starting "ca" |
| `nointro.ms-mkiii` | Master System / Mark III (`sms`) | — |
| `nointro.md` | Mega Drive, which RomM files as `genesis` | — |

The table is checked **before any request**: a `collections` entry nobody
mapped is a configuration error, not a per-result oddity, and paying for a
fetch to discover it helps nobody. Values were checked against RomM's own
platform-slug enum.

## The importer confirms before it plans

A URL could be built from a source id by concatenation alone. It would be
wrong the first time a set is rebuilt and a file renamed — and the host would
then fetch the mirror's 404 page, hash it, upload it and report `DONE`. So the
importer reads the directory index (usually from the cache the search already
filled) and matches the entry by name. No entry, no plan.

Source ids are `<directory>/<file>`. A Myrient-layout directory contains
slashes of its own, so the split is done against the configured `collections`
rather than at the last slash — which also means a source id naming a
directory this install does not search is refused rather than guessed at.

## Legal position

**Plainly: the default `collections` are No-Intro sets of commercial console
ROMs, held on the Internet Archive. They are copyrighted works, and this
plugin does not launder that.** No-Intro sets are checksum-verified dumps of
retail cartridges; the copyright in those games belongs to their publishers,
most of whom have never licensed redistribution. Whether you may download them
depends on where you live and on whether you own the original media — in the
United States, for example, the archival exemption courts have recognised does
not extend to downloading a copy of something you do not own.

What this plugin does and does not do:

- It fetches only from hosts named in `manifest.toml`, over HTTPS, one request
  at a time, with the Hub's `romm-hub/0.1` User-Agent.
- It does not circumvent any access control, paywall, login or robots
  directive. `https://archive.org/download/` is a public, unauthenticated
  directory listing; the Internet Archive's robots.txt does not disallow it.
- It does not mirror or bulk-download: a query reads index pages, and an
  import fetches exactly one file you asked for.
- **MiNERVA Archive was considered and rejected** as a `base_url` default
  because its robots.txt `Disallow`s `/browse/` and `/rom/` — the paths a
  scripted client would need. Working around that was not on the table.

If you want this plugin pointed only at material that is unambiguously free to
redistribute, that is what `base_url` and `collections` are for; the
`homebrew` plugin in this repository is built for that case from the start.

## Network

Declared allowlist: `archive.org`, `*.archive.org`. Downloads redirect from
`archive.org` to a node like `dn721808.ca.archive.org`, and the Hub
re-validates **every redirect hop** against this list, which is why the
wildcard is there and why nothing broader is. `myrient.erista.me` is
deliberately *not* listed: an allowlist is a statement about where the plugin
actually goes, and a dead host in it would be decoration.
