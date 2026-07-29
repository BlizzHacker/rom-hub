"""itch.io `metadata`: the developer's own title and cover art.

    source_id -> the game page -> MetadataPatch(name, artwork_url)

This is the capability that makes the itch.io plugin worth installing.
Its `importer` refuses by design and always will (see `importer.py`), so
until now the plugin could find you a game and then do nothing with it.
It cannot fetch the *game*, but the two things it can read off a game
page -- what the developer called it and the cover they chose -- are
exactly what a library is missing for a title it already has.

The plugin never fetches the cover. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist. That is why `img.itch.zone` is in the manifest: an artwork URL
the host may not fetch is worse than no artwork, because it fails at
enrich time rather than here.

**A game id is required, and there is no name-based lookup.** That is not
an omission. itch.io's robots.txt `Disallow`s `/search`, and the browse
listings this plugin is allowed to read are a popularity-ordered slice of
a catalogue with hundreds of thousands of titles -- looking for one
specific game in them would find the wrong one far more often than the
right one, and attaching another developer's cover to somebody's rom is
the failure this codebase refuses everywhere else. `rom-hub search itch-io
"<name>"` produces the id; `--source-id` passes it in. RetroAchievements
takes the same position for the same reason.

**Two fields, and only what actually resolved.** `name` comes from the
page's `Product` JSON-LD, falling back to the `<h1 class="game_title">`;
`artwork_url` comes from `og:image`. Either may be absent on a real page,
and an absent one is left out of the patch rather than filled in, because
`MetadataPatch` reads absent as "leave the library alone". A page with
neither is a refusal, not an empty patch that reports a successful enrich
which changed nothing.
"""

import html
import json
import posixpath
import re
from urllib.parse import unquote, urlsplit

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .filenames import safe_filename

# https://<developer>.itch.io/<game>, as `search` reports it in source_id.
_SOURCE_ID = re.compile(
    r"\A(?P<developer>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<game>[A-Za-z0-9][A-Za-z0-9._-]*)\Z"
)
# The same thing written as a URL, which is what somebody pasting from a
# browser will have. Accepted because refusing it would be pedantry.
_GAME_URL = re.compile(
    r"\Ahttps?://(?P<developer>[A-Za-z0-9][A-Za-z0-9-]*)\.itch\.io/"
    r"(?P<game>[A-Za-z0-9][A-Za-z0-9._-]*)/?\Z"
)

# Where a game id may arrive. `source_id` is what --source-id fills in.
ID_KEYS = ("itch_id", "itch_game", "source_id")

_LD_JSON = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)
# Attribute order is not stable on itch.io -- the same meta tag is emitted
# `content=... property=...` on one page and the other way round on the
# next -- so both orders are matched rather than one being assumed. The
# same lesson browse.py learned from the listing markup.
_OG_IMAGE = re.compile(
    r'<meta[^>]*\bcontent="([^"]*)"[^>]*\bproperty="og:image"'
    r'|<meta[^>]*\bproperty="og:image"[^>]*\bcontent="([^"]*)"'
)
_H1_TITLE = re.compile(
    r'<h1[^>]*\bclass="[^"]*\bgame_title\b[^"]*"[^>]*>(.*?)</h1>', re.DOTALL
)
_TAGS = re.compile(r"<[^>]+>")

# itch.io serves every uploaded image from this one host. Declared in the
# manifest, and checked here too: a page that named a cover somewhere else
# would fail the broker's allowlist at enrich time with a policy error,
# which reads like a bug in the Hub rather than an oddity on the page.
IMAGE_HOST = "img.itch.zone"

DEFAULT_ARTWORK_FILENAME = "cover.png"
# Long enough for any real title, and the same ceiling MetadataPatch puts
# on `name`, so a page cannot produce a patch the host then rejects.
MAX_NAME_CHARS = 500


class NotIdentified(Exception):
    """No itch.io game id was supplied, and one cannot be guessed."""


class PageUnusable(Exception):
    """The game page could not be read."""


class NothingToPropose(Exception):
    """The page carries neither a usable title nor a cover."""


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", fragment or "")).split())


def product_name(page: str) -> str:
    """The title from the page's `Product` JSON-LD, or "".

    Parsed as JSON rather than pattern-matched out of the script body:
    itch.io emits the object's keys in different orders on different pages
    (`aggregateRating` first on one, `name` first on the next), so a regex
    for `"name":"..."` works on whichever page it was written against and
    silently stops working on the others.

    A page carries several JSON-LD blocks -- a BreadcrumbList as well --
    so the type is checked rather than the first block being taken.
    """
    for match in _LD_JSON.finditer(page or ""):
        try:
            payload = json.loads(match.group(1))
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("@type", "")).lower() != "product":
            continue
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            return " ".join(name.split())
    return ""


def heading_title(page: str) -> str:
    """The `<h1 class="game_title">`, or "". The fallback for the above."""
    match = _H1_TITLE.search(page or "")
    return _text(match.group(1)) if match else ""


def cover_url(page: str) -> str:
    """The `og:image`, if it is on itch.io's image host.

    A cover anywhere else is dropped rather than proposed. The host checks
    every plugin-supplied URL against this plugin's allowlist before
    fetching, so an off-host URL would fail the enrich with a policy
    violation -- which reads as a Hub fault rather than as this page being
    unusual. Dropping it means the patch carries a name and no cover,
    which is a true and useful answer.
    """
    match = _OG_IMAGE.search(page or "")
    if not match:
        return ""
    raw = next((group for group in match.groups() if group), "")
    url = html.unescape(raw).strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != IMAGE_HOST:
        return ""
    return url


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        developer, game = self._identify(rom)
        page_url = f"https://{developer}.itch.io/{game}"
        page = self._page(page_url, f"{developer}/{game}")

        patch: dict = {}

        name = product_name(page) or heading_title(page)
        if name:
            patch["name"] = name[:MAX_NAME_CHARS]

        cover = cover_url(page)
        if cover:
            patch["artwork_url"] = cover
            patch["artwork_filename"] = self._artwork_filename(cover)

        if not patch:
            raise NothingToPropose(
                f"itch.io game page {page_url} carries neither a title this "
                f"plugin can read (no Product JSON-LD, no game_title heading) "
                f"nor a cover on {IMAGE_HOST}. Nothing was proposed, and the "
                f"library was left alone."
            )
        return MetadataPatch(**patch)

    # -- identification --------------------------------------------------

    @staticmethod
    def _identify(rom: RomRef) -> tuple[str, str]:
        """The developer and game this rom is, or a refusal saying how to
        supply one. Never inferred from the rom's name -- see the module
        docstring."""
        for key in ID_KEYS:
            raw = (rom.extra.get(key) or "").strip()
            if not raw:
                continue
            match = _SOURCE_ID.match(raw) or _GAME_URL.match(raw)
            if match:
                return match.group("developer"), match.group("game")
            raise NotIdentified(
                f"{raw!r} is not an itch.io game id: expected "
                f"'<developer>/<game>', as in "
                f"'csbrannan/disco-elysium-game-boy-edition', or the game's "
                f"page URL."
            )
        raise NotIdentified(
            f"rom {rom.rom_id} ({rom.name or rom.filename!r}) needs an itch.io "
            f"game id and none was given. There is no lookup by name here on "
            f"purpose: itch.io's robots.txt disallows /search, and the browse "
            f"listings this plugin may read are a small popularity-ordered "
            f"slice of the catalogue, so searching them for one title would "
            f"attach the wrong developer's cover more often than the right "
            f"one. Run `rom-hub search itch-io \"{rom.name or 'the title'}\"` "
            f"and pass the id it prints with --source-id."
        )

    @staticmethod
    def _artwork_filename(url: str) -> str:
        """A bare filename for the cover, derived from its URL.

        itch.io's image URLs are percent-encoded and end in a real
        extension (`/original/Z66%2BLw.png`). RomM routes on that
        extension, so it is preserved; everything else goes through the
        same sanitiser the importer uses.
        """
        name = posixpath.basename(unquote(urlsplit(url).path))
        return safe_filename(name, fallback=DEFAULT_ARTWORK_FILENAME)

    # -- the network -----------------------------------------------------

    def _page(self, url: str, source_id: str) -> str:
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise PageUnusable(
                f"itch.io returned HTTP {response.status_code} for {url!r} "
                f"(game {source_id!r})"
            )
        return response.text
