"""FitGirl & DODI Repacks `torrent` capability: resolve a search result to a magnet."""

from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import parse_qsl, quote, urlsplit

from rom_hub_sdk import SearchResult, TorrentProvider, TorrentSource

#: Open public trackers declared in manifest.toml.
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
    """Raised when a repack search result carries no valid v1 magnet."""


def _normalise_info_hash(value: str) -> str | None:
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
    def __init__(self, ctx=None):
        super().__init__(ctx)

    def resolve(self, result: SearchResult) -> TorrentSource:
        raw = str(
            (result.extra or {}).get("torrent_magnet")
            or result.source_id
            or ""
        ).strip()
        if urlsplit(raw).scheme.lower() != "magnet":
            raise TorrentRefused(
                "that repack result carries no inline magnet; open its release "
                "page or mirror links in the browser"
            )

        split = urlsplit(raw)
        pairs = parse_qsl(
            split.query or split.path.lstrip("?"),
            keep_blank_values=True,
        )
        hashes: list[str] = []
        names: list[str] = []
        for key, value in pairs:
            if key == "xt":
                urn = value.strip()
                if urn.lower().startswith("urn:btih:"):
                    got = _normalise_info_hash(urn[len("urn:btih:"):])
                    if got:
                        hashes.append(got)
                elif urn.lower().startswith("urn:btmh:"):
                    raise TorrentRefused(
                        "that magnet names a v2 info-hash; this host checks v1"
                    )
            elif key == "dn":
                names.append(value.strip())

        if not hashes:
            raise TorrentRefused(f"that magnet has no usable v1 info-hash: {raw!r}")
        if len(set(hashes)) > 1:
            raise TorrentRefused("that magnet names more than one info-hash")

        info_hash = hashes[0]
        display = (names[0] if names else None) or (result.title or None)
        if display and urlsplit(display).scheme.lower() in {"magnet", "http", "https"}:
            display = None

        magnet = self._magnet(info_hash, display, DEFAULT_TRACKERS)
        repacker = str((result.extra or {}).get("repacker") or "repacks")
        return TorrentSource(
            kind="magnet",
            source=magnet,
            name=display,
            info_hash=info_hash,
            extra={
                "origin": repacker.lower(),
                "trackers": str(len(DEFAULT_TRACKERS)),
            },
        )

    @staticmethod
    def _magnet(info_hash: str, display: str | None, trackers) -> str:
        parts = [f"xt=urn:btih:{info_hash}"]
        if display:
            parts.append(f"dn={quote(display, safe='')}")
        for tr in trackers:
            parts.append(f"tr={quote(tr, safe=':/')}")
        return "magnet:?" + "&".join(parts)
