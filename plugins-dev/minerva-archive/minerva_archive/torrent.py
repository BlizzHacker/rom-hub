"""MiNERVA `torrent` capability: resolve a search result to its magnet.

MiNERVA distributes everything as collection torrents, and its search API
hands back a bare magnet (`magnet:?xt=urn:btih:<hash>&dn=Minerva_Myrient`)
with **no trackers** -- so a client that grabs it as-is has only DHT to find
peers, which is slow to bootstrap and blocked outright on some networks.

This capability takes that magnet apart, keeps the info-hash (the torrent's
identity, and the only part that names bytes), drops any tracker the manifest
does not declare, and appends the well-known public trackers -- every one of
them a host listed in `manifest.toml`, because the Hub gates each `tr=` by
hostname against that list and silently drops the rest.

The plugin never opens a socket and never runs a torrent client. It returns a
`TorrentSource`; the host validates it (`check_magnet`) and hands it to the
operator's client. That division is the Hub's, not this plugin's to change.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit

from rom_hub_sdk import SearchResult, TorrentProvider, TorrentSource

#: Open public trackers, announced to by the operator's client and never by
#: this plugin or the Hub. Every host here is declared in manifest.toml.
#: Bounded well under the host's MAX_ANNOUNCE_URLS.
DEFAULT_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.publictracker.xyz:6969/announce",
    "udp://tracker.wildkat.net:6969/announce",
    "udp://tracker.qu.ax:6969/announce",
    "udp://tracker.peerfect.org:6969/announce",
    "udp://tracker.ilibr.org:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://tracker.0x7c0.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
)


class TorrentRefused(Exception):
    """This result carries no usable magnet, and the message says so."""


def _normalise_info_hash(value: str) -> str | None:
    """A v1 info-hash as 40 lowercase hex, or None if this is not one."""
    import base64
    import binascii
    import re

    v = (value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", v):
        return v.lower()
    upper = v.upper()
    if re.fullmatch(r"[A-Z2-7]{32}", upper):
        try:
            return base64.b32decode(upper).hex()
        except (binascii.Error, ValueError):
            return None
    return None


class Torrent(TorrentProvider):
    def resolve(self, result: SearchResult) -> TorrentSource:
        # source_id identifies MiNERVA's file row so candidates sharing one
        # collection torrent remain distinct. The magnet is carried in extra
        # and still names the whole collection.
        raw = str((result.extra or {}).get("torrent_magnet")
                  or result.source_id or "").strip()
        if urlsplit(raw).scheme.lower() != "magnet":
            raise TorrentRefused(
                "that search result carries no magnet; MiNERVA results always "
                "do, so this one came from somewhere else")

        split = urlsplit(raw)
        pairs = parse_qsl(split.query or split.path.lstrip("?"),
                          keep_blank_values=True)
        hashes, names = [], []
        for key, value in pairs:
            if key == "xt":
                urn = value.strip()
                if urn.lower().startswith("urn:btih:"):
                    got = _normalise_info_hash(urn[len("urn:btih:"):])
                    if got:
                        hashes.append(got)
                elif urn.lower().startswith("urn:btmh:"):
                    raise TorrentRefused(
                        "that magnet names a v2 info-hash; this host checks v1")
            elif key == "dn":
                names.append(value.strip())

        if not hashes:
            raise TorrentRefused(f"that magnet has no usable v1 info-hash: {raw!r}")
        if len(set(hashes)) > 1:
            raise TorrentRefused("that magnet names more than one info-hash")

        info_hash = hashes[0]
        # Prefer a real display name from the magnet; fall back to the result
        # title, but never to the magnet URI itself (nesting a URI in `dn=`
        # tells the operator nothing).
        display = (names[0] if names else None) or (result.title or None)
        if display and urlsplit(display).scheme.lower() in {"magnet", "http", "https"}:
            display = None

        magnet = self._magnet(info_hash, display, DEFAULT_TRACKERS)
        return TorrentSource(
            kind="magnet",
            source=magnet,
            name=display,
            info_hash=info_hash,
            extra={
                "origin": "minerva-archive",
                "trackers": str(len(DEFAULT_TRACKERS)),
            },
        )

    def _magnet(self, info_hash: str, display: str | None,
                trackers) -> str:
        """Rebuild a clean magnet: the info-hash, a name, declared trackers.

        Built by hand rather than with `urlencode`: a magnet's `xt` value is
        an opaque URN that every client publishes with literal colons
        (`urn:btih:<hash>`), and percent-encoding them -- valid, and the host
        parses either -- is not the shape the ecosystem recognises. Only the
        display name needs quoting.
        """
        from urllib.parse import quote
        parts = [f"xt=urn:btih:{info_hash}"]
        if display:
            parts.append(f"dn={quote(display, safe='')}")
        for tr in trackers:
            parts.append(f"tr={quote(tr, safe=':/')}")
        return "magnet:?" + "&".join(parts)
