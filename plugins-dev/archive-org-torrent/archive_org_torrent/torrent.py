"""Archive.org's `torrent`: point the host at the item's own `.torrent`.

The Internet Archive publishes a BitTorrent file for very nearly every
item it holds and seeds it itself. That is the whole reason this plugin
exists: it is a source that *wants* to be reached this way, at a scale
nothing else here comes close to. Measured against the live service on
2026-08-01, by asking `advancedsearch.php` for `format:"Archive
BitTorrent"` within each collection:

    collection                       items   with a torrent
    softwarelibrary                250,398          231,663   92.5%
    consolelivingroom               24,746           21,956   88.7%
      of which downloadable         17,930           17,908   99.9%
      of which stream_only           6,816            4,048   59.4%
    softwarelibrary_msdos_games      8,899            5,922   66.5%

The line that matters is the third: of the Console Living Room items an
operator can actually have, all but 22 publish a torrent.

## What this plugin does, and what it refuses

One request, to `/metadata/<identifier>`, and everything is decided from
the reply:

* **A torrent is listed** -- the item has a file whose `format` is
  `Archive BitTorrent`. Return its `/download/` URL, the `btih` the
  metadata publishes beside it, and which files inside are the payload.
* **No such file** -- the item exists and has no torrent. Refuse saying
  exactly that. `msdos_Oregon_Trail_The_1990` is a live example: it is
  `stream_only` and `access-restricted-item`, and no torrent is
  generated for it.
* **`is_dark`** -- the Archive has taken the item down. Its metadata
  comes back as a stub with no `files` at all, and its `/download/` path
  answers **403**. `nointro.gb` is the live example. Refuse on the stub,
  so the host never makes the request that would 403.

None of the three is a bug and the messages say which is which, because
"this item has no torrent" and "something broke" send an operator to
completely different places.

## Why this is a separate plugin from `archive-org`

Not because the code could not live there -- it reads the same endpoint
the importer does, and `_payload` below is deliberately the same idea as
that plugin's, keyed off the Archive's own `emulator_ext`.

Because what a reader is being asked to trust is different. `archive-org`
imports a file over https from one origin. This one produces something an
operator may hand to a **torrent client**, which then announces to
trackers and connects to peers -- a different network posture, with a
different set of hosts in it, and one an operator should be able to
decline by simply not installing this plugin. Folding it into
`archive-org` would mean everybody who wanted a ROM download also got a
manifest declaring `bt1.archive.org`. Two plugins, two decisions.

## The payload, and where the host will not follow

`metadata.emulator_ext` is Archive.org's own statement of which extension
holds the game, and it is what the `archive-org` importer keys off. This
plugin uses it the same way, then falls back to "original files that are
not the Archive's own bookkeeping" for items that declare none.

What it cannot do is name a file in a subdirectory. `pac-man-championship
-edition-1` keeps its ROM at `NES/PAC-MAN Championship Edition.nes`, and
the host writes bare filenames only, so that entry is listed as
unselectable rather than flattened to its last component -- see
`TorrentSource.files`. Such an item is still perfectly good as a handoff:
a torrent client makes the directories itself. This plugin therefore does
not refuse the item; it names what it can and lets the host say the rest.
"""

import json
import posixpath
from urllib.parse import quote

from rom_hub_sdk import SearchResult, TorrentProvider, TorrentSource

METADATA = "https://archive.org/metadata/"
DOWNLOAD = "https://archive.org/download/"

#: The `format` the Archive gives its own generated torrent file.
TORRENT_FORMAT = "Archive BitTorrent"

#: Formats that are the Archive's bookkeeping rather than the item. Used
#: only for the fallback below; when `emulator_ext` is present it decides
#: on its own and none of this is consulted.
BOOKKEEPING_FORMATS = frozenset(
    {
        "Metadata",
        "Archive BitTorrent",
        "Item Tile",
        "JPEG Thumb",
        "Item Image",
        "Emulator Screenshot",
        "Animated GIF",
        "Thumbnail",
    }
)


class TorrentRefused(Exception):
    """This item has no torrent to offer, and the message says why."""


def _as_list(value) -> list[str]:
    """Archive.org returns `collection` as a list, or as a bare string when
    an item is in exactly one collection."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


class Torrent(TorrentProvider):
    def resolve(self, result: SearchResult) -> TorrentSource:
        identifier = (result.source_id or "").strip()
        if not identifier:
            raise TorrentRefused(
                "the search result carries no Archive.org identifier"
            )

        item = self._metadata(identifier)

        # A darkened item answers /metadata/ with a stub: `is_dark: true`,
        # no `metadata` and no `files`. Checked first, because its
        # /download/ path answers 403 and there is no reason to make that
        # request when the metadata already said so.
        if item.get("is_dark"):
            raise TorrentRefused(
                f"Archive.org has darkened item {identifier!r}: its metadata "
                f"carries is_dark, it lists no files, and its download path "
                f"answers HTTP 403. There is no torrent to reach"
            )

        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            raise TorrentRefused(
                f"Archive.org has no item {identifier!r} (its metadata "
                f"endpoint returned nothing)"
            )

        files = item.get("files")
        files = files if isinstance(files, list) else []
        entry = self._torrent_entry(identifier, metadata, files)

        wanted: list[str] = []
        if self.ctx.config.get("payload_only", True):
            wanted = self._payload(metadata, files)

        extra = {"identifier": identifier}
        emulator = metadata.get("emulator")
        if isinstance(emulator, str) and emulator.strip():
            extra["emulator"] = emulator.strip()
        collections = _as_list(metadata.get("collection"))
        if collections:
            # An operator seeing `stream_only` here has the explanation for
            # why the same item refuses `rom-hub import`.
            extra["stream_only"] = "true" if "stream_only" in collections else "false"
        size = metadata.get("item_size") or item.get("item_size")
        if isinstance(size, (int, str)) and str(size).isdigit():
            extra["item_size"] = str(size)

        title = metadata.get("title")
        return TorrentSource(
            kind="torrent_url",
            source=DOWNLOAD
            + quote(identifier, safe="")
            + "/"
            + quote(entry["name"], safe=""),
            name=title if isinstance(title, str) and title.strip() else identifier,
            files=wanted,
            # Archive.org publishes the info-hash beside the torrent, so
            # this is knowable without fetching anything. The host computes
            # its own from the bytes and refuses if the two disagree --
            # which is the only reason it is worth sending.
            info_hash=self._btih(entry),
            extra=extra,
        )

    # -- the torrent file itself ------------------------------------------

    def _torrent_entry(self, identifier: str, metadata: dict, files: list) -> dict:
        for entry in files:
            if not isinstance(entry, dict):
                continue
            if entry.get("format") != TORRENT_FORMAT:
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                return entry

        # Say *why* there is none where the metadata makes it knowable.
        # "no torrent" and "no torrent because the Archive will not let you
        # download this item" are different facts to an operator.
        collections = _as_list(metadata.get("collection"))
        why = ""
        if "stream_only" in collections:
            why = (
                " -- it is in the Archive's `stream_only` collection, which "
                "is played in a browser and not distributed"
            )
        elif str(metadata.get("access-restricted-item", "")).lower() == "true":
            why = " -- it is marked access-restricted-item"
        raise TorrentRefused(
            f"Archive.org item {identifier!r} publishes no "
            f"{TORRENT_FORMAT!r} file, so there is no torrent for it{why}"
        )

    def _btih(self, entry: dict) -> str | None:
        """The info-hash Archive.org publishes for its own torrent.

        Returned as a *claim*. Nothing here can establish it -- the plugin
        has not seen the torrent's bytes and could not fetch them if it
        wanted to. Malformed values are dropped rather than passed on, so
        a garbled `btih` costs a cross-check rather than the whole resolve.
        """
        value = entry.get("btih")
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            return None
        return value

    # -- which files inside it are the game -------------------------------

    def _payload(self, metadata: dict, files: list) -> list[str]:
        """Bare filenames inside the torrent worth having on their own.

        Keyed off `emulator_ext` -- Archive.org's own statement of which
        extension is the ROM -- exactly as the `archive-org` importer is.
        The fallback is for the software items that are not emulated and
        declare none.

        Only bare names are returned. A file in a subdirectory is skipped
        here rather than reported as unfetchable, because the host already
        lists every entry with its own reason and a plugin repeating that
        badly would be a second answer to the same question.
        """
        originals = self._originals(files)

        extension = metadata.get("emulator_ext")
        if isinstance(extension, str) and extension.strip():
            suffix = "." + extension.strip().lstrip(".").lower()
            named = [
                entry["name"]
                for entry in originals
                # A bare ".zip" has no basename to write to disk.
                if entry["name"].lower().endswith(suffix)
                and entry["name"].lower() != suffix
            ]
            if named:
                return named

        return [
            entry["name"]
            for entry in originals
            if entry.get("format") not in BOOKKEEPING_FORMATS
        ]

    def _originals(self, files: list) -> list[dict]:
        """The item's own files: not derivatives, not the Archive's index.

        `source == "original"` alone is not enough -- `_meta.xml`,
        `_files.xml` and `_meta.sqlite` are all marked `original` and are
        all bookkeeping -- which is why the format check above exists too.
        """
        return [
            entry
            for entry in files
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry.get("source") == "original"
            and self._is_bare(entry["name"])
        ]

    @staticmethod
    def _is_bare(name: str) -> bool:
        """True for a torrent entry the host is able to write.

        The host's rule, restated on this side only so that a name it
        would refuse is never *offered*. It is not the enforcement -- that
        is `TorrentSource.files`' validator and then the host's own
        containment check, both of which run on everything this returns.
        """
        return bool(name) and posixpath.basename(name) == name and name not in (
            ".",
            "..",
        )

    # -- the one request --------------------------------------------------

    def _metadata(self, identifier: str) -> dict:
        url = METADATA + quote(identifier, safe="")
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise TorrentRefused(
                f"Archive.org returned HTTP {response.status_code} for the "
                f"metadata of {identifier!r}"
            )
        try:
            item = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            # Rate limiting and maintenance pages both arrive as 200 + HTML.
            raise TorrentRefused(
                f"Archive.org's metadata for {identifier!r} was not JSON: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise TorrentRefused(
                f"Archive.org's metadata for {identifier!r} was not an object"
            )
        return item
