# Demozoo plugin for ROM Hub

Implements the RPP v1 `search` and `importer` capabilities against
[Demozoo](https://demozoo.org), the demoscene's catalogue: 386,682
productions on 2026-07-29, indexed by platform, type, author and party.

Demos, intros and cracktros for the machines a ROM library is already
full of — Commodore 64, Amiga, ZX Spectrum, Atari 8-bit and ST, Amstrad
CPC, MS-DOS — released free by their authors for distribution, because
that is what the demoscene is. It is a very large body of real retro
software that nobody has to be asked about.

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `demozoo.org/api/v1/productions/` | exact-title lookup, or a platform browse |
| `importer` | `demozoo.org/api/v1/productions/<id>/` | resolves one production to a download |
| `importer` | `files.scene.org` / `fujiology.org` | names a URL; the **Hub** fetches it |

## Install

    rom-hub plugin install ./plugins-dev/demozoo
    rom-hub search "second reality"
    rom-hub import demozoo 108

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `collection` | `str` | `"Demozoo"` | the library collection imports land in |
| `production_type` | `str` | `""` | narrow a browse to one type — `Cracktro`, `Demo`, `64K Intro` … |
| `max_requests` | `int` | `6` | requests one search may make, across platform ids and pages |

No credentials. The API is public and unauthenticated and this plugin
sends nothing but a GET.

## Search: what it can and cannot do

**`title` is an exact match, case-insensitively. It is not a substring
search.** Verified live: `title=Cracktro` and `title=CRACKTRO` both return
the same 26 productions all named exactly "Cracktro"; `title=crackt`
returns **zero**.

That is Demozoo's API, not a shortcut taken here, and the reason it is not
worked around matters. Demozoo *does* have a substring search — it is at
`/search/?q=…` and it renders HTML — and `demozoo.org/robots.txt` says:

    User-agent: *
    Disallow: /search/
    Disallow: /account/
    Disallow: /beep-boop/
    Crawl-delay: 10

So the substring search is precisely the endpoint this plugin must not
touch. Everything it uses is under `/api/v1/`, which robots.txt does not
restrict. The fixtures in `tests/fixtures/demozoo/` were captured from the
API at one request per ten seconds.

One further trap, worth knowing before extending this: **this API silently
ignores every parameter it does not recognise.** `?search=cracktro`,
`?q=cracktro`, `?title__icontains=crackt` and `?page_size=5` all return
HTTP 200 and the complete unfiltered 386,682-row listing. A plugin that
hopefully sent `search=` would look like it was searching and would in
fact be handing back the alphabetical head of the whole database. Only
`title`, `platform`, `production_type`, `supertype` and `page` are real.

So the two useful shapes are:

    rom-hub search "second reality"              # exact title, one request
    rom-hub search "" --platform c64             # browse that machine

A platform browse is where a demoscene source earns its place, and it is
what `production_type` is for. `--platform amiga` costs three requests,
because Demozoo splits the Amiga into OCS/ECS, AGA and PPC/RTG and the
library has one `amiga`.

## Platforms

Demozoo's 93 platform names map to library platform slugs through an
exact-match table with **no fallback and no prefix rule**. A platform that
is not in it stops the import rather than guessing, because a
misfiled production is invisible afterwards and a refusal is not.

The absences come in three kinds, because "add a row" is the right answer
to only one of them:

- **needs mapping** — a real machine the library has a slug for that
  nobody has added yet. `Acorn Archimedes`, `BBC Micro`, `Commodore 128`,
  `Atari Falcon`, `Oric`, `SAM Coupé`, `Sinclair QL`, `TRS-80` and about
  twenty more. The message says to add the row.
- **not a library platform** — `Windows`, `Linux`, `macOS`, `Browser`,
  `Java`, `Flash`, `Android`, `Paper`. A Windows 64K intro is a desktop
  `.exe`; there is no shelf for it and there never will be. Saying "needs
  mapping" for those would invite somebody to invent one.
- **ambiguous** — worse than missing, because it looks answerable.
  Demozoo has one `Neo Geo` where the library keeps `neogeoaes` and
  `neogeomvs`; one `Neo Geo Pocket / Neo Geo Pocket Color (NGPC)` where it
  keeps two; one `Thomson` where it keeps `thomson-mo5` and `thomson-to`.
  All three refuse with that sentence.

`Amstrad Plus` is in the first group for a specific reason: the CPC Plus
range and the GX4000 console are one Demozoo platform and two library
ones.

## What it does and does not surface

**Demozoo indexes the demoscene, and the demoscene is not only software.**
Of its 57 production types, a `Photo` is a jpeg of a party hall, `Tracked
Music` is a `.mod`, `Performance` happened on a stage, `Papermag` was
printed on paper, and `Report` is somebody's write-up of a weekend.

**Surfaced** — everything the scene distributes as an executable or a disk
image for the machine it names: `Demo`, `Intro`, the whole sized-intro
ladder from `8b intro` to `100K Intro`, `Cracktro`, `BBStro`, `Game`,
`Musicdisk`, `Diskmag`, `Docsdisk`, `Votedisk`, `Slideshow`, `Invitation`,
`Pack`, `Chip Music Pack`.

**Not surfaced:**

- **`Video`.** This is the important one. Its supertype is `production`,
  exactly like `Demo`, so a supertype-only filter would keep it — and its
  download links are frequently a YouTube page. Importing one would file a
  link to a web page in a ROM library as though it were a game. There are
  thousands of them.
- **The whole `graphics` and `music` supertypes.** `Executable Graphics`
  and `Executable Music` genuinely *are* executables — a `.prg` that draws
  one picture, a `.prg` that plays one tune — so this is a real choice
  rather than an oversight. A ROM library is a shelf of things you play,
  and forty thousand single-image entries next to the games would make it
  useless for what it is for.
- **`Tool`** (development utilities), **`BBS Door`** (runs on the BBS
  host, not the caller's machine), **`Magazine` / `Textmag` / `Papermag` /
  `Report` / `Performance`** (publications and events — `Diskmag` is the
  one of these that ships as a bootable disk, and it *is* imported), and
  **`Code Challenge`** (sometimes an executable, sometimes a source
  listing, and the type does not say which).
- **Productions with no platform at all.** Demozoo has many; they are
  usually old records tagged `lost`.

Search applies the same filter as import, so a row you can see is a row
you can import. In the captured `title=second reality` response, seven
real productions share that name and three survive: the ZX Spectrum demo,
the C64 demo, and Future Crew's MS-DOS original.

## Where the files come from

**Demozoo hosts nothing.** Every production's `download_links` point at
somebody else's archive, and the `link_class` is the only structured
statement about which one. A sample of 48 records across seven platforms
(2026-07-29, at one request per ten seconds) produced 32 links across
twelve hosts, twenty `https` and twelve plain `http`.

Two archives are supported and every other host is refused **by name**.
That is the only shape that can work: `FetchPlan` URLs are checked against
this plugin's `network` allowlist by the Hub, so an undeclared archive
would fail as an opaque policy violation instead of a sentence.

### scene.org, and the redirect that is the whole problem

`SceneOrgFile` links point at `https://files.scene.org/view/<path>`, which
is an HTML page rather than the file. The download entry point is
`/get/<path>` — and that is where the trap is, because `/get/` picks a
mirror and answers `302`. Verified live:

| Requested | `Location` |
|---|---|
| `/get/<p>` | `http://http.us.scene.org/pub/scene.org/<p>` |
| `/get:us-http/<p>` | `http://http.us.scene.org/pub/scene.org/<p>` |
| `/get:pl-http/<p>` | `http://http.pl.scene.org/pub/scene.org/<p>` |
| `/get:de-https/<p>` | `https://mirror.netcologne.de/scene.org/<p>` |
| `/get:nl-https/<p>` | `https://archive.scene.org/pub/<p>` |

Every hop is re-checked against the allowlist, so the mirror is not an
implementation detail that can be ignored — an undeclared one breaks the
download at the moment the bytes would start arriving. Worse, the first
two land on **plain http**, and the Hub's `netpolicy` permits `https`
only, so the default `/get/` form cannot work whatever is declared.

So the plugin pins `get:nl-https`, which resolves to exactly one https
host, and declares both ends of that hop. Pinning rather than declaring
the whole mirror pool: the pool is scene.org's business and can change,
and an allowlist naming a dozen hosts to cover a choice made at random is
a far larger permission than this needs. Mirror *selection* stays
scene.org's — the plugin asks `files.scene.org` for a mirror by label; it
does not construct an `archive.scene.org` URL itself.

`fujiology.org` (`FujiologyFile`, the Atari archive) serves its files
directly over https with no redirect at all, so those links are used as
they stand.

### Archives deliberately not used

- **csdb.dk** — 5 of 32 sampled links, and the biggest gap in coverage.
  Its `robots.txt` carries `User-agent: ClaudeBot` / `Disallow: /` and the
  same for `anthropic-ai`, plus `Disallow: /release/download.php` for
  everybody. Nothing here was fetched from it and nothing here will be: a
  source that cannot be verified without breaching a crawl directive is
  not a source this plugin ships. Adding it would roughly double C64
  coverage, and it stays out.
- **ftp.amigascne.org** — 6 of 32. Its `robots.txt` is `User-agent: *` /
  `Disallow: /`, refused to everyone rather than to us in particular. Its
  links are also plain `http`, and several have no file extension at all.
- **files.zxdemo.org** — 3 of 32. Answers `403` to its own root;
  unverified, so unshipped.
- **Everything reached through `BaseUrl`** — Demozoo's "some URL" class.
  The sample found it pointing at nine different hosts including personal
  sites. There is no allowlist that covers "anywhere", and one that tried
  would be the opposite of what a `network` declaration is for.

A production whose only links are on those hosts refuses at import with
the host named and the reason given, rather than failing as a policy
violation nobody can interpret.

## Terms

**Demoscene productions are released free by their authors for
distribution.** That is not a licence granted to this plugin — each
production is its author's work under whatever terms they chose, and many
carry none at all — but free circulation is the point of the form and has
been since the 1980s: a demo exists to be copied, and scene.org's whole
purpose is copying them. Cracktros are the one class where the *original*
context was piracy; the intro itself is the group's own code and artwork,
which is why it is what got archived and the game it was attached to is
not here.

This plugin surfaces the index and points at the archives the scene runs
for itself. It refuses to fetch from hosts whose `robots.txt` says no,
including where that costs real coverage, and it uses no endpoint Demozoo
disallows.
