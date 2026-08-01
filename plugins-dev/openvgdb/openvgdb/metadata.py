"""openvgdb `metadata`: a title, a description and a cover, from a local database.

    RomRef -> systemID -> a ROMs row (hash, serial, or exact filename)
           -> the best RELEASES row -> releaseTitleName + a composed
              summary + releaseCoverFront

OpenVGDB is the only source in this directory that carries a **curated
title** rather than a dump name. Its `RELEASES.releaseTitleName` for the
Game Boy dump `Tetris (World) (Rev A).gb` is `Tetris`, and that is the
distinction `libretro-thumbnails` refused to blur when it declined to
write a No-Intro filename into a library as a game name. A title is not a
filename, and this plugin only ever writes the title.

## What else is in that row

For a long time: a title and a cover, out of a `RELEASES` row carrying
eighteen columns. The other sixteen were selected, read, and dropped on
the floor.

Five of them are worth a library's while -- `releaseDescription`,
`releaseDeveloper`, `releasePublisher`, `releaseGenre` and `releaseDate`
-- and the description in particular is a real paragraph, not a tag.
OpenVGDB's Game Boy Tetris row opens "The Soviet game sensation is now on
your Game Boy!" and runs 380 characters.

They now go into `summary`, which is the only field on RomM's update
endpoint that will hold any of them. **That is a real limit and not a
choice made here.** RomM keeps genres, companies, `first_release_date`
and `player_count` in a `metadatum` sub-object that its own configured
providers populate; `PUT /api/roms/{id}` has no form field that reaches
it, and a part named `genres` is accepted with a 200 and discarded. So a
genre written by this plugin is a genre an operator can *read* on the rom
page, not one they can filter a library by. See README.md, "What cannot
reach RomM".

## Where the database comes from

It is a **declared data asset**, and the host fetches it. The plugin
still cannot -- `ctx.http` caps a response at 4 MiB, carries `text`
decoded with `errors="replace"` rather than bytes, and follows no
redirect, so a 9,118,645-byte zip behind a 302 was unreachable four
different ways, and a per-command subprocess had nowhere to cache it
anyway. That is unchanged. What changed is that the plugin no longer has
to reach it: `manifest.toml` declares the URL, the size and the sha256 of
the unpacked `openvgdb.sqlite`, and the Hub downloads it, re-validates
the redirect hop against this plugin's own allowlist, unpacks the one
declared member, verifies the digest, caches it, and hands this plugin a
path. `ctx.data_asset("openvgdb.sqlite")` is that path.

Everything about the arrangement is deliberate:

* **The plugin gets a path, not bytes.** 40 MiB will not cross an 8 MiB
  JSON frame, and SQLite could not mmap it if it did.
* **The digest is not optional.** A 9 MB blob off the network feeding a
  library's names and covers is a supply chain; the host refuses on
  mismatch and re-verifies the cached copy every run.
* **It is announced.** `rom-hub plugin install` prints the size and the
  origin, and the fetch itself says so on stderr before the request goes
  out. Nobody discovers 9 MB of traffic afterwards.

`db_path` still works and is still honoured first, for an operator who
already has the file, keeps it on a NAS, or shares one copy with OpenEmu.
It is simply no longer *required*.

There is still nothing to query instead: OpenVGDB publishes no API. Its
repository contains a `.gitignore` and a 28-byte `README.md`; the whole
project is one release asset, last published 2021-11-11.

## What the network permission is for

Two different things, and the manifest says which is which. The database
(`github.com`, plus `release-assets.githubusercontent.com` because the
release asset 302s there and the Hub re-checks each hop instead of
following it blindly), and the covers.

A cover is a `releaseCoverFront` URL on a third party, and the **host**
fetches it after checking it against this plugin's allowlist. The plugin
probes the candidate first (a plain GET, status only) so that a dead or
blocked cover becomes "no artwork" instead of a failed enrich -- the name
is the valuable half and should not be lost to a 403 on an image.

`art.gametdb.com` is never probed and never proposed, even though 1,789
GameCube and Wii covers point there, because it serves a `robots.txt` of
`User-agent: *` / `Disallow: *.*`. Those roms get a title and no cover.

And the probe earns its keep on the largest host too: measured with the
Hub's own user agent on 2026-07-29, `gamefaqs.gamespot.com` answers
**403** (Cloudflare, to a non-browser client) where
`raw.githubusercontent.com` answers 200. Robots permits the fetch; the
site declines to serve it. Without the probe, every non-arcade enrich
would resolve a correct title and then throw it away on an image the host
was never going to get.
"""

from urllib.parse import urlsplit

from rom_hub_sdk import MAX_SUMMARY_CHARS, MetadataPatch, MetadataProvider, RomRef

from .database import (
    DatabaseUnavailable,
    by_filename,
    by_hash,
    by_serial,
    open_database,
    rank_release,
)
from .platforms import NeedsMapping, system_for  # noqa: F401  (re-exported)

# Hex length -> the ROMs column that holds it. OpenVGDB carries no
# SHA-256, so a 64-character digest is simply not a key here.
HASH_BY_LENGTH: dict[int, str] = {8: "crc", 32: "md5", 40: "sha1"}

# Strongest first.
HASH_ORDER: tuple[str, ...] = ("sha1", "md5", "crc")

# Cover hosts this plugin will name. A *subset* of the manifest's
# allowlist, which also carries the two hosts the database download needs
# -- a cover must never be proposed on those, so this set stays separate
# from `manifest.network` rather than being derived from it. The reason
# `art.gametdb.com` is in neither is its robots.txt.
COVER_HOSTS = frozenset({"raw.githubusercontent.com", "gamefaqs.gamespot.com"})

# The `[[data_assets]]` entry the host resolves for us. Matching the
# manifest's `name` by construction: `test_openvgdb` fails if they drift.
DATA_ASSET = "openvgdb.sqlite"

_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})

# Region tags a No-Intro-style filename can carry, in OpenVGDB's spelling.
_REGION_TAGS = ("World", "USA", "Europe", "Japan")

_HEX = frozenset("0123456789abcdefABCDEF")


class NoMatch(Exception):
    """OpenVGDB has no row for this rom, and the message says what was tried."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        system = system_for(rom.platform)
        connection = open_database(self._database_path())
        try:
            matches, how = self._find(connection, system, rom)
            if not matches:
                raise NoMatch(
                    f"OpenVGDB has no entry for rom {rom.rom_id} "
                    f"({rom.filename or rom.name!r}) under system {system}. "
                    f"Tried: {how}. OpenVGDB was last published in 2021, so a "
                    f"newer dump may simply not be in it; pass a hash or a "
                    f"serial with --source-id if the filename differs."
                )
            if len(matches) > 1:
                raise NoMatch(
                    f"{len(matches)} OpenVGDB roms match {how} for rom "
                    f"{rom.rom_id}: {[m.file_name for m in matches]}. Nothing "
                    f"was written -- pass the dump's SHA-1 or MD5 with "
                    f"--source-id to say which one you mean."
                )

            release = self._best_release(matches[0], rom)
            if release is None:
                raise NoMatch(
                    f"OpenVGDB knows rom {rom.rom_id} as "
                    f"{matches[0].file_name!r} but carries no release for it, "
                    f"so there is no title and no cover to propose"
                )
            return self._patch(release, rom)
        finally:
            connection.close()

    def _database_path(self) -> str:
        """Which `openvgdb.sqlite` to read.

        `db_path` wins when it is set. It is an override, not a fallback,
        and that direction is the only one that behaves: an operator who
        pinned a specific copy -- an older release, a shared NAS file, one
        they audited themselves -- has said something more specific than
        the manifest's default, and silently preferring the Hub's cache
        would quietly ignore it.

        Otherwise the host's verified data asset, whose bytes match the
        sha256 in this plugin's own manifest. Nothing here checks that: by
        the time a path arrives in `ctx.data_assets` the host has already
        hashed the file, and a second opinion computed inside an untrusted
        subprocess would not be worth the 40 MiB it read.
        """
        override = str(self.ctx.config.get("db_path") or "").strip()
        if override:
            return override
        return self.ctx.data_assets.get(DATA_ASSET, "")

    # -- finding the rom -------------------------------------------------

    def _find(self, connection, system: int, rom: RomRef):
        """`(matches, how)` -- how being what to say if there are none."""
        source_id = (rom.extra.get("source_id") or "").strip()
        if source_id:
            kind = HASH_BY_LENGTH.get(len(source_id))
            if kind and set(source_id) <= _HEX:
                return by_hash(connection, system, kind, source_id), (
                    f"{kind} {source_id}"
                )
            return by_serial(connection, system, source_id), f"serial {source_id!r}"

        for kind in HASH_ORDER:
            digest = (rom.extra.get(kind) or "").strip()
            if digest and set(digest) <= _HEX:
                found = by_hash(connection, system, kind, digest)
                if found:
                    return found, f"{kind} {digest}"

        tried = []
        for label in (rom.filename, rom.name):
            label = (label or "").strip()
            if not label or label in tried:
                continue
            tried.append(label)
            found = by_filename(connection, system, label)
            if found:
                return found, f"filename {label!r}"
        return [], f"filename {tried!r}" if tried else "nothing identifying"

    def _best_release(self, match, rom: RomRef):
        preferred = self._preferred_region(rom)
        releases = [r for r in match.releases if r.get("releaseTitleName")]
        if not releases:
            return None
        return min(releases, key=lambda r: rank_release(r, preferred))

    def _preferred_region(self, rom: RomRef) -> str | None:
        configured = str(self.ctx.config.get("region") or "").strip()
        if configured:
            return configured
        for label in (rom.filename, rom.name):
            for region in _REGION_TAGS:
                if f"({region}" in (label or ""):
                    return region
        return None

    # -- turning a release into a patch ----------------------------------

    def _patch(self, release: dict, rom: RomRef) -> MetadataPatch:
        patch: dict = {}

        if bool(self.ctx.config.get("set_name", True)):
            title = (release.get("releaseTitleName") or "").strip()
            if title:
                patch["name"] = title

        if bool(self.ctx.config.get("summary", True)):
            summary = _summary(release)
            if summary:
                patch["summary"] = summary

        if bool(self.ctx.config.get("artwork", True)):
            cover = self._cover(release)
            if cover is not None:
                url, extension = cover
                patch["artwork_url"] = url
                patch["artwork_filename"] = f"cover.{extension}"

        # No provider ids. OpenVGDB carries no IGDB, MobyGames or
        # ScreenScraper identifier -- its own `romID` and `releaseID` are
        # row numbers in a file the operator downloaded, meaningful to
        # nothing else, and RomM has no field that means "a row in
        # somebody's local SQLite". An id written there would look like a
        # cross-reference and be a coincidence.
        #
        # This is not a gap the host's provider-id gate can close either.
        # That gate decides whether an id a plugin *has* is safe to write;
        # it cannot conjure one, and hasheous is the plugin whose whole
        # purpose is to map a dump to other providers' ids. Running the
        # two together is how a rom gets both a curated title and an
        # igdb_id.
        return MetadataPatch(**patch)

    def _cover(self, release: dict) -> tuple[str, str] | None:
        """The best cover this release offers that the host may fetch.

        Front first, back second. Each candidate is probed, so a cover the
        host would be refused is reported here as "no artwork" instead of
        failing the whole enrich after the name has already been resolved.
        """
        for field in ("releaseCoverFront", "releaseCoverBack"):
            url = (release.get(field) or "").strip()
            if not url:
                continue
            extension = _image_extension(url)
            if extension is None:
                continue
            if urlsplit(url).hostname not in COVER_HOSTS:
                # `art.gametdb.com` lands here, and so would any host the
                # manifest does not declare. Skipped rather than proposed:
                # the host would refuse it, and that refusal would fail an
                # enrich whose name was already good.
                continue
            if self._exists(url):
                return url, extension
        return None

    def _exists(self, url: str) -> bool:
        """True if the cover host serves this URL to the Hub right now.

        Everything that is not a 200 is `False`, including a redirect:
        `ctx.http` does not follow one and exposes no `Location`, so a
        moved cover is a cover this plugin cannot name. A transport error
        is `False` too -- artwork is the optional half of this patch and
        must not take the title down with it.
        """
        try:
            return self.ctx.http.get(url).status_code == 200
        except RuntimeError:
            return False


def _image_extension(url: str) -> str | None:
    path = urlsplit(url).path
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return extension if extension in _IMAGE_EXTENSIONS else None


def _summary(release: dict) -> str | None:
    """The description, and the facts RomM has nowhere else to put.

    OpenVGDB's `RELEASES` row carries six things this plugin used to read
    past on its way to the title: a description, a developer, a publisher,
    a genre list, a release date and a region. Only one of the six has a
    home in RomM -- `summary` -- and it takes prose, so the other five are
    composed into a sentence after it rather than dropped.

    That composition is doing real work and it is worth being plain about
    what it is not: RomM keeps genres and companies in a `metadatum`
    sub-object populated by its own providers, and `PUT /api/roms/{id}`
    has no form field that reaches it (a part named `genres` is accepted
    with a 200 and discarded -- measured). A genre written here is a genre
    an operator can *read*, not one they can filter by. The plugin README
    says so in those words.

    Absent when the row is empty in all six columns, because an empty
    summary is not a summary and `MetadataPatch` treats absent as "leave
    RomM's alone".
    """
    description = _clean(release.get("releaseDescription"))
    facts = _facts(release)
    if not description and not facts:
        return None
    if not description:
        return facts
    if not facts:
        return _fit(description, MAX_SUMMARY_CHARS)
    # The facts line is short and always survives; the description is what
    # gets trimmed if the pair does not fit. OpenVGDB's descriptions run to
    # a few hundred characters, so this is a guard rather than a path
    # anything real takes.
    room = MAX_SUMMARY_CHARS - len(facts) - 2
    return f"{_fit(description, room)}\n\n{facts}"


def _facts(release: dict) -> str:
    """`Developed by X. Published by Y. Released Z. Genre: ... Region: ...`

    Only the parts that are actually in the row. OpenVGDB leaves
    `releasePublisher` NULL far more often than not -- it is NULL for
    every Tetris release in the database -- so a fixed template with
    "Publisher: unknown" in it would be wrong on most roms.
    """
    parts: list[str] = []

    developer = _clean(release.get("releaseDeveloper"))
    publisher = _clean(release.get("releasePublisher"))
    if developer and publisher and developer != publisher:
        parts.append(f"Developed by {developer}, published by {publisher}.")
    elif developer:
        parts.append(f"Developed by {developer}.")
    elif publisher:
        parts.append(f"Published by {publisher}.")

    date = _clean(release.get("releaseDate"))
    if date:
        # Stored as human text already -- "June 1989", "December 1993" --
        # so it is quoted rather than parsed. A parser here would have to
        # invent a day for a month-precision date and would then be
        # printing a date OpenVGDB does not claim.
        parts.append(f"Released {date}.")

    genre = _clean(release.get("releaseGenre"))
    if genre:
        # Comma-separated in the database, with no spaces:
        # "Miscellaneous,Puzzle,Stacking".
        names = [item.strip() for item in genre.split(",") if item.strip()]
        if names:
            parts.append(f"Genre: {', '.join(names)}.")

    region = _clean(release.get("regionName"))
    if region:
        parts.append(f"Region: {region}.")

    return " ".join(parts)


def _clean(value) -> str:
    """A trimmed string, or "" for NULL and for anything not a string."""
    return value.strip() if isinstance(value, str) else ""


def _fit(text: str, limit: int) -> str:
    """`text`, cut at a word boundary if it is over `limit`.

    Ends with a single-character ellipsis so a truncated description is
    visibly truncated. A summary that stops mid-word with no mark looks
    like the source's own text is corrupt.
    """
    if limit <= 1:
        return text[:1]
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if len(spaced) > limit // 2 else cut).rstrip(" ,;:.") + "…"
