"""What the host does with a torrent a plugin described.

    plugin.resolve_torrent(result) -> TorrentSource
      -> host fetches the .torrent (allowlist-gated, every redirect hop)
      -> host reads it, computes the info-hash, checks the plugin's claim
      -> then one of:
           show    -- print the manifest and stop
           handoff -- give it to the torrent client the operator runs
           fetch   -- pull one named file from the torrent's own https web
                      seed, verified against the torrent's own digest

## The Hub is not a torrent client, and this is the argument

The obvious reading of "add torrent support" is "link libtorrent". This
module does not, and the reasoning is worth stating plainly because the
conclusion is smaller than the request.

**What linking a client would cost.** `libtorrent` is the only complete
implementation; it is C++ over Boost, it wants a compiler or a platform
wheel, and what it brings into the process is a *session*: a listening
socket, a DHT node, a peer connection pool and a background thread that
outlives any one call. `rom-hub` is a CLI that runs one command and
exits, whose plugins are seccomp-confined subprocesses with no sockets at
all. A daemon-shaped dependency does not fit a command-shaped program,
and the pure-Python clients that would avoid the build are partial and
largely unmaintained.

**What it would buy, for this corpus.** Nothing that is not already
there. Archive.org publishes a `.torrent` for essentially every item and
**seeds it itself**, over HTTPS, from the same origin the rest of this
project already talks to -- the torrent says so: `url-list` (BEP 19) on a
live Archive.org torrent is

    https://archive.org/download/
    http://ia902705.us.archive.org/26/items/
    http://ia802705.us.archive.org/26/items/

The first of those is https and inside the plugin's own allowlist. So the
bytes a swarm would deliver are reachable over the transport this project
already gates, verifies and understands. A peer stack would be a second
way to fetch the same file from the same organisation.

**What is actually needed is a reader.** Archive.org's torrents carry, in
the `info` dictionary and therefore *under the info-hash*, a `sha1`,
`md5`, `crc32` and `length` for every file. That makes a `.torrent` a
per-file verified manifest, which is strictly better than the plain
`/download/` path it describes: the same bytes, plus a digest to check
them against. Reading it is `rom_hub.bencode`, about 120 lines and no
dependency.

**Where a swarm genuinely wins, the answer is handoff.** A multi-gigabyte
disc image pulled from many peers really is faster than one HTTPS
connection, and an operator who wants that already runs qBittorrent,
Transmission or Deluge. So the host hands the torrent over -- the same
shape `stream` uses, which resolves a URL and gives it to a browser
rather than building a player. `handoff_path()` writes the validated
`.torrent` into a watch directory; `magnet_for()` produces a magnet for a
client that takes one. Every client in that list has watched a directory
for a decade. That integration is smaller, has no dependency, and is
*better*, because the operator's client already has their bandwidth
limits, their VPN binding and their disk layout configured.

## "Streaming", honestly

The ask behind this capability was torrent *streaming*: sequential piece
ordering so a file is usable before it completes. Three things are true
and the first one is the important one.

**For most of this corpus, "stream" and "download" are the same thing.**
A ROM is kilobytes to a few megabytes -- `rubik.zip` is 15 KB, a NES
image 262 KB. There is no interval during which a 15 KB file is partly
useful; it arrives inside one round trip. Sequential ordering buys
literally nothing there, and saying otherwise would be selling a feature
by its name.

**Where it matters, it is a real effect.** Archive.org's software
collections do hold multi-gigabyte items -- 5.7 GB is the largest
measured. For a disc image, order of arrival is the difference between
mounting it now and mounting it in an hour.

**And for those, the HTTPS path is already sequential.** An HTTP response
body arrives in order, from byte zero, by construction. Out-of-order
arrival is something a *swarm* introduces, because it fetches rare pieces
first from many peers; "sequential mode" in a torrent client exists to
partly undo that, at the cost of the parallelism that was the point. So
for a source that seeds its own content over HTTPS, the plain fetch below
is the sequential one, and `fetch_entry` streams to disk as the bytes
arrive rather than buffering.

What this module therefore does **not** claim is a play-while-downloading
pipeline. Nothing here hands a partial file to an emulator, and there is
no piece-priority scheduler, because there are no pieces being scheduled.

## Every URL is gated, including the ones inside the torrent

The plugin's `source` is checked before the fetch, and again on every
redirect hop, by `HttpDownloader` -- the same downloader and the same
`netpolicy.check_url` an import uses.

The URLs *inside* the fetched torrent are checked too, and that is the
less obvious half. A torrent's `announce`, `announce-list` and `url-list`
are network locations somebody will contact: the web seed by this host,
the trackers by whatever client the torrent is handed to. A torrent whose
tracker list a plugin's allowlist never mentioned is a way to cause
traffic to an undeclared host with the plugin's fingerprints nowhere near
it. So `check_trackers` and `check_web_seeds` run over what came back,
against the same manifest allowlist, and `handoff` refuses on a tracker
that is not covered.

That is deliberately strict, and it is strict in a direction that will
eventually be inconvenient: a future source using public trackers
(`opentrackr`, `torrent.eu.org`) would have to declare them in its
manifest. That is the correct outcome -- `permissions.network` is
supposed to be a complete account of where a plugin causes traffic, and a
tracker is traffic.

## Filenames come from the torrent, and get the filename rules anyway

Nothing a plugin returns becomes a path here: the plugin names selectors,
the torrent names files, and the *torrent* is the untrusted document. So
every entry path is put through `types.bare_filename` before it can be a
destination and through `paths.dest_in_job_dir` before it is opened --
the same two layers a `FetchPlan` filename passes, reused rather than
reimplemented. An entry that fails either is listed as unselectable with
the reason, never sanitised into something writable.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from rom_hub.bencode import BencodeError, decode
from rom_hub.netpolicy import PolicyViolation, check_url, host_matches
from rom_hub.paths import UnsafeDestination, dest_in_job_dir
from rom_hub.types import (
    MAX_TORRENT_BYTES,
    MAX_TORRENT_ENTRIES,
    TorrentSource,
    bare_filename,
)


class TorrentError(Exception):
    """Reading, validating, handing over or fetching from a torrent failed."""


#: Where a `.torrent` is dropped for the client the operator already runs.
#: Every mainstream client -- qBittorrent, Transmission, Deluge -- has
#: watched a directory for a decade, which is why this is the integration
#: rather than a client API with credentials in it.
WATCH_DIR_ENV = "ROM_HUB_TORRENT_WATCH_DIR"

#: Schemes a BitTorrent tracker actually uses. `udp` is the common one and
#: `http` is what Archive.org's own trackers speak
#: (`http://bt1.archive.org:6969/announce`) -- neither is fetchable, which
#: is exactly why they are validated by host rather than by `check_url`.
#: See `check_trackers`.
TRACKER_SCHEMES = frozenset({"http", "https", "udp", "ws", "wss"})

#: Magnet parameters this host will accept, as an allowlist. See
#: `check_magnet` for what each one is and why everything else is refused.
MAGNET_PARAMS = frozenset({"xt", "dn", "tr", "ws", "xl", "kt", "so"})

#: How many trackers or web seeds are read out of one torrent or magnet.
#: Bounded because the host iterates them and they arrived over the wire.
MAX_ANNOUNCE_URLS = 64


# -- what a torrent says ---------------------------------------------------


@dataclass(frozen=True)
class TorrentEntry:
    """One file a torrent declares.

    `sha1`/`md5` are per-file digests. They are **not** part of the
    BitTorrent spec -- pieces are how a torrent normally proves its bytes
    -- but Archive.org's `ia_make_torrent` writes them into each file
    entry, which puts them inside the `info` dictionary and therefore
    under the info-hash. That is what makes a single-file verified fetch
    possible without touching the swarm, and `verified_by` is what stops
    the absence of one from being mistaken for a pass.

    `refusal` is set when this entry cannot be a destination on disk: a
    nested path, or a name the host's own filename rules reject. Listed
    rather than dropped, so an operator can see the file exists and read
    why it is not on offer.
    """

    path: str
    length: int
    sha1: str = ""
    md5: str = ""
    refusal: str = ""

    @property
    def selectable(self) -> bool:
        return not self.refusal

    @property
    def verified_by(self) -> str:
        """Which digest a fetch of this entry can be checked against."""
        if self.sha1:
            return "sha1"
        if self.md5:
            return "md5"
        return ""


@dataclass(frozen=True)
class Torrent:
    """A `.torrent` the host fetched, read and computed the info-hash of."""

    info_hash: str
    name: str
    piece_length: int
    piece_count: int
    entries: tuple[TorrentEntry, ...]
    trackers: tuple[str, ...]
    web_seeds: tuple[str, ...]
    comment: str = ""
    #: The bytes as received. Kept because `handoff` writes them out
    #: verbatim -- a re-encoding could not be guaranteed to carry the same
    #: info-hash, which is the one property the operator's client needs.
    raw: bytes = b""

    @property
    def total_bytes(self) -> int:
        return sum(e.length for e in self.entries)

    def entry(self, selector: str) -> TorrentEntry:
        """The entry a selector names, or a refusal that says what exists.

        Exact, case-sensitive matching. A torrent's file list is the
        source's own, and two entries differing only in case are two files
        on the filesystems that can hold both -- so a case-insensitive
        match here would sometimes pick the wrong one silently.
        """
        for candidate in self.entries:
            if candidate.path == selector:
                if not candidate.selectable:
                    raise TorrentError(
                        f"{selector!r} is in this torrent but cannot be "
                        f"fetched: {candidate.refusal}"
                    )
                return candidate
        names = [e.path for e in self.entries if e.selectable]
        shown = ", ".join(repr(n) for n in names[:10])
        more = "" if len(names) <= 10 else f" (and {len(names) - 10} more)"
        raise TorrentError(
            f"this torrent has no file {selector!r}; it offers: {shown}{more}"
        )


def parse_torrent(data: bytes) -> Torrent:
    """Read `.torrent` bytes into a `Torrent`, or refuse with a reason.

    Everything here treats the input as hostile; see `rom_hub.bencode`.
    What this adds on top of the parse is *shape*: a well-formed bencoded
    document is not necessarily a torrent, and every field below is
    checked for the type it must have rather than coerced into it.
    """
    if len(data) > MAX_TORRENT_BYTES:
        raise TorrentError(
            f"this .torrent is {len(data)} bytes, over the "
            f"{MAX_TORRENT_BYTES}-byte limit for a file whose whole job is "
            f"to be a manifest"
        )
    try:
        document = decode(data)
    except BencodeError as exc:
        raise TorrentError(f"this is not a readable .torrent file: {exc}") from exc
    if not isinstance(document, dict):
        raise TorrentError(
            f"a .torrent must be a bencoded dictionary, got "
            f"{type(document).__name__}"
        )

    info = document.get(b"info")
    if not isinstance(info, dict):
        raise TorrentError("this .torrent has no `info` dictionary")
    span = getattr(info, "span", None)
    if span is None or span.stop <= span.start:
        raise TorrentError("this .torrent's `info` dictionary could not be located")
    # By definition rather than by reconstruction. See `bencode.Span`.
    info_hash = hashlib.sha1(data[span.start : span.stop]).hexdigest()

    name = _text(info.get(b"name"), "name")
    if not name:
        raise TorrentError("this .torrent's `info` has no `name`")

    piece_length = info.get(b"piece length")
    if not isinstance(piece_length, int) or piece_length <= 0:
        raise TorrentError(
            f"this .torrent's `piece length` is not a positive integer "
            f"({piece_length!r})"
        )
    pieces = info.get(b"pieces")
    if not isinstance(pieces, bytes) or len(pieces) % 20 != 0:
        raise TorrentError(
            "this .torrent's `pieces` is not a whole number of 20-byte SHA-1 "
            "hashes"
        )

    return Torrent(
        info_hash=info_hash,
        name=name,
        piece_length=piece_length,
        piece_count=len(pieces) // 20,
        entries=_entries(info, name),
        trackers=_announce_urls(document),
        web_seeds=_url_list(document),
        comment=_text(document.get(b"comment"), "comment")[:1000],
        raw=data,
    )


def _text(value, what: str) -> str:
    """A bencoded string as text, without ever raising on odd bytes.

    Torrent strings are *usually* UTF-8 and are not required to be. A file
    the operator can see on the source's own website must not become an
    exception here, so undecodable bytes are replaced -- and because the
    result may then differ from the real name, anything derived from it is
    put back through `bare_filename` before it can be a path.
    """
    if value is None:
        return ""
    if not isinstance(value, bytes):
        raise TorrentError(f"this .torrent's `{what}` is not a string")
    return value.decode("utf-8", errors="replace")


def _entries(info: dict, name: str) -> tuple[TorrentEntry, ...]:
    """The torrent's file list, single-file and multi-file forms alike."""
    files = info.get(b"files")
    if files is None:
        length = info.get(b"length")
        if not isinstance(length, int) or length < 0:
            raise TorrentError(
                "this .torrent declares neither `files` nor a valid `length`"
            )
        # A single-file torrent's `name` *is* the filename.
        return (_entry(name, length, info),)

    if not isinstance(files, list):
        raise TorrentError("this .torrent's `files` is not a list")
    if len(files) > MAX_TORRENT_ENTRIES:
        raise TorrentError(
            f"this .torrent declares {len(files)} files, over the "
            f"{MAX_TORRENT_ENTRIES} this host will read"
        )

    out: list[TorrentEntry] = []
    for index, raw in enumerate(files):
        if not isinstance(raw, dict):
            raise TorrentError(f"file {index} in this .torrent is not a dictionary")
        length = raw.get(b"length")
        if not isinstance(length, int) or length < 0:
            raise TorrentError(
                f"file {index} in this .torrent has no valid `length`"
            )
        parts = raw.get(b"path")
        if not isinstance(parts, list) or not parts:
            raise TorrentError(f"file {index} in this .torrent has no `path`")
        components = [_text(p, f"files[{index}].path") for p in parts]
        out.append(_entry("/".join(components), length, raw, nested=len(components) > 1))
    return tuple(out)


def _entry(path: str, length: int, raw: dict, *, nested: bool = False) -> TorrentEntry:
    """One entry, with the refusal worked out rather than the name fixed up.

    A path this host could not safely write is recorded as a refusal and
    kept in the listing. Sanitising it into something writable is the one
    thing not on the table: the operator would then get a file under a
    name the source never used, which is how the wrong ROM ends up in a
    library under the right title.
    """
    refusal = ""
    if nested:
        refusal = (
            "its path has more than one component, and this host writes only "
            "bare filenames (see TorrentSource.files)"
        )
    else:
        try:
            bare_filename(path)
        except ValueError as exc:
            refusal = f"its name is not one this host will write ({exc})"
    return TorrentEntry(
        path=path,
        length=length,
        sha1=_hex(raw.get(b"sha1"), 40),
        md5=_hex(raw.get(b"md5"), 32),
        refusal=refusal,
    )


def _hex(value, width: int) -> str:
    """A per-file digest, or "" for anything that is not one.

    Silent on a bad value rather than raising: these fields are an
    Archive.org extension, so a torrent from anywhere else simply has none
    and that is not an error. What matters is that a *malformed* one never
    reaches a comparison -- an empty string means "no digest", and
    `verify` then says the fetch was unverified instead of passing it.
    """
    if not isinstance(value, bytes):
        return ""
    try:
        text = value.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return ""
    if len(text) != width or any(c not in "0123456789abcdef" for c in text):
        return ""
    return text


def _announce_urls(document: dict) -> tuple[str, ...]:
    """`announce` plus every tier of `announce-list`, de-duplicated in order."""
    found: list[str] = []
    primary = document.get(b"announce")
    if isinstance(primary, bytes):
        found.append(_text(primary, "announce"))
    tiers = document.get(b"announce-list")
    if isinstance(tiers, list):
        for tier in tiers:
            if not isinstance(tier, list):
                continue
            for url in tier:
                if isinstance(url, bytes):
                    found.append(_text(url, "announce-list"))
    return _unique(found)


def _url_list(document: dict) -> tuple[str, ...]:
    """BEP 19 web seeds: `url-list`, as a string or a list of them."""
    value = document.get(b"url-list")
    if isinstance(value, bytes):
        return _unique([_text(value, "url-list")])
    if not isinstance(value, list):
        return ()
    return _unique(
        [_text(u, "url-list") for u in value if isinstance(u, bytes)]
    )


def _unique(urls: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return tuple(out[:MAX_ANNOUNCE_URLS])


# -- gating what came back -------------------------------------------------


@dataclass(frozen=True)
class UrlVerdict:
    """Which of a torrent's URLs the plugin's allowlist covers."""

    permitted: tuple[str, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refused

    def reasons(self) -> str:
        return "; ".join(f"{url} ({why})" for url, why in self.refused)


def check_trackers(trackers: tuple[str, ...], allowlist: list[str]) -> UrlVerdict:
    """Gate a torrent's trackers by **host**, not by `check_url`.

    This is the one place the usual gate cannot be used verbatim, and the
    reason is worth writing down rather than working around.

    `netpolicy.check_url` permits `https` and nothing else. That is right
    for everything it currently guards, all of which the host itself
    fetches. A tracker is not fetched by anything here -- it is announced
    to, by the operator's torrent client, over `udp` or plain `http`.
    Archive.org's own trackers are `http://bt1.archive.org:6969/announce`.
    Passing that to `check_url` would refuse every real torrent; widening
    `ALLOWED_SCHEMES` to make it pass would weaken the check for the four
    capabilities that legitimately depend on it being https-only.

    So the scheme is narrowed to a closed set of things that actually are
    trackers, and the **hostname** goes through `netpolicy.host_matches` --
    the same wildcard rule, on the same allowlist. What is enforced is the
    property `permissions.network` promises: every host this torrent will
    cause traffic to was declared in a manifest somebody could read before
    installing it.
    """
    permitted: list[str] = []
    refused: list[tuple[str, str]] = []
    for url in trackers:
        try:
            parts = urlsplit(url)
            scheme = parts.scheme.lower()
            host = parts.hostname
        except ValueError:
            refused.append((url, "not a URL"))
            continue
        if scheme not in TRACKER_SCHEMES:
            refused.append(
                (url, f"scheme {scheme!r} is not a tracker scheme "
                      f"({', '.join(sorted(TRACKER_SCHEMES))})")
            )
        elif not host:
            refused.append((url, "no host"))
        elif not any(host_matches(host, p) for p in allowlist):
            refused.append(
                (url, f"host {host!r} is not permitted by the plugin's "
                      f"network allowlist {allowlist!r}")
            )
        else:
            permitted.append(url)
    return UrlVerdict(tuple(permitted), tuple(refused))


def check_web_seeds(seeds: tuple[str, ...], allowlist: list[str]) -> UrlVerdict:
    """Gate a torrent's web seeds with `check_url` proper.

    Unlike a tracker, a web seed is a URL **this host will GET**. So it
    gets the ordinary gate with no adjustment at all: https only, and on a
    declared host. That refuses the two plain-http mirrors Archive.org
    lists beside its https one, which is the correct outcome and costs
    nothing -- the https entry is the one that works.
    """
    permitted: list[str] = []
    refused: list[tuple[str, str]] = []
    for url in seeds:
        try:
            check_url(url, list(allowlist))
        except PolicyViolation as exc:
            refused.append((url, str(exc)))
        else:
            permitted.append(url)
    return UrlVerdict(tuple(permitted), tuple(refused))


# -- magnets ---------------------------------------------------------------


@dataclass(frozen=True)
class MagnetLink:
    """A magnet URI that has been taken apart and checked."""

    info_hash: str
    display_name: str = ""
    trackers: tuple[str, ...] = ()
    web_seeds: tuple[str, ...] = ()


def check_magnet(uri: str, allowlist: list[str]) -> MagnetLink:
    """Validate a magnet, parameter by parameter, against the allowlist.

    A magnet is not http(s), so `netpolicy.check_url` cannot be applied to
    it as a whole, and "it is not a URL so there is nothing to check" is
    the wrong conclusion -- it is a bundle of things, some of which are
    network locations. Left ungated it would be the largest hole in this
    capability, so each part is treated on its own terms:

    * **`xt=urn:btih:<hash>` -- the content, and not a location.** An
      info-hash names bytes by their digest. It cannot be pointed at a
      host, it cannot be an SSRF target, and there is nothing for an
      allowlist to say about it. What *is* checked is that it is a
      well-formed v1 info-hash -- 40 hex, or the 32-character base32 form
      normalised to hex -- and that there is exactly one. Nothing about
      the allowlist; everything about not passing a malformed identifier
      to somebody else's program.

    * **`tr=` -- trackers.** Real network locations, contacted by whatever
      client this is handed to. Gated by `check_trackers` above: closed
      scheme set, hostname through the manifest allowlist.

    * **`ws=` -- web seeds.** Plain http(s) URLs that a client (or this
      host) will GET, so they get `check_url` unmodified: https, declared
      host.

    * **`dn`, `xl`, `kt`, `so` -- description.** A display name, a length,
      keywords, a file-index selection. None is a location. Length-bounded
      by the enclosing `TorrentSource.source` field and otherwise inert.

    * **everything else -- refused.** `xs` and `mt` are URLs in schemes
      this reasoning has not been done for; `x.pe` is a raw `IP:port` peer
      address with no hostname for an allowlist to match, which makes it
      precisely the parameter that would turn a magnet into an
      unrestricted outbound connection. Rather than enumerate the
      dangerous ones -- the list that cannot be finished -- the accepted
      keys are an allowlist, which is what `manifest.py` does with
      manifest keys and `netpolicy` does with hosts. This is the third
      instance of the same rule.

    Returns the parsed link; raises `TorrentError` on anything refused.
    """
    parts = urlsplit(uri)
    if parts.scheme.lower() != "magnet":
        raise TorrentError(f"not a magnet URI: {uri!r}")

    # A magnet's payload is in the *query* of an opaque URI, which urlsplit
    # leaves in `path` when there is no `//`. Take whichever carries it.
    query = parts.query or parts.path.lstrip("?")
    if query.startswith("?"):
        query = query[1:]
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        raise TorrentError(f"magnet URI carries no parameters: {uri!r}")

    unknown = sorted({k for k, _ in pairs} - MAGNET_PARAMS)
    if unknown:
        raise TorrentError(
            f"magnet URI has parameter(s) this host will not accept: "
            f"{unknown}. Permitted: {sorted(MAGNET_PARAMS)}. A parameter "
            f"that names a host or a peer is refused rather than passed on, "
            f"because the plugin's network allowlist could not be applied "
            f"to it."
        )

    exact = [v for k, v in pairs if k == "xt"]
    if len(exact) != 1:
        raise TorrentError(
            f"a magnet must carry exactly one `xt` info-hash, got {len(exact)}"
        )
    info_hash = _info_hash_from_xt(exact[0])

    trackers = tuple(v for k, v in pairs if k == "tr")[:MAX_ANNOUNCE_URLS]
    verdict = check_trackers(trackers, allowlist)
    if not verdict.ok:
        raise TorrentError(
            f"magnet refused: its tracker(s) are outside the plugin's network "
            f"allowlist -- {verdict.reasons()}"
        )

    seeds = tuple(v for k, v in pairs if k == "ws")[:MAX_ANNOUNCE_URLS]
    seed_verdict = check_web_seeds(seeds, allowlist)
    if not seed_verdict.ok:
        raise TorrentError(
            f"magnet refused: its web seed(s) are outside the plugin's "
            f"network allowlist -- {seed_verdict.reasons()}"
        )

    names = [v for k, v in pairs if k == "dn"]
    return MagnetLink(
        info_hash=info_hash,
        display_name=names[0][:500] if names else "",
        trackers=verdict.permitted,
        web_seeds=seed_verdict.permitted,
    )


def _info_hash_from_xt(value: str) -> str:
    """The v1 info-hash inside an `xt`, in hex, or a refusal."""
    prefix = "urn:btih:"
    if not value.lower().startswith(prefix):
        raise TorrentError(
            f"magnet `xt` must be {prefix}<info-hash>; this host reads v1 "
            f"(SHA-1) torrents only, so `urn:btmh:` (v2) is not accepted. "
            f"Got {value!r}"
        )
    body = value[len(prefix) :].strip()
    if len(body) == 40 and all(c in "0123456789abcdefABCDEF" for c in body):
        return body.lower()
    if len(body) == 32:
        # The base32 spelling is equally standard and equally common.
        try:
            return base64.b32decode(body.upper()).hex()
        except (ValueError, TypeError) as exc:
            raise TorrentError(
                f"magnet `xt` is 32 characters but not valid base32: {exc}"
            ) from exc
    raise TorrentError(
        f"magnet `xt` info-hash must be 40 hex or 32 base32 characters, got "
        f"{len(body)}"
    )


def magnet_for(
    torrent: Torrent, allowlist: list[str], *, include_web_seeds: bool = True
) -> str:
    """Build a magnet from a torrent the host itself read and verified.

    Deliberately built rather than accepted. The info-hash comes from
    `parse_torrent`, which took it from the bytes on the wire; the
    trackers and web seeds are only those the allowlist permitted. So the
    string handed to the operator's client cannot contain a location the
    plugin never declared, whatever the plugin returned.

    The result is put back through `check_magnet` before it is returned --
    a round trip that looks redundant and is the point: if this builder
    ever emits something the validator would refuse, that is a bug found
    here rather than a magnet in somebody's client.
    """
    parts = [f"magnet:?xt=urn:btih:{torrent.info_hash}"]
    if torrent.name:
        parts.append(f"dn={quote(torrent.name, safe='')}")
    for url in check_trackers(torrent.trackers, allowlist).permitted:
        parts.append(f"tr={quote(url, safe='')}")
    if include_web_seeds:
        for url in check_web_seeds(torrent.web_seeds, allowlist).permitted:
            parts.append(f"ws={quote(url, safe='')}")
    uri = "&".join(parts)
    check_magnet(uri, allowlist)
    return uri


# -- handing it to the client the operator runs ----------------------------


def handoff_path(torrent: Torrent, watch_dir: Path) -> Path:
    """Where a validated `.torrent` is written for the operator's client.

    The filename is the **host-computed info-hash**, not the torrent's
    `name` and not anything a plugin returned. Two reasons, and the second
    is the one that matters: an info-hash is 40 hex characters, so no
    plugin- or source-controlled string reaches the filesystem at all; and
    it is content-addressed, so handing over the same torrent twice
    overwrites rather than accumulating `x (2).torrent`.

    Still routed through `dest_in_job_dir` even though the name is
    generated here. That is the same reasoning `emuassets.install_asset`
    applies to a manifest-validated slug: a containment check that is
    skipped whenever the caller believes the name is safe is a check that
    stops running exactly when somebody's belief turns out to be wrong.
    """
    try:
        return dest_in_job_dir(Path(watch_dir), f"{torrent.info_hash}.torrent")
    except UnsafeDestination as exc:  # pragma: no cover - name is 40 hex
        raise TorrentError(str(exc)) from exc


def write_handoff(torrent: Torrent, watch_dir: Path) -> Path:
    """Drop the torrent into a watch directory, byte for byte as received.

    The bytes are written verbatim rather than re-encoded from the parsed
    form. A re-encoding is only guaranteed to preserve the info-hash for
    input that was already canonical, and the info-hash is the single
    property the receiving client depends on -- so the one representation
    that is certainly right is the one that arrived.
    """
    dest = handoff_path(torrent, watch_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(torrent.raw)
    return dest


# -- fetching one file from the torrent's own web seed ---------------------


@dataclass(frozen=True)
class FetchedFile:
    """One file pulled from a web seed and checked against the torrent."""

    entry: TorrentEntry
    path: Path
    url: str
    size_bytes: int
    digest: str = ""
    verified_by: str = ""
    note: str = ""

    @property
    def verified(self) -> bool:
        return bool(self.verified_by)


def web_seed_url(seed: str, torrent: Torrent, entry: TorrentEntry) -> str:
    """The BEP 19 URL for one entry under one web seed.

    BEP 19's rule for a multi-file torrent is that a `url-list` entry
    ending in `/` is a base, onto which `<name>/<path>` is appended.
    Archive.org's `https://archive.org/download/` plus `rubik_202308`
    plus `rubik.zip` is exactly the item's ordinary download URL, which is
    what makes this path unremarkable: the torrent is describing bytes the
    source already serves over HTTPS.

    For a single-file torrent the base is followed by `<name>` alone,
    because there `name` *is* the file.
    """
    base = seed if seed.endswith("/") else seed + "/"
    if len(torrent.entries) == 1 and torrent.entries[0].path == torrent.name:
        return base + quote(torrent.name, safe="")
    return (
        base
        + quote(torrent.name, safe="")
        + "/"
        + quote(entry.path, safe="")
    )


def fetch_entry(
    torrent: Torrent,
    entry: TorrentEntry,
    dest_dir: Path,
    allowlist: list[str],
    *,
    downloader=None,
) -> FetchedFile:
    """Download one entry from an allowlisted web seed and verify it.

    The web seed is chosen from the torrent's own `url-list`, after
    `check_web_seeds` has removed every entry the plugin's allowlist does
    not cover -- so a torrent that lists only undeclared or plain-http
    mirrors yields no seed and a refusal that says so, rather than a
    download from somewhere nobody declared.

    Verification prefers the torrent's per-file `sha1`, falls back to
    `md5`, and when the torrent carries neither says so in `note` and
    leaves `verified` false. It never reports a pass it did not perform:
    a `.torrent` from a source that does not write per-file digests is
    still useful as a manifest and as a handoff, and pretending otherwise
    would make the one guarantee this capability offers worthless.

    Piece hashes are deliberately not used as that fallback. For this
    corpus they cannot do the job: Archive.org's piece length is 512 KB
    and up while a ROM is kilobytes, so a single piece routinely spans an
    item's *entire* file list -- verifying one 15 KB ROM against piece 0
    would mean downloading the thumbnails, the screenshot and the metadata
    sqlite that share it. A check that costs more than the thing it checks
    is not a fallback, it is a different feature.
    """
    if not entry.selectable:
        raise TorrentError(
            f"{entry.path!r} cannot be fetched: {entry.refusal}"
        )

    verdict = check_web_seeds(torrent.web_seeds, allowlist)
    if not verdict.permitted:
        raise TorrentError(
            "this torrent names no web seed the plugin's network allowlist "
            "permits, so there is nothing to fetch it from over https"
            + (f" -- refused: {verdict.reasons()}" if verdict.refused else "")
            + ". Hand it to a torrent client instead (rom-hub torrent "
            "handoff)."
        )
    seed = verdict.permitted[0]
    url = web_seed_url(seed, torrent, entry)

    try:
        dest = dest_in_job_dir(Path(dest_dir), entry.path)
    except UnsafeDestination as exc:
        raise TorrentError(str(exc)) from exc
    dest.parent.mkdir(parents=True, exist_ok=True)

    owns = downloader is None
    if owns:
        # Imported here rather than at module scope: `parse_torrent`,
        # `check_magnet` and `write_handoff` are the common paths and none
        # of them needs an HTTP client, a job queue or a hasher.
        from rom_hub.importer import HttpDownloader

        # The torrent declares the length, so the budget is free and tight:
        # anything longer than the entry says is not the entry.
        downloader = HttpDownloader(allowlist=allowlist, max_bytes=entry.length)
    try:
        downloader.download(url, dest, expected_size=entry.length)
    except Exception as exc:  # noqa: BLE001 - reported, never propagated raw
        raise TorrentError(
            f"fetching {entry.path!r} from {url!r} failed: {exc}"
        ) from exc
    finally:
        if owns:
            downloader.close()

    return _verify(entry, dest, url)


def _verify(entry: TorrentEntry, dest: Path, url: str) -> FetchedFile:
    """Check what landed against what the torrent said it would be."""
    size = dest.stat().st_size
    if size != entry.length:
        raise TorrentError(
            f"{entry.path!r} arrived as {size} bytes but the torrent declares "
            f"{entry.length}; refusing to report a file the torrent does not "
            f"describe"
        )

    algorithm = entry.verified_by
    if not algorithm:
        return FetchedFile(
            entry=entry,
            path=dest,
            url=url,
            size_bytes=size,
            note=(
                "this torrent carries no per-file digest, so the bytes match "
                "the declared length and nothing more"
            ),
        )

    expected = entry.sha1 if algorithm == "sha1" else entry.md5
    digest = hashlib.new(algorithm)
    with dest.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    if got != expected:
        raise TorrentError(
            f"{entry.path!r} failed its {algorithm} check: the torrent says "
            f"{expected}, the bytes hash to {got}"
        )
    return FetchedFile(
        entry=entry,
        path=dest,
        url=url,
        size_bytes=size,
        digest=got,
        verified_by=algorithm,
    )


# -- what the CLI prints ---------------------------------------------------


@dataclass
class TorrentOutcome:
    """Everything a `rom-hub torrent` command learned, for one printer."""

    source: TorrentSource
    torrent: Torrent
    plugin: str = ""
    trackers: UrlVerdict = field(default_factory=UrlVerdict)
    seeds: UrlVerdict = field(default_factory=UrlVerdict)
    magnet: str = ""
    handed_to: Path | None = None
    fetched: list[FetchedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "kind": self.source.kind,
            "source": self.source.source,
            "info_hash": self.torrent.info_hash,
            "name": self.torrent.name,
            "total_bytes": self.torrent.total_bytes,
            "piece_length": self.torrent.piece_length,
            "piece_count": self.torrent.piece_count,
            "comment": self.torrent.comment,
            "files": [
                {
                    "path": e.path,
                    "length": e.length,
                    "sha1": e.sha1,
                    "md5": e.md5,
                    "selectable": e.selectable,
                    "refusal": e.refusal,
                }
                for e in self.torrent.entries
            ],
            "wanted": list(self.source.files),
            "trackers": {
                "permitted": list(self.trackers.permitted),
                "refused": [
                    {"url": u, "why": w} for u, w in self.trackers.refused
                ],
            },
            "web_seeds": {
                "permitted": list(self.seeds.permitted),
                "refused": [
                    {"url": u, "why": w} for u, w in self.seeds.refused
                ],
            },
            "magnet": self.magnet,
            "handed_to": str(self.handed_to) if self.handed_to else "",
            "fetched": [
                {
                    "path": str(f.path),
                    "file": f.entry.path,
                    "url": f.url,
                    "size_bytes": f.size_bytes,
                    "digest": f.digest,
                    "verified_by": f.verified_by,
                    "verified": f.verified,
                    "note": f.note,
                }
                for f in self.fetched
            ],
            "extra": dict(self.source.extra),
            "notes": list(self.notes),
        }
